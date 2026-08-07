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

from aiobserve.enrich.prompts import ApiCallRow, Item, Level, ToolCallRow, TurnItem
from aiobserve.enrich.validation import Enrichment
from aiobserve.export.duckdb import SCHEMA_VERSION, SchemaVersionError

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
        where = "t.source = 'main'"
        parameters: list[str] = []
        if project is not None:
            where += " AND s.project_dir = ?"
            parameters.append(project)
        turns = self.connection.execute(
            f"""SELECT t.session_id, t.source, t.id, t."index", t.prompt,
                       t.command_name, t.command_args
                FROM live_turns t JOIN sessions s ON s.id = t.session_id
                WHERE {where} ORDER BY t.session_id, t."index" """,
            parameters,
        ).fetchall()
        calls = self._api_calls(where, parameters)
        return [
            TurnItem(
                session_id=session_id,
                source=source,
                turn_id=turn_id,
                index=index,
                prompt=prompt,
                command_name=command_name,
                command_args=command_args,
                api_calls=tuple(calls.get((session_id, source, turn_id), ())),
            )
            for session_id, source, turn_id, index, prompt, command_name, command_args in turns
        ]

    def _api_calls(
        self, where: str, parameters: list[str]
    ) -> dict[tuple[str, str, str], list[ApiCallRow]]:
        """Every api call of the selected turns, in order, with its tool calls attached.

        Read in two queries and joined here rather than in SQL: a row per tool call would
        repeat every call's text once per tool.
        """
        tools: dict[tuple[str, str, str], list[ToolCallRow]] = {}
        for (
            session_id,
            source,
            api_call_id,
            name,
            tool_input,
            result,
            is_error,
            incomplete,
        ) in self.connection.execute(
            f"""SELECT c.session_id, c.source, c.api_call_id, c.name, c.input, c.result,
                           c.is_error, c.incomplete
                    FROM live_tool_calls c
                    JOIN live_api_calls a
                      ON a.session_id = c.session_id AND a.source = c.source
                     AND a.id = c.api_call_id
                    JOIN live_turns t
                      ON t.session_id = a.session_id AND t.source = a.source
                     AND t.id = a.turn_id
                    JOIN sessions s ON s.id = t.session_id
                    WHERE {where} ORDER BY c.session_id, c."index" """,
            parameters,
        ).fetchall():
            tools.setdefault((session_id, source, api_call_id), []).append(
                ToolCallRow(
                    name=name,
                    input=tool_input,
                    result=result,
                    is_error=is_error,
                    incomplete=incomplete,
                )
            )
        calls: dict[tuple[str, str, str], list[ApiCallRow]] = {}
        for session_id, source, turn_id, api_call_id, text in self.connection.execute(
            f"""SELECT a.session_id, a.source, a.turn_id, a.id, a.text
                FROM live_api_calls a
                JOIN live_turns t
                  ON t.session_id = a.session_id AND t.source = a.source AND t.id = a.turn_id
                JOIN sessions s ON s.id = t.session_id
                WHERE {where} ORDER BY a.session_id, a."index" """,
            parameters,
        ).fetchall():
            calls.setdefault((session_id, source, turn_id), []).append(
                ApiCallRow(
                    text=text, tool_calls=tuple(tools.get((session_id, source, api_call_id), ()))
                )
            )
        return calls

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
