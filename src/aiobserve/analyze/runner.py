"""Runs one library query against the trace store and hands back its rows and its citation.

The store is opened read-only, and every value the caller supplies reaches DuckDB as a bound
parameter — nothing is interpolated into SQL. A corpus query gets one thing from the runner
that its file does not define: `project_sessions`, the temp table holding the sessions
`--project` selected and whether each falls in the trailing window.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from aiobserve.analyze import queries
from aiobserve.analyze.queries import NoDefault, ParamType, ParamValue, Scope
from aiobserve.export.duckdb import SCHEMA_VERSION

# The sessions `--project` selects, and the window flag every corpus query reads. Defined
# here rather than in each file so the `/`-suffix trap — without it the predicate annexes
# every neighbouring checkout — is written once.
_PROJECT_SESSIONS = """
CREATE OR REPLACE TEMP TABLE project_sessions AS
SELECT
    id AS session_id,
    started_at,
    coalesce(
        started_at >= $as_of::DATE - to_days($window_days::INTEGER)
            AND started_at < $as_of::DATE + INTERVAL 1 DAY,
        false
    ) AS in_window
FROM sessions
WHERE (project_dir = $project OR starts_with(project_dir, $project || '/'))
  AND ($since::DATE IS NULL OR started_at >= $since::DATE)
"""

# The two windows every count is reported in, as rows a count can group by. Written here for
# the same reason as the predicate above: a query that filtered its own window would be a
# second implementation of the recency rule, free to drift from the total it restricts.
_SESSION_PERIODS = """
CREATE OR REPLACE TEMP VIEW session_period AS
SELECT session_id, 'corpus' AS period FROM project_sessions
UNION ALL
SELECT session_id, 'trailing_window' AS period FROM project_sessions WHERE in_window
"""

# What the runner puts in scope for a corpus query. A query that reads neither is not scoped
# to `--project` at all, whatever its manifest says.
CORPUS_RELATIONS = ("project_sessions", "session_period")

# Sessions no project predicate can place. They are excluded from every corpus count, so the
# runner reports how many there were rather than leaving the gap silent.
_UNPLACEABLE = "SELECT count(*) FROM sessions WHERE project_dir IS NULL"


class QueryError(Exception):
    """The caller asked for something the library cannot run, and it says which part."""


@dataclass(frozen=True)
class Result:
    """One query's rows, and the line a report copies to show what produced them."""

    name: str
    # Resolved bindings in citation order — every one at the value DuckDB actually saw.
    bindings: dict[str, ParamValue]
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]
    # Sessions with no `project_dir`; None for a keyed query, which asks about one session.
    unplaceable_sessions: int | None

    @property
    def citation(self) -> str:
        """Query file and resolved bindings, as a SQL comment: the claim's query."""
        bound = " ".join(f"{key}={_show(value)}" for key, value in self.bindings.items())
        return f"-- queries/{self.name}.sql {bound}".rstrip()


def run(
    db: Path,
    name: str,
    *,
    project: Path | None,
    since: dt.date | None,
    as_of: dt.date,
    params: Mapping[str, str],
) -> Result:
    """Bind one library query and run it read-only against the store at `db`.

    `params` are raw `k=v` strings; each is parsed to the type its manifest entry declares.
    Raises `QueryError` for anything the manifest cannot account for — an unknown query, an
    undeclared parameter, a required one left unbound, `--project` where it is needed or
    where it means nothing.
    """
    query = queries.QUERIES.get(name)
    if query is None:
        raise QueryError(f"no query named {name!r}. Known queries: {', '.join(queries.QUERIES)}")
    bindings = _resolve(name, query.params, params)
    corpus = query.scope is Scope.CORPUS
    if corpus and project is None:
        raise QueryError(f"{name} counts across sessions: it needs --project")
    if not corpus and (project is not None or since is not None):
        raise QueryError(
            f"{name} is keyed to one session: --project and --since mean nothing to it"
        )

    connection = duckdb.connect(str(db), read_only=True)
    try:
        # Timestamps went in as UTC; a window measured in the machine's local zone would
        # move the corpus by a few hours depending on where the reader sits.
        connection.execute("SET TimeZone='UTC'")
        _check_schema(db, connection)
        cited: dict[str, ParamValue] = {}
        unplaceable = None
        if corpus:
            assert project is not None  # `corpus and project is None` raised above
            cited = _build_project_sessions(connection, project, since, as_of)
            unplaceable = connection.execute(_UNPLACEABLE).fetchone()[0]  # type: ignore[index]
        cursor = connection.execute(queries.load(name), dict(bindings))
        columns = tuple(column[0] for column in cursor.description or ())
        return Result(
            name=name,
            bindings=cited | bindings,
            columns=columns,
            rows=cursor.fetchall(),
            unplaceable_sessions=unplaceable,
        )
    finally:
        connection.close()


def _build_project_sessions(
    connection: duckdb.DuckDBPyConnection, project: Path, since: dt.date | None, as_of: dt.date
) -> dict[str, ParamValue]:
    """Materialize the corpus for `project`, and return the bindings that defined it."""
    # A trailing slash is the same corpus: `mycelia/` and `mycelia` name one repository, and
    # the predicate supplies its own `/` to pick up the worktrees below it.
    resolved = str(Path(str(project).rstrip("/")).resolve())
    bindings: dict[str, ParamValue] = {
        "project": resolved,
        "since": since,
        "as_of": as_of,
        "window_days": queries.WINDOW_DAYS,
    }
    connection.execute(_PROJECT_SESSIONS, bindings)
    connection.execute(_SESSION_PERIODS)
    return bindings


def _resolve(
    name: str, declared: Mapping[str, queries.Param], given: Mapping[str, str]
) -> dict[str, ParamValue]:
    """Parse what the caller passed, fill in the production defaults, refuse the rest."""
    unknown = set(given) - set(declared)
    if unknown:
        raise QueryError(f"{name} declares no parameter named {', '.join(sorted(unknown))}")
    resolved: dict[str, ParamValue] = {}
    missing = []
    for parameter, spec in declared.items():
        if parameter in given:
            resolved[parameter] = _parse(parameter, spec.type, given[parameter])
        elif isinstance(spec.default, NoDefault):
            missing.append(parameter)
        else:
            resolved[parameter] = spec.default
    if missing:
        raise QueryError(
            f"{name} has no default for {', '.join(missing)}: "
            f"bind each with --param {missing[0]}=<value>"
        )
    return resolved


def _parse(parameter: str, type_: ParamType, text: str) -> ParamValue:
    try:
        match type_:
            case ParamType.TEXT:
                return text
            case ParamType.INTEGER:
                return int(text)
            case ParamType.DATE:
                return dt.date.fromisoformat(text)
    except ValueError as error:
        raise QueryError(f"--param {parameter}={text} is not a {type_}: {error}") from error


def _check_schema(db: Path, connection: duckdb.DuckDBPyConnection) -> None:
    """Refuse a store this build's queries were not written against."""
    row = connection.execute("SELECT schema_version FROM meta").fetchone()
    if row is None or row[0] != SCHEMA_VERSION:
        raise QueryError(
            f"{db} holds schema version {row and row[0]}, these queries read {SCHEMA_VERSION}. "
            f"Delete the database and re-extract."
        )


def _show(value: ParamValue) -> str:
    """A binding as it goes in the citation — NULL is a value a reader can rebind."""
    return "NULL" if value is None else str(value)
