"""The `aiobserve query` runner: what it selects, what it binds, and what it cites.

Nothing is mocked — every leaf drives `cli.main("query", …)` against a real DuckDB built
from recorded fixtures, and reads the two streams apart, because which stream a line lands
on is itself a contract a piped analysis depends on.
"""

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from aiobserve.analyze import queries
from aiobserve.export.duckdb import SCHEMA_VERSION
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


def test_a_trailing_slash_on_the_project_changes_nothing(run_query: QueryRunner) -> None:
    """`--project path/` and `--project path` name the same corpus."""
    assert (
        run_query("sessions", "--project", f"{MYCELIA}/", "--csv").csv_rows()
        == run_query("sessions", "--project", MYCELIA, "--csv").csv_rows()
    )


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
    # `$as_of` as its date, because a citation a reader cannot rebind is not a citation.
    citation = table.stdout.splitlines()[0]
    assert "queries/sessions.sql" in citation
    assert f"project={MYCELIA}" in citation
    assert f"as_of={AS_OF_PARTIAL}" in citation
    assert f"window_days={queries.WINDOW_DAYS}" in citation
    # ...and under `--csv` the same line moves to stderr, leaving stdout machine-readable.
    piped = run_query("sessions", "--project", MYCELIA, "--as-of", AS_OF_PARTIAL, "--csv")
    assert citation in piped.stderr
    assert "queries/sessions.sql" not in piped.stdout


def test_as_of_defaults_to_today(run_query: QueryRunner) -> None:
    """A bare run cites the date its window was measured back from."""
    result = run_query("sessions", "--project", MYCELIA)
    assert f"as_of={dt.date.today().isoformat()}" in result.stdout.splitlines()[0]


def test_since_filters_and_omitting_it_means_the_whole_corpus(run_query: QueryRunner) -> None:
    """`--since` cuts the corpus at a date; with no `--since` there is no cut."""
    # If six mycelia sessions started on or after 2026-07-15...
    since = run_query("sessions", "--project", MYCELIA, "--since", "2026-07-15", "--csv")
    assert len(since.column("session_id")) == 6
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


def test_a_store_from_another_schema_is_refused_and_sends_the_reader_to_the_guide(
    corpus_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store these queries were not written against is refused, with what to do about it."""
    # If the store holds a schema version this build does not read — stamped onto a copy,
    # since every fixture store is written by the current schema...
    path = tmp_path / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    with duckdb.connect(str(path)) as connection:
        connection.execute("UPDATE meta SET schema_version = ?", [SCHEMA_VERSION - 1])
    # ...then the query refuses rather than reading tables it may not understand, and points
    # at the store guide — a reader told to delete the store instead can destroy the only
    # copy of a session Claude Code has since pruned from disk.
    with pytest.raises(SystemExit, match="docs/store.md"):
        query(path, capsys, "sessions", "--project", MYCELIA)


def test_the_store_is_opened_read_only(
    corpus_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No query can write to the store, whatever its SQL says."""
    # If a query file asks for DDL (planted here — no shipped query does)...
    monkeypatch.setattr(queries, "QUERY_DIR", tmp_path)
    monkeypatch.setitem(queries.QUERIES, "ddl", queries.Query(scope=queries.Scope.KEYED, params={}))
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
