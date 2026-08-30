"""The enrichment tables, and everything that reads or writes them.

These tables live in the same DuckDB file as the trace store but outside the pipeline's
per-session replace, so a re-extraction never touches them. They attach to the pipeline's
natural keys, which come from the data and survive re-extraction with it.

Open a store, ask it for the items of a level, and it hands back rows to render. Ask it what
is stale and it compares each item's `Stamp` against the one on disk.
"""

import datetime as dt
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from hyphae.enrich.prompts import (
    AgentRunItem,
    ApiCallRow,
    Item,
    Level,
    RunSection,
    SessionChild,
    SessionItem,
    ToolCallRow,
    TurnItem,
)
from hyphae.enrich.validation import Enrichment
from hyphae.export.duckdb import open_trace_store
from hyphae.export.schema import check_shape
from hyphae.model import MAIN_SOURCE
from hyphae.sessions import project_predicate

# Every enrichment table holds the same columns; only the primary key differs.
_ENRICHMENT_COLUMNS = """
  description VARCHAR NOT NULL,
  category VARCHAR NOT NULL,
  outcome VARCHAR NOT NULL,
  -- One line of visible struggle, NULL when the records showed none.
  friction VARCHAR,
  -- The four fields that decide staleness: sha256 of the rendered prompt content, the
  -- level's prompt version, the taxonomy version, and the model that answered.
  input_hash VARCHAR NOT NULL,
  prompt_version INTEGER NOT NULL,
  taxonomy_version INTEGER NOT NULL,
  model VARCHAR NOT NULL,
  enriched_at TIMESTAMPTZ NOT NULL,
"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS turn_enrichments (
  session_id VARCHAR NOT NULL, source VARCHAR NOT NULL, turn_id VARCHAR NOT NULL,
  {_ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id, source, turn_id)
);
CREATE TABLE IF NOT EXISTS agent_run_enrichments (
  session_id VARCHAR NOT NULL, agent_run_id VARCHAR NOT NULL,
  {_ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id, agent_run_id)
);
CREATE TABLE IF NOT EXISTS session_enrichments (
  session_id VARCHAR NOT NULL,
  {_ENRICHMENT_COLUMNS}
  PRIMARY KEY (session_id)
);
-- The sessions enrichment describes, named once so the reader and the sweep cannot drift
-- apart: a session with no main turn and no agent run has nothing to describe, and one whose
-- turns drove no api call has no model response to describe. 45 recorded sessions are in the
-- second state — `/model` and `/effort` turns the CLI answered by itself — and the QC pass
-- found the model inventing work for them rather than reporting none.
CREATE OR REPLACE VIEW describable_sessions AS
SELECT * FROM session_rollups WHERE (turns > 0 OR agent_runs > 0) AND api_calls > 0;
-- LEFT join, so an un-enriched turn still appears and coverage reads honestly. The
-- enrichment's own model is renamed: `agent_runs` carries a `model` of its own, and the
-- three views answer the same question the same way.
CREATE OR REPLACE VIEW enriched_turns AS
SELECT t.*, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM live_turns t
LEFT JOIN turn_enrichments e
  ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id;
-- `agent_runs` carries a `model` of its own — the model that ran it — so it keeps its
-- meaning under a name that says whose it is, and `description` means the enrichment's in
-- all three views. The run's own brief needs no such rename: it is `brief`.
CREATE OR REPLACE VIEW enriched_agent_runs AS
SELECT r.* EXCLUDE (model),
       r.model AS agent_model, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM live_agent_runs r
LEFT JOIN agent_run_enrichments e
  ON e.session_id = r.session_id AND e.agent_run_id = r.id;
CREATE OR REPLACE VIEW enriched_sessions AS
SELECT r.*, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM session_rollups r
LEFT JOIN session_enrichments e ON e.session_id = r.session_id;
"""


@dataclass(frozen=True)
class LevelSpec:
    """Where one level's rows live, and what makes one of them an orphan."""

    table: str
    # The enrichment table's primary key columns, in order.
    keys: tuple[str, ...]
    # The view holding the rows enrichment describes, and the columns matching `keys`.
    base: str
    base_keys: tuple[str, ...]


