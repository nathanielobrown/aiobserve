"""The trace store as an extractor: its rows back out as `SessionTrace`s.

Pairs with an exporter that ships somewhere else — the OTLP export reads the store rather
than the transcripts on disk, because the store is the archive (a pruned session exists only
here), because a backend then mirrors exactly what the analyses and the viewer cite, and
because reading rows costs a fraction of re-parsing every record.

Rebuilding is mechanical: `export/duckdb.py`'s `TABLES` registry drives the read, so the
columns, the session key and the row order are the ones the write used and a new column
reaches both sides at once. Provenance is not rebuilt — `extract_state`'s `extractor` and
`extractor_version` come back verbatim, naming the parser that produced the rows rather than
this reader.
"""

from dataclasses import fields
from pathlib import Path
from typing import Any

import duckdb

from hyphae.export.duckdb import TABLES
from hyphae.model import SessionTrace
from hyphae.pipeline import SessionSource
from hyphae.projects import project_predicate, resolve_project

# The tables that are the archive rather than the session's work: every line of every
# transcript, and the tool outputs Claude Code wrote to files beside it. Nothing ships them,
# so they say nothing about whether excluding a session loses anything.
ARCHIVE_TABLES = frozenset({"raw_records", "offload_files"})


class UnplaceableSessionError(Exception):
    """A session that recorded no `project_dir` holds rows, so no filter can ship it."""


class UnknownSessionError(Exception):
    """Asked for a session the store never extracted."""


class UnknownProjectError(Exception):
    """Asked for a project no session in the store was recorded under."""


class StoreSource:
    """Reads one project's extracted sessions back out of a trace store.

    Takes an open connection rather than a path: DuckDB admits one writer at a time, so the
    exporter writing its delivery rows beside this reader has to be holding the same one.
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def sessions(self, project: Path) -> list[SessionSource]:
        """Every extracted session recorded at or under `project`, resolved as typed.

        No files: the store is the source, so there is nothing on disk to stat, and the
        fingerprint is the one `extract_state` recorded when the rows were written.

        Sessions with no `project_dir` sit under no project and are excluded by the filter
        itself. That is only safe while they are empty, so one holding rows crashes here
        rather than disappearing.

        A project nothing was recorded under is refused: the store is a finite corpus, so an
        empty answer here is a mistyped argument, and the export it feeds would otherwise
        report a clean delivery of nothing.
        """
        self._refuse_unplaceable_content()
        project = resolve_project(project)
        rows = self.connection.execute(
            "SELECT e.session_id, e.fingerprint FROM extract_state e"
            " JOIN sessions s ON s.id = e.session_id"
            f" WHERE {project_predicate('s.project_dir')}"
            " ORDER BY e.session_id",
            # Twice: the predicate names its placeholder in both halves.
            [str(project), str(project)],
        ).fetchall()
        if not rows:
            raise UnknownProjectError(
                f"No session in this store was recorded under {project}. "
                f"Check the path, or run `hp extract` for it first."
            )
        return [
            SessionSource(id=session_id, files=(), fingerprint=fingerprint)
            for session_id, fingerprint in rows
        ]

    def extract(self, source: SessionSource) -> SessionTrace:
        """Rebuild one session's whole trace from its rows."""
        state = self.connection.execute(
            "SELECT extractor, extractor_version FROM extract_state WHERE session_id = ?",
            [source.id],
        ).fetchone()
        if state is None:
            raise UnknownSessionError(f"{source.id} is not in this store")
        entities = {table: self._read(table, source.id) for table in TABLES}
        # Every other table is a list on the trace under the same name; `sessions` is the one
        # row they hang off.
        session = entities.pop("sessions")
        if len(session) != 1:
            raise UnknownSessionError(
                f"{source.id} has an `extract_state` row but {len(session)} session rows"
            )
        return SessionTrace(
            extractor=state[0], extractor_version=state[1], session=session[0], **entities
        )

    def _read(self, table: str, session_id: str) -> list[Any]:
        """One table's rows for one session, as its model."""
        spec = TABLES[table]
        columns = ", ".join(f'"{field.name}"' for field in fields(spec.model))
        order = ", ".join(f'"{column}"' for column in spec.order)
        rows = self.connection.execute(
            f"SELECT {columns} FROM {table} WHERE {spec.session_key} = ? ORDER BY {order}",
            [session_id],
        ).fetchall()
        return [spec.model(*row) for row in rows]

    def _refuse_unplaceable_content(self) -> None:
        """Crash when a session with no `project_dir` holds work this filter would drop.

        The bar for a finding is that an absence is bounded: excluding a session because it
        names no project is honest only while excluding it loses nothing.

        Only the work tables can make it dishonest. Every transcript has lines, so a session
        that recorded nothing but its own opening bookkeeping still owns `raw_records` — the
        shape all four unplaceable sessions of the canonical store have. Those rows and the
        offloaded outputs beside them are the archive, which stays local whatever ships, so
        they are reported in the message and are never the reason for it.
        """
        owned = [table for table in TABLES if table != "sessions"]
        work = [table for table in owned if table not in ARCHIVE_TABLES]
        counts = ", ".join(
            f"(SELECT count(*) FROM {table} t WHERE t.session_id = s.id)" for table in owned
        )
        for session_id, *found in self.connection.execute(
            f"SELECT s.id, {counts} FROM sessions s WHERE s.project_dir IS NULL"
        ).fetchall():
            rows = dict(zip(owned, found, strict=True))
            if not any(rows[table] for table in work):
                continue
            held = ", ".join(f"{table} {count}" for table, count in rows.items() if count)
            raise UnplaceableSessionError(
                f"Session {session_id} records no project_dir but holds {held}. It sits "
                f"under no project, so shipping it is impossible and skipping it would "
                f"lose that work silently."
            )
