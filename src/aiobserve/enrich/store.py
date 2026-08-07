"""The enrichment tables, and everything that reads or writes them.

These tables live in the same DuckDB file as the trace store but outside the pipeline's
per-session replace, so a re-extraction never touches them. They attach to the pipeline's
natural keys, which come from the data and survive re-extraction with it.

Open a store, ask it for the items of a level, and it hands back rows to render. Ask it what
is stale and it compares each item's `Stamp` against the one on disk.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import duckdb

from aiobserve.enrich.prompts import (
    AgentRunItem,
    ApiCallRow,
    Item,
    Level,
    RunSection,
    ToolCallRow,
    TurnItem,
)
from aiobserve.enrich.validation import Enrichment
from aiobserve.export.duckdb import SCHEMA_VERSION, SchemaVersionError
from aiobserve.model import MAIN_SOURCE

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
-- LEFT join, so an un-enriched turn still appears and coverage reads honestly. The
-- enrichment's own model is renamed: `agent_runs` carries a `model` of its own, and the
-- three views answer the same question the same way.
CREATE OR REPLACE VIEW enriched_turns AS
SELECT t.*, e.description, e.category, e.outcome, e.friction, e.input_hash,
       e.prompt_version, e.taxonomy_version, e.model AS enrichment_model, e.enriched_at
FROM live_turns t
LEFT JOIN turn_enrichments e
  ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id;
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
        base="sessions",
        base_keys=("id",),
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


def _project_clause(project: str | None) -> str:
    """Narrows a query already joined to `sessions s` to one analyzed repository."""
    return " AND s.project_dir = ?" if project is not None else ""


def _project_parameters(project: str | None) -> list[str]:
    return [project] if project is not None else []


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
        self.connection = duckdb.connect(str(path))
        self.connection.execute("SET TimeZone='UTC'")
        self._check_base_schema()
        self.connection.execute(_SCHEMA)

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
        self.connection.close()

    def _check_base_schema(self) -> None:
        """Refuse a file whose base tables this build cannot read, before creating anything.

        Enrichment reads the pipeline's views by name and column, so a store written by
        another schema version is not a store this code can enrich.
        """
        held = self.connection.execute(
            "SELECT schema_version FROM duckdb_tables() t"
            " LEFT JOIN meta ON true WHERE t.table_name = 'meta'"
        ).fetchone()
        if held is None or held[0] != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{self.path} holds schema version {held[0] if held else 'nothing'}, this "
                f"build enriches {SCHEMA_VERSION}. Extract into it first, or point at the "
                f"canonical store."
            )

    def turn_items(self, project: str | None = None) -> list[TurnItem]:
        """Every enrichable main turn, each carrying the api and tool calls it drove.

        `project` filters by the analyzed repository's path, as `sessions.project_dir`
        records it; None takes every session in the store.
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
                api_calls=tuple(by_turn.get((session_id, source, turn_id), ())),
            )
            for session_id, source, turn_id, index, prompt, command_name, command_args in turns
        ]

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
        for session_id, source, turn_id, api_call_id, text in self.connection.execute(
            f"""SELECT a.session_id, a.source, a.turn_id, a.id, a.text
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
