"""The `hp query` runner: what it selects, what it binds, and what it cites.

Nothing is mocked — every leaf drives `cli.main("query", …)` against a real DuckDB built
from recorded fixtures, and reads the two streams apart, because which stream a line lands
on is itself a contract a piped analysis depends on.
"""

import datetime as dt
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyphae.analyze import manifest, queries
from hyphae.export.schema import MIGRATE_REMEDY, SCHEMA_MISMATCH_REMEDY, SCHEMA_VERSION
from tests.analyze.conftest import AS_OF_PARTIAL, MYCELIA_SESSIONS, Output, QueryRunner, query
from tests.conftest import (
    MAIN,
    MYCELIA,
    NO_PROJECT_SESSION,
    NON_CORPUS,
    RESUME,
    SIBLING_SESSION,
    SPINE,
    WORKTREE_SESSION,
)


def test_a_project_selects_its_own_sessions_and_no_others(run_query: QueryRunner) -> None:
    """`--project` scopes a corpus query to one repository's sessions."""
    # If the store holds three sessions recorded outside mycelia — two other projects and
    # one with no `project_dir` at all...
    result = run_query("sessions", "--project", MYCELIA, "--csv")
    # ...then the corpus is the 13 mycelia sessions, and none of the three.
    ids = result.column("session_id")
    assert len(ids) == MYCELIA_SESSIONS
    assert set(ids).isdisjoint(NON_CORPUS)
    assert SPINE in ids