# Closed set: a level added here without a table above cannot be written, and a table added
# without a level here would never be swept.
LEVELS: dict[Level, LevelSpec] = {
    Level.turn: LevelSpec(
        table="turn_enrichments",
        keys=("session_id", "source", "turn_id"),
        # `live_turns`, not `turns`: a fork's replay of another transcript's turn is a copy,
        # and the turn it copied is enriched under the transcript that ran it.
        base="live_turns",
        base_keys=("session_id", "source", "id"),
    ),
    Level.agent_run: LevelSpec(
        table="agent_run_enrichments",
        keys=("session_id", "agent_run_id"),
        base="live_agent_runs",
        base_keys=("session_id", "id"),
    ),
    Level.session: LevelSpec(
        table="session_enrichments",
        keys=("session_id",),
        # `describable_sessions`, not `sessions`: a row for a session the pass will never
        # refresh again is a zombie by the same definition as one whose session is gone, and
        # 45 such rows are already on disk from before the gate existed.
        base="describable_sessions",
        base_keys=("session_id",),
    ),
}

_PAYLOAD_COLUMNS = (
    "description",
    "category",
    "outcome",
    "friction",
    "input_hash",
    "prompt_version",
    "taxonomy_version",
    "model",
    "enriched_at",
)


def _source_clause(alias: str, *, main: bool) -> str:
    """The main transcript's rows, or every agent run's — the two families a `source` has."""
    return f"{alias}.source {'=' if main else '<>'} '{MAIN_SOURCE}'"


# The tag Claude Code wraps a slash command's own output in, and the pattern that reads a
# body out of it. `(?s)` is load-bearing: without it a multi-line body matches nothing and
# extracts as the empty string, which is a state of its own.
_STDOUT_TAG = "local-command-stdout"
_STDOUT_BODY = f"(?s)<{_STDOUT_TAG}>(.*)</{_STDOUT_TAG}>"


def _project_clause(project: str | None) -> str:
    """Narrows a query already joined to `sessions s` to one analyzed repository."""
    return f" AND {project_predicate('s.project_dir')}" if project is not None else ""


def _project_parameters(project: str | None) -> list[str]:
    """What `_project_clause` binds: the path twice, once per placeholder in the predicate."""
    return [project, project] if project is not None else []


@dataclass(frozen=True)
class RunLink:
    """One agent run against whatever spawned it, as the records name it."""

    session_id: str
    run_id: str
    # The run whose transcript holds the spawning call, named either way the records name it.
    parent_run: str | None
    # The main turn holding the spawning call, when no run does. None alongside `parent_run`
    # means nothing in the session embeds this run, and the session carries it directly.
    parent_turn: str | None


@dataclass(frozen=True)
class Stamp:
    """What a row was written under. A row is current when its stamp equals today's."""

    # sha256 of the rendered prompt content — not of the instructions, which
    # `prompt_version` covers.
    input_hash: str
    prompt_version: int
    taxonomy_version: int
    model: str