def test_a_worktree_session_is_in_the_corpus_and_a_prefix_sibling_is_not(
    worktree_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session run in the project's worktree counts; a neighbouring checkout does not."""
    # If one session sits under `<project>/.claude/worktrees/` and another under a checkout
    # that merely shares the prefix (both planted `project_dir`s over real traces)...
    result = query(worktree_db, capsys, "sessions", "--project", MYCELIA, "--csv")
    ids = result.column("session_id")
    # ...then the worktree child is in the corpus...
    assert WORKTREE_SESSION in ids
    # ...and the sibling is not: `starts_with` without the `/` would annex every neighbour,
    # and the failure would be a wrong number rather than an error.
    assert SIBLING_SESSION not in ids


@pytest.mark.parametrize(
    "spelling",
    [f"{MYCELIA}/", str(Path(MYCELIA).relative_to("/"))],
    ids=["trailing-slash", "relative"],
)
def test_every_spelling_of_one_project_names_one_corpus(
    run_query: QueryRunner, spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """However the shell reached a repository, `--project` selects the same sessions."""
    # A recorded `project_dir` is an absolute path, so the root is the one working directory
    # a relative spelling of one can be typed from.
    monkeypatch.chdir("/")
    rows = run_query("sessions", "--project", spelling, "--csv").csv_rows()
    assert rows == run_query("sessions", "--project", MYCELIA, "--csv").csv_rows()
    # Two identical empty corpora would prove nothing.
    assert len(rows) == MYCELIA_SESSIONS + 1


def test_the_excluded_count_goes_to_stderr_and_csv_stdout_stays_clean(
    run_query: QueryRunner,
) -> None:
    """The sessions the predicate could not judge are reported, off the data stream."""
    result = run_query("sessions", "--project", MYCELIA, "--csv")
    # If one recorded session carries no `project_dir`, so no predicate can place it...
    assert "1" in result.stderr and "excluded" in result.stderr
    # ...then that count is on stderr, and stdout is the header plus one row per session and
    # nothing else — prose on stdout would break every piped analysis silently.
    rows = result.csv_rows()
    assert len(rows) == MYCELIA_SESSIONS + 1
    assert all(len(row) == len(rows[0]) for row in rows)
    assert NO_PROJECT_SESSION not in result.stdout


def test_the_citation_names_the_query_file_and_every_resolved_binding(
    run_query: QueryRunner,
) -> None:
    """Each result carries the line a report copies to show what it ran."""
    # If a corpus query runs with an explicit `--as-of`...
    table = run_query("sessions", "--project", MYCELIA, "--as-of", AS_OF_PARTIAL)
    # ...then the citation heads the table, naming the file and every resolved binding —
    # `$as_of` as its date, and the `--since` the caller never passed as NULL rather than
    # dropped, because a citation a reader cannot paste back is not a citation.
    citation = table.stdout.splitlines()[0]
    assert citation == (
        f"-- queries/sessions.sql project={MYCELIA} since=NULL"
        f" as_of={AS_OF_PARTIAL} window_days={queries.WINDOW_DAYS}"
    )
    # ...and under `--csv` the same line moves to stderr, leaving stdout machine-readable.
    piped = run_query("sessions", "--project", MYCELIA, "--as-of", AS_OF_PARTIAL, "--csv")
    assert citation in piped.stderr
    assert "queries/sessions.sql" not in piped.stdout


def test_as_of_defaults_to_today(run_query: QueryRunner) -> None:
    """A bare run cites the date its window was measured back from."""
    result = run_query("sessions", "--project", MYCELIA)
    today = dt.datetime.now(tz=dt.UTC).date()
    assert f"as_of={today.isoformat()}" in result.stdout.splitlines()[0]


def test_since_filters_and_omitting_it_means_the_whole_corpus(run_query: QueryRunner) -> None:
    """`--since` cuts the corpus at a date; with no `--since` there is no cut."""
    # If seven mycelia sessions started on or after 2026-07-15...
    since = run_query("sessions", "--project", MYCELIA, "--since", "2026-07-15", "--csv")
    assert len(since.column("session_id")) == 7
    # ...then the same query with no `--since` still returns the whole corpus.
    whole = run_query("sessions", "--project", MYCELIA, "--csv")
    assert len(whole.column("session_id")) == MYCELIA_SESSIONS


def test_the_production_defaults_run_unless_a_param_overrides_one(run_query: QueryRunner) -> None:
    """A run with no `--param` uses the manifest's numbers, and an override moves only its own.

    Stands in for `select_sessions`, whose quotas are the design's headline defaults and land
    with selection in the next slice; `records_slice`'s cap is the same mechanism.
    """
    keys = (
        "--param",
        f"session_id={RESUME}",
        "--param",
        f"source={MAIN}",
        "--param",
        "first_line=1",
        "--param",
        "last_line=1",
    )
    # If a query declares a parameter with a production default and the caller binds none...
    bare = run_query("records_slice", *keys, "--csv")
    # ...the citation reports the manifest's value, which is what a committed report quotes...
    assert _bindings(bare)["max_chars"] == str(queries.RAW_CHARS)
    # ...and an explicit override moves that one binding and no other...
    overridden = run_query("records_slice", *keys, "--param", "max_chars=50", "--csv")
    assert _bindings(overridden) == {**_bindings(bare), "max_chars": "50"}
    # ...and the result obeys the value the citation reports, which is the point of citing it.
    assert len(overridden.csv_rows()[1][-1]) == 50


def test_the_listing_names_every_query_with_its_scope_and_what_it_needs_bound(
    run_query: QueryRunner,
) -> None:
    """`--list` is the library's directory: a reader picks a name off it and knows what to bind.

    Read off the registry rather than written down, so a query that ships is a line here
    whichever half of the manifest declared it. The viewer's half declares no defaults — a
    size belongs to the surface that prints it (`view/manifest.py`) — so for those queries
    this is the only place a caller finds out what a bare run is missing.
    """
    printed = run_query("--list").stdout.splitlines()
    listed = {line.split()[0]: line.split()[1:] for line in printed}
    # One line per query, and the names are the registry's...
    assert len(printed) == len(manifest.QUERIES)
    assert set(listed) == set(manifest.QUERIES)
    # ...each carrying the scope, which is what says whether `--project` is wanted...
    assert listed["agent_types"] == ["corpus"]
    # ...and the parameters with no default, in the order the manifest declares them.
    assert listed["view_runs"] == ["keyed", "session_id", "chip_chars"]
    assert listed["records_slice"] == ["keyed", "session_id", "source", "first_line", "last_line"]


def test_a_corpus_query_needs_a_project_and_a_keyed_one_refuses_it(
    run_query: QueryRunner,
) -> None:
    """Which of `--project` and `--since` a query takes follows from what its statement reads.

    A query counting across sessions reads the `project_sessions` the runner builds, so it
    cannot run without one. A keyed query reads no such thing, and a corpus predicate on
    `WHERE session_id = $session_id` would narrow nothing while reading as if it had. Both
    refusals are the query's scope, which is the one fact about a run nobody types.
    """
    # If a corpus query runs with no `--project`, it is refused rather than counting the
    # whole store — a total over every project in it answers nobody's question...
    with pytest.raises(SystemExit, match="agent_types counts across sessions"):
        run_query("agent_types")
    # ...and if a keyed query is handed one, that is refused too, naming the query: a flag
    # silently ignored is a citation quoting a corpus the rows were never scoped to.
    with pytest.raises(SystemExit, match="session_overview is keyed"):
        run_query("session_overview", "--param", f"session_id={SPINE}", "--project", MYCELIA)


def test_an_unknown_query_or_parameter_names_what_it_did_not_recognize(
    run_query: QueryRunner,
) -> None:
    """The runner refuses what it cannot bind rather than running something else."""
    # If the query does not exist...
    with pytest.raises(SystemExit, match="no_such_query"):
        run_query("no_such_query", "--project", MYCELIA)
    # ...or a `--param` names something the query never declared, the message says which —
    # a silently ignored parameter produces a plausible wrong number and no signal.
    with pytest.raises(SystemExit, match="nonsense"):
        run_query("sessions", "--project", MYCELIA, "--param", "nonsense=1")


def test_a_parameter_type_nothing_binds_is_refused_rather_than_bound_to_null(
    corpus_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ParamType` the binder does not parse is an error, not a silent SQL NULL.

    A fourth type is a plausible next change — a boolean, a list — and the match that parses
    one covered three arms and then fell off the end, which in Python hands back `None`. The
    query would have run with NULL where the reader's value belonged, and the citation would
    have reported it as bound: a wrong answer with nothing marking it.

    Planted rather than added to the enum, because what this holds is the arm that catches a
    member this build knows nothing about.
    """
    monkeypatch.setattr(queries, "QUERY_DIR", tmp_path)
    monkeypatch.setitem(
        manifest.QUERIES,
        "planted",
        queries.Query(
            scope=queries.Scope.KEYED,
            params={
                "flag": queries.Param(
                    type=cast(queries.ParamType, "boolean"), default=queries.REQUIRED
                )
            },
        ),
    )
    (tmp_path / "planted.sql").write_text("SELECT $flag AS flag")
    # The refusal names the type it could not bind, so the fix is the binder and not the call.
    with pytest.raises(SystemExit, match="boolean"):
        query(corpus_db, capsys, "planted", "--param", "flag=true")


@pytest.mark.parametrize(
    ("held", "remedy"),
    # One vintage a write open would carry forward, and one nothing reaches.
    [
        (SCHEMA_VERSION - 1, MIGRATE_REMEDY),
        (SCHEMA_VERSION + 1, SCHEMA_MISMATCH_REMEDY),
    ],
    ids=["migratable", "unreachable"],
)
def test_a_store_from_another_schema_is_refused_with_the_remedy_that_fits_it(
    corpus_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], held: int, remedy: str
) -> None:
    """A store these queries were not written against is refused, with what to do about it."""
    # If the store holds a schema version this build does not read — stamped onto a copy,
    # since every fixture store is written by the current schema...
    path = tmp_path / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    with duckdb.connect(str(path)) as connection:
        connection.execute("UPDATE meta SET schema_version = ?", [held])
    # ...then the query refuses rather than reading tables it may not understand, and names
    # the one action that fits this store. A reader told to extract into a fresh one when a
    # migration would have done can destroy the only copy of a session Claude Code pruned.
    with pytest.raises(SystemExit) as refused:
        query(path, capsys, "sessions", "--project", MYCELIA)
    assert remedy in str(refused.value)


def test_a_db_path_with_no_store_behind_it_names_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo in `--db` is the runner's error to report, not DuckDB's to raise."""
    # If `--db` names a path nothing extracted to — the store is never created by a reader...
    missing = tmp_path / "nothing.duckdb"
    # ...then the command exits saying which path and what to run, rather than crashing out
    # of the opener with an I/O error naming a file the operator never asked about.
    with pytest.raises(SystemExit) as refused:
        query(missing, capsys, "sessions", "--project", MYCELIA)
    assert str(missing) in str(refused.value)
    assert "hp extract" in str(refused.value)


def test_the_store_is_opened_read_only(
    corpus_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No query can write to the store, whatever its SQL says."""
    # If a query file asks for DDL (planted here — no shipped query does)...
    monkeypatch.setattr(queries, "QUERY_DIR", tmp_path)
    monkeypatch.setitem(
        manifest.QUERIES, "ddl", queries.Query(scope=queries.Scope.KEYED, params={})
    )
    (tmp_path / "ddl.sql").write_text("CREATE TABLE planted (a INTEGER);")
    before = _tables(corpus_db)
    # ...then running it raises...
    with pytest.raises(duckdb.Error):
        query(corpus_db, capsys, "ddl")
    # ...and the store is exactly as it was: the analysis layer is out of the mutation
    # business by construction, not by convention.
    assert _tables(corpus_db) == before


def _bindings(output: Output) -> dict[str, str]:
    """The `k=v` pairs of a citation line, which under `--csv` sits on stderr."""
    citation = next(line for line in output.stderr.splitlines() if line.startswith("-- queries/"))
    return dict(pair.split("=", 1) for pair in citation.split()[2:])


def _tables(db: Path) -> list[tuple[str]]:
    connection = duckdb.connect(str(db), read_only=True)
    try:
        return connection.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall()
    finally:
        connection.close()