class EnrichmentStore:
    """Reads enrichable items out of a trace store and writes enrichments back to it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Enrichment reads the pipeline's views by name and column, so a store another schema
        # wrote is not one this code can enrich, and it opens on the same terms as every
        # other reader: nothing is created at a path that holds no store. The store outlives
        # any one `with` block of the opener's, so it holds the block open on a stack and
        # closes it in `close()`.
        self._open = ExitStack()
        self.connection = self._open.enter_context(open_trace_store(path, read_only=False))
        try:
            # Before the DDL: an enrichment table that drifted from it would otherwise be
            # left alone by `CREATE TABLE IF NOT EXISTS` and fail at the first read below.
            check_shape(self.connection, _SCHEMA)
            self.connection.execute(_SCHEMA)
        except Exception:
            # Nothing was handed out, so no `with` block will close it, and the write lock
            # would outlive the refusal.
            self._open.close()
            raise

    def __enter__(self) -> "EnrichmentStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._open.close()

    def turn_items(self, project: str | None = None) -> list[TurnItem]:
        """Every enrichable main turn, each carrying the api and tool calls it drove.

        `project` filters by the analyzed repository's resolved path, taking its worktrees
        with it (`sessions.project_predicate`); None takes every session in the store.
        """
        turns = self.connection.execute(
            f"""SELECT t.session_id, t.source, t.id, t."index", t.prompt,
                       t.command_name, t.command_args
                FROM live_turns t JOIN sessions s ON s.id = t.session_id
                WHERE {_source_clause("t", main=True)}{_project_clause(project)}
                ORDER BY t.session_id, t."index" """,
            _project_parameters(project),
        ).fetchall()
        calls = self._api_calls(main=True, project=project)
        results = self._command_results(project=project)
        by_turn: dict[tuple[str, str, str], list[ApiCallRow]] = {}
        for (session_id, source), sequence in calls.items():
            for turn_id, row in sequence:
                if turn_id is not None:
                    by_turn.setdefault((session_id, source, turn_id), []).append(row)
        return [
            TurnItem(
                session_id=session_id,
                source=source,
                turn_id=turn_id,
                index=index,
                prompt=prompt,
                command_name=command_name,
                command_args=command_args,
                command_result=results.get((session_id, source, turn_id)),
                api_calls=tuple(by_turn.get((session_id, source, turn_id), ())),
            )
            for session_id, source, turn_id, index, prompt, command_name, command_args in turns
        ]

    def _command_results(self, *, project: str | None) -> dict[tuple[str, str, str], str]:
        """What the CLI printed for each command turn, keyed by session, source and turn.

        A turn absent from the mapping had no such record archived, which is a different
        state from one whose record printed nothing — `render_turn` says which. A record
        this build cannot classify raises rather than reading as either.
        """
        results: dict[tuple[str, str, str], str] = {}
        for session_id, source, turn_id, line_no, body, readable in self.connection.execute(
            f"""WITH carriers AS (
                    SELECT r.session_id, r.source, t.id AS turn_id, r.line_no,
                           -- The two recorded carriers: a `user` record holds the output in
                           -- its message, a `system`/`local_command` one at the top level.
                           -- Both are plain strings in every recorded case. A list-shaped
                           -- `message.content` would extract as the serialised array, so a
                           -- tag quoted inside it would match and pass the guard below.
                           coalesce(json_extract_string(r.raw, '$.message.content'),
                                    json_extract_string(r.raw, '$.content')) AS carrier
                    FROM raw_records r
                    JOIN live_turns t
                      ON t.session_id = r.session_id AND t.source = r.source
                     AND t.id = json_extract_string(r.raw, '$.parentUuid')
                    JOIN sessions s ON s.id = r.session_id
                    WHERE r.raw LIKE '%<{_STDOUT_TAG}>%'
                      AND t.command_name IS NOT NULL
                      AND {_source_clause("t", main=True)}{_project_clause(project)}
                )
                SELECT session_id, source, turn_id, line_no,
                       regexp_extract(carrier, ?, 1) AS body,
                       -- Tells "no match" from "matched nothing": without it an unreadable
                       -- record extracts as '', which is the printed-nothing state.
                       coalesce(regexp_matches(carrier, ?), false) AS readable
                FROM carriers
                ORDER BY session_id, source, turn_id, line_no""",
            [*_project_parameters(project), _STDOUT_BODY, _STDOUT_BODY],
        ).fetchall():
            if not readable:
                raise ValueError(
                    f"session {session_id} source {source} line {line_no} archives a command "
                    f"result in a shape this build cannot read: no <{_STDOUT_TAG}> in either "
                    "carrier field. Claude Code changed the record shape — record it and "
                    "teach the reader before enriching again."
                )
            key = (session_id, source, turn_id)
            # Ordered by line, so a turn answered over several records reads in sequence.
            results[key] = f"{results[key]}\n{body}" if key in results else body
        return results

    def run_items(self, project: str | None = None) -> list[AgentRunItem]:
        """Every agent run, each as the sequence of instructions and work its transcript holds.

        A run's api calls that belong to no turn of its own come first, as one continuation
        section: they are a fork's work on a conversation another transcript opened, and the
        turn its records replay is that other transcript's, not this run's.
        """
        runs = self.connection.execute(
            f"""SELECT r.session_id, r.id, r.agent_type
                FROM live_agent_runs r JOIN sessions s ON s.id = r.session_id
                WHERE true{_project_clause(project)} ORDER BY r.session_id, r.id""",
            _project_parameters(project),
        ).fetchall()
        turns: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for session_id, source, turn_id, prompt in self.connection.execute(
            f"""SELECT t.session_id, t.source, t.id, t.prompt
                FROM live_turns t JOIN sessions s ON s.id = t.session_id
                WHERE {_source_clause("t", main=False)}{_project_clause(project)}
                ORDER BY t.session_id, t.source, t."index" """,
            _project_parameters(project),
        ).fetchall():
            turns.setdefault((session_id, source), []).append((turn_id, prompt))
        calls = self._api_calls(main=False, project=project)
        items: list[AgentRunItem] = []
        for session_id, run_id, agent_type in runs:
            local = turns.get((session_id, run_id), [])
            local_ids = {turn_id for turn_id, _ in local}
            sequence = calls.get((session_id, run_id), [])
            continuation = [row for turn_id, row in sequence if turn_id not in local_ids]
            by_turn: dict[str, list[ApiCallRow]] = {}
            for turn_id, row in sequence:
                if turn_id is not None and turn_id in local_ids:
                    by_turn.setdefault(turn_id, []).append(row)
            sections = (
                [RunSection(prompt=None, api_calls=tuple(continuation))] if continuation else []
            )
            sections += [
                RunSection(prompt=prompt, api_calls=tuple(by_turn.get(turn_id, ())))
                for turn_id, prompt in local
            ]
            if not sections:
                # No turn and no api call: nothing to describe, and no recorded run is in
                # this state (2,459 scanned). Crash rather than buy a description of nothing.
                raise ValueError(f"agent run {session_id}/{run_id} holds no turn and no api call")
            items.append(
                AgentRunItem(
                    session_id=session_id,
                    agent_run_id=run_id,
                    agent_type=agent_type,
                    sections=tuple(sections),
                )
            )
        return items

    def _api_calls(
        self, *, main: bool, project: str | None
    ) -> dict[tuple[str, str], list[tuple[str | None, ApiCallRow]]]:
        """Every api call of the selected sources, in order, with its tool calls attached.

        Keyed by session and source, each call paired with the turn it belongs to — which is
        None for a call no turn opened. Read in two queries and joined here rather than in
        SQL: a row per tool call would repeat every call's text once per tool.
        """
        spawned = self._spawned_descriptions()
        tools: dict[tuple[str, str, str], list[ToolCallRow]] = {}
        for (
            session_id,
            source,
            api_call_id,
            tool_call_id,
            name,
            tool_input,
            result,
            is_error,
            incomplete,
        ) in self.connection.execute(
            f"""SELECT c.session_id, c.source, c.api_call_id, c.id, c.name, c.input, c.result,
                       c.is_error, c.incomplete
                FROM live_tool_calls c
                JOIN live_api_calls a
                  ON a.session_id = c.session_id AND a.source = c.source
                 AND a.id = c.api_call_id
                JOIN sessions s ON s.id = c.session_id
                WHERE {_source_clause("c", main=main)}{_project_clause(project)}
                ORDER BY c.session_id, c.source, c."index" """,
            _project_parameters(project),
        ).fetchall():
            tools.setdefault((session_id, source, api_call_id), []).append(
                ToolCallRow(
                    name=name,
                    input=tool_input,
                    result=result,
                    is_error=is_error,
                    incomplete=incomplete,
                    spawned=spawned.get((session_id, source, tool_call_id)),
                )
            )
        calls: dict[tuple[str, str], list[tuple[str | None, ApiCallRow]]] = {}
        for session_id, source, turn_id, api_call_id, text, stop_reason in self.connection.execute(
            f"""SELECT a.session_id, a.source, a.turn_id, a.id, a.text, a.stop_reason
                FROM live_api_calls a JOIN sessions s ON s.id = a.session_id
                WHERE {_source_clause("a", main=main)}{_project_clause(project)}
                ORDER BY a.session_id, a.source, a."index" """,
            _project_parameters(project),
        ).fetchall():
            calls.setdefault((session_id, source), []).append(
                (
                    turn_id,
                    ApiCallRow(
                        text=text,
                        stop_reason=stop_reason,
                        tool_calls=tuple(tools.get((session_id, source, api_call_id), ())),
                    ),
                )
            )
        return calls

    def _spawned_descriptions(self) -> dict[tuple[str, str, str], str]:
        """What each spawning tool call's run was described as, for the calls that have one.

        Keyed by the *call*, so a tool line can carry its child's description. A call
        recorded inside the very run it spawned is left out: forking replays the spawning
        call into the fork's own transcript, and a run embedding itself is a cycle.
        """
        return {
            (session_id, source, tool_call_id): description
            for session_id, source, tool_call_id, description in self.connection.execute(
                """SELECT c.session_id, c.source, c.id, e.description
                   FROM live_tool_calls c
                   JOIN live_agent_runs r
                     ON r.session_id = c.session_id AND r.tool_use_id = c.id
                   JOIN agent_run_enrichments e
                     ON e.session_id = r.session_id AND e.agent_run_id = r.id
                   WHERE c.source <> r.id"""
            ).fetchall()
        }

    def session_items(self, project: str | None = None) -> list[SessionItem]:
        """Every session worth describing, with what it cost and what its children did.

        `describable_sessions` decides which those are: 102 of 575 recorded sessions hold no
        main turn and no agent run, and 45 more drove no api call under the turns they hold.
        """
        children = self._session_children(project)
        return [
            SessionItem(
                session_id=session_id,
                title=title,
                git_branch=git_branch,
                wall_ms=wall_ms,
                active_ms=active_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cost_usd=cost_usd,
                children=tuple(children.get(session_id, ())),
            )
            for (
                session_id,
                title,
                git_branch,
                wall_ms,
                active_ms,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_creation_tokens,
                cost_usd,
            ) in self.connection.execute(
                f"""SELECT r.session_id, s.title, s.git_branch, r.wall_ms, r.active_ms,
                           r.input_tokens, r.output_tokens, r.cache_read_tokens,
                           r.cache_creation_tokens, r.cost_usd
                    FROM describable_sessions r JOIN sessions s ON s.id = r.session_id
                    WHERE true{_project_clause(project)}
                    ORDER BY r.session_id""",
                _project_parameters(project),
            ).fetchall()
        ]

    def _session_children(self, project: str | None) -> dict[str, list[SessionChild]]:
        """What each session did directly, in the order it started doing it.

        Its main turns, plus the runs nothing in the session embeds — everything else reaches
        the session through the turn or the run whose prompt carries its description.
        """
        direct = {
            (link.session_id, link.run_id)
            for link in self._run_links(project)
            if link.parent_run is None and link.parent_turn is None
        }
        rows = [
            (session_id, started_at, SessionChild(Level.turn, None, *enrichment))
            for session_id, started_at, *enrichment in self.connection.execute(
                f"""SELECT t.session_id, t.started_at, e.description, e.category, e.outcome
                    FROM live_turns t JOIN sessions s ON s.id = t.session_id
                    LEFT JOIN turn_enrichments e
                      ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id
                    WHERE {_source_clause("t", main=True)}{_project_clause(project)}""",
                _project_parameters(project),
            ).fetchall()
        ]
        rows += [
            (session_id, started_at, SessionChild(Level.agent_run, agent_type, *enrichment))
            for session_id, run_id, agent_type, started_at, *enrichment in self.connection.execute(
                f"""SELECT r.session_id, r.id, r.agent_type, r.started_at,
                           e.description, e.category, e.outcome
                    FROM live_agent_runs r JOIN sessions s ON s.id = r.session_id
                    LEFT JOIN agent_run_enrichments e
                      ON e.session_id = r.session_id AND e.agent_run_id = r.id
                    WHERE true{_project_clause(project)}""",
                _project_parameters(project),
            ).fetchall()
            if (session_id, run_id) in direct
        ]
        children: dict[str, list[SessionChild]] = {}
        for session_id, _, child in sorted(rows, key=lambda row: (row[1] is None, row[1])):
            children.setdefault(session_id, []).append(child)
        return children

    def items(self, level: Level, project: str | None = None) -> list[Item]:
        """Every enrichable item of one level. The enricher's one door into the store."""
        readers = {
            Level.turn: self.turn_items,
            Level.agent_run: self.run_items,
            Level.session: self.session_items,
        }
        return list(readers[level](project))

    def _run_links(self, project: str | None) -> list[RunLink]:
        """Each agent run against whatever spawned it, by both rules the records offer.

        `parent_agent_id` where the records name one, and otherwise the transcript holding the
        spawning tool call. Both are needed: 112 of 2,459 recorded runs name no parent agent
        yet were spawned from inside another run, and either rule alone strands them.

        Ordering cannot be right for a tree with a gap in it, so a run naming a parent run the
        store does not hold crashes here rather than being treated as a root.
        """
        rows = self.connection.execute(
            f"""SELECT r.session_id, r.id, r.parent_agent_id, c.source, a.turn_id
                FROM live_agent_runs r
                JOIN sessions s ON s.id = r.session_id
                -- The spawning call, excluding the copy of itself a fork's own transcript
                -- holds: a run is not its own parent.
                LEFT JOIN live_tool_calls c
                  ON c.session_id = r.session_id AND c.id = r.tool_use_id AND c.source <> r.id
                LEFT JOIN live_api_calls a
                  ON a.session_id = c.session_id AND a.source = c.source
                 AND a.id = c.api_call_id
                WHERE true{_project_clause(project)}""",
            _project_parameters(project),
        ).fetchall()
        held = {(session_id, run_id) for session_id, run_id, *_ in rows}
        links: list[RunLink] = []
        for session_id, run_id, parent_agent_id, source, turn_id in rows:
            run = parent_agent_id or (source if source not in (None, MAIN_SOURCE) else None)
            if run is not None and (session_id, run) not in held:
                raise ValueError(
                    f"agent run {session_id}/{run_id} names parent run {run}, which the store"
                    f" does not hold — re-extract the session before enriching it"
                )
            links.append(
                RunLink(
                    session_id=session_id,
                    run_id=run_id,
                    parent_run=run,
                    parent_turn=turn_id if run is None else None,
                )
            )
        return links

    def item_parents(self, project: str | None = None) -> dict[str, str | None]:
        """Each item's key against the key of the item whose prompt embeds its description.

        A run's parent is the agent that spawned it, or the main turn that did, or — when
        nothing in the session embeds it — the session itself. A main turn's parent is always
        its session. Sessions are not here: nothing embeds a session, so they are the roots
        every chain ends at.

        A run's `tool_use_id` alone would not do: 9 recorded runs were spawned by a
        main-transcript call belonging to no turn, and reading those as embedded by nothing
        *and* claimed by nothing would drop them out of every render there is.
        """
        parents: dict[str, str | None] = {}
        for link in self._run_links(project):
            if link.parent_run is not None:
                parent = f"{Level.agent_run}|{link.session_id}|{link.parent_run}"
            elif link.parent_turn is not None:
                parent = f"{Level.turn}|{link.session_id}|{MAIN_SOURCE}|{link.parent_turn}"
            else:
                parent = f"{Level.session}|{link.session_id}"
            parents[f"{Level.agent_run}|{link.session_id}|{link.run_id}"] = parent
        for session_id, turn_id in self.connection.execute(
            f"""SELECT t.session_id, t.id FROM live_turns t JOIN sessions s ON s.id = t.session_id
                WHERE {_source_clause("t", main=True)}{_project_clause(project)}""",
            _project_parameters(project),
        ).fetchall():
            parents[f"{Level.turn}|{session_id}|{MAIN_SOURCE}|{turn_id}"] = (
                f"{Level.session}|{session_id}"
            )
        return parents

    def stale_keys(self, level: Level, planned: Mapping[str, Stamp]) -> list[str]:
        """The planned items whose stored stamp is not the one the enricher would write now.

        Called again after every round: a child's new description changes its parents'
        rendered input, and only a fresh comparison sees that.
        """
        stored = self._stamps(level)
        return [key for key, stamp in planned.items() if stored.get(key) != stamp]

    def _stamps(self, level: Level) -> dict[str, Stamp]:
        spec = LEVELS[level]
        columns = ", ".join(spec.keys)
        rows = self.connection.execute(
            f"SELECT {columns}, input_hash, prompt_version, taxonomy_version, model"
            f" FROM {spec.table}"
        ).fetchall()
        width = len(spec.keys)
        return {"|".join((level, *row[:width])): Stamp(*row[width:]) for row in rows}

    def upsert(self, item: Item, enrichment: Enrichment, stamp: Stamp) -> None:
        """Write one item's enrichment, replacing whatever the key held before."""
        spec = LEVELS[item.level]
        columns = ", ".join((*spec.keys, *_PAYLOAD_COLUMNS))
        placeholders = ", ".join("?" for _ in range(len(spec.keys) + len(_PAYLOAD_COLUMNS)))
        self.connection.execute(
            f"INSERT OR REPLACE INTO {spec.table} ({columns}) VALUES ({placeholders})",
            [
                *item.key_values,
                enrichment.description,
                str(enrichment.category),
                str(enrichment.outcome),
                enrichment.friction,
                stamp.input_hash,
                stamp.prompt_version,
                stamp.taxonomy_version,
                stamp.model,
                dt.datetime.now(dt.UTC),
            ],
        )

    def sweep_zombies(self) -> int:
        """Delete enrichments whose base row is gone, and say how many there were.

        An extractor bump can redraw turn boundaries or drop a run, and the LEFT-joined
        views hide the leftovers completely — nothing else in the system would report them.
        """
        swept = 0
        for spec in LEVELS.values():
            match = " AND ".join(
                f"b.{base} = e.{key}" for base, key in zip(spec.base_keys, spec.keys, strict=True)
            )
            deleted = self.connection.execute(
                f"DELETE FROM {spec.table} e"
                f" WHERE NOT EXISTS (SELECT 1 FROM {spec.base} b WHERE {match})"
            ).fetchone()
            swept += deleted[0] if deleted else 0
        return swept
