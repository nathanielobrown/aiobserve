"""The library smoke tier: every shipped `.sql` runs, and every one obeys the house rules.

Discovery, not enumeration — the leaves parametrize over `queries/*.sql`, so a query added
by any consumer of the library (the analysis process, the viewer) is covered the moment it
lands, and one shipped without a manifest entry fails here rather than at a reader's prompt.

`FIXTURE_BINDINGS` is where a query says what to bind on a 16-session store. A query whose
manifest marks a parameter required must appear there, or its leaf fails naming it.
"""

import re

import pytest

from aiobserve.analyze import queries
from aiobserve.analyze.queries import QUERIES, Scope
from aiobserve.analyze.runner import CORPUS_RELATIONS
from aiobserve.enrich.store import LEVELS
from aiobserve.export.duckdb import TABLES
from tests.analyze.conftest import AS_OF_WHOLE, QueryRunner
from tests.conftest import (
    ANCESTOR,
    CONFIG_ONLY,
    DENSE_CALL,
    DENSE_CALL_TURN,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    MYCELIA,
    OFFLOAD_FILE,
    RESUME,
    RESUME_LONG_RECORD,
    SERVER_TOOLS,
    SLASH_TURN,
    SPINE,
    SPINE_RUN,
)

# Bindings that make a query return something on the fixture corpus, per query name. The
# production defaults are pinned by their own leaves; these are the fixture-sized values.
FIXTURE_BINDINGS: dict[str, dict[str, str]] = {
    # The production floor of 3 sessions holds no pair on a 16-session store, so the smoke
    # run would exercise the filter and never the join under it.
    "co_occurrence": {"min_sessions": "1"},
    # Every fixture agent type ran exactly once, so the production floor of 5 admits none.
    "select_runs": {"min_runs": "1"},
    # Redaction leaves every recorded command line as `[redacted]`, and the handful that
    # survive into the corpus views sit below the production floor of 5.
    "command_failures": {"min_occurrences": "1"},
    # Where to split the fixture corpus's two idle reloads, which followed silences of 6,035
    # and 23,773 seconds: anything between them puts one on each side of the bound.
    "reload_cost_split": {"short_gap_seconds": "10000"},
    # One of the two fixture sessions holding a failed tool call.
    "error_records": {"session_id": SERVER_TOOLS},
    # Both fixture errors are one-offs, so the production floor of 5 lists neither.
    "error_signatures": {"min_occurrences": "1"},
    # Redaction cuts every recorded `file_path` to `[redacted]`, so the corpus holds one
    # directory — the bucket for a path with none — and no failing call in it at all.
    "path_failures": {"min_occurrences": "0"},
    "records_slice": {"session_id": RESUME, "source": MAIN, "first_line": "1", "last_line": "5"},
    "run_timeline": {"session_id": SPINE, "source": SPINE_RUN},
    "session_timeline": {"session_id": SPINE},
    "session_overview": {"session_id": SPINE},
    # `spine/` is the fixture session with agent runs; its main thread never compacted, so
    # the compaction markers come from the session that did.
    "view_compactions": {"session_id": ANCESTOR, "source": MAIN},
    "view_run_header": {"session_id": SPINE, "run_id": SPINE_RUN},
    "view_runs": {"session_id": SPINE},
    "view_session_header": {"session_id": SPINE},
    # `spine/` failed nothing, so the errors list is bound at one of the two fixture sessions
    # that did — the one whose failure sits on a run thread rather than on `main`, which is
    # the shape the session-wide list exists for.
    "view_session_errors": {"session_id": FORK_ORIGIN},
    # The tree levels beside a node page, bound at the session the tree tests open and
    # at the turn under it holding 4 api calls, so each level answers with more than one row.
    "view_tree_turns": {"session_id": ANCESTOR, "source": MAIN},
    "view_tree_calls": {"session_id": ANCESTOR, "source": MAIN, "turn_id": DENSE_TURN},
    # Bound at one api call, which is the level under a call; the turn is what the other
    # question binds — every tool call under a turn, the level `noapi` puts there — and the
    # CLI has no way to send the NULL that asks it, so this run exercises the first.
    "view_tree_tools": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "api_call_id": DENSE_CALL,
        "turn_id": DENSE_CALL_TURN,
    },
    # One node read whole, one per kind that has fields of its own.
    "view_turn_header": {"session_id": ANCESTOR, "source": MAIN, "turn_id": DENSE_TURN},
    "view_call_header": {"session_id": ANCESTOR, "source": MAIN, "api_call_id": DENSE_TURN_CALL},
    "view_tool_header": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "tool_call_id": DENSE_TOOL,
    },
    # The viewer's drill-down, bound at the corpus's densest shapes so each query answers
    # with more than one row: the turn holding 4 api calls, and the call holding 4 tools.
    "view_turn_calls": {"session_id": ANCESTOR, "source": MAIN, "turn_id": DENSE_TURN},
    "view_call_tools": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "api_call_id": DENSE_CALL,
    },
    # The numbers behind one tree row. Bound at a turn rather than at a session, because the
    # turn is the one kind whose delta is measured against a sibling — the arm with a window
    # function under it — and at the thread the tree tests open, where that turn has one before
    # it to be measured against.
    "view_numbers": {
        "session_id": SPINE,
        "source": MAIN,
        "node_id": SLASH_TURN,
        "kind": "turn",
    },
    "view_numbers_tool": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "tool_call_id": DENSE_TOOL,
    },
    # The records browser, at the corpus's densest recorded thread — 47 archived records, so
    # the default page of 100 answers with more than one row and the turn join with several.
    "view_records": {"session_id": ANCESTOR, "source": MAIN},
    "view_turn_records": {"session_id": ANCESTOR, "source": MAIN},
    # The corpus holds exactly one offloaded tool result, and this is it.
    "view_offload": {"session_id": CONFIG_ONLY, "name": OFFLOAD_FILE},
    # The per-value queries answer with one row apiece, whatever is bound.
    "view_call_text": {"session_id": ANCESTOR, "source": MAIN, "api_call_id": DENSE_TURN_CALL},
    "view_call_thinking": {"session_id": ANCESTOR, "source": MAIN, "api_call_id": DENSE_TURN_CALL},
    "view_tool_input": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "tool_call_id": DENSE_TOOL,
    },
    # The command arm answers NULL off a call that is not a `Bash` call, which is a row and
    # not a failure — the smoke run asks whether the query runs.
    "view_tool_command": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "tool_call_id": DENSE_TOOL,
    },
    "view_tool_result": {
        "session_id": FORK_ORIGIN,
        "source": FORK_ORIGIN_RUN,
        "tool_call_id": DENSE_TOOL,
    },
    "view_turn_prompt": {"session_id": ANCESTOR, "source": MAIN, "turn_id": DENSE_TURN},
    # A turn the corpus records a command on, so the value comes back as one a reader reads
    # rather than as the NULL every turn nobody typed a slash at holds.
    "view_turn_command_args": {"session_id": SPINE, "source": MAIN, "turn_id": SLASH_TURN},
    "view_run_brief": {"session_id": SPINE, "run_id": SPINE_RUN},
    # A run the corpus records a spawning `Agent` call for, so both values come back as the
    # strings a pane previews rather than as the NULL a run with no spawning call holds.
    "view_run_prompt": {"session_id": SPINE, "run_id": SPINE_RUN},
    "view_run_result": {"session_id": SPINE, "run_id": SPINE_RUN},
    "view_record": {"session_id": RESUME, "source": MAIN, "line_no": str(RESUME_LONG_RECORD)},
    # The enrichment family, at the fixture session the plant describes at every level and
    # the level holding the most planted rows.
    "enrichment_digest": {"session_id": SPINE},
    "select_enrichments": {"level": "agent_run"},
    # The landing page's clock. Inside the corpus's own dates, so both trailing windows hold
    # sessions on a store whose recordings recede: the fixture sessions ran up to 2026-08-06.
    "view_project_rollups": {"as_of": "2026-07-28"},
    # The viewer's own read of the three tables, at the thread a session page renders: the
    # plant describes `spine/` at every level, so all three arms of the union answer.
    "view_enrichment": {"session_id": SPINE, "source": MAIN},
    # The whole of one line the pass wrote, one query per level — bound at the same session,
    # which the plant describes at every level, and at a turn and a run under it.
    "view_turn_said": {"session_id": SPINE, "source": MAIN, "turn_id": SLASH_TURN},
    "view_run_said": {"session_id": SPINE, "run_id": SPINE_RUN},
    "view_session_said": {"session_id": SPINE},
}

# The relations only a store an enrichment pass has written to holds: the pipeline creates
# none of them, so a query reading one runs against the planted store instead of the bare
# corpus. Derived from the level table map rather than listed, so a fourth level is covered.
ENRICHMENT_TABLES = {spec.table for spec in LEVELS.values()}
ENRICHMENT_VIEWS = "enriched_"

# The clock a query file may not read: a `current_date` filter goes green on a frozen
# fixture store today and returns nothing next month.
CLOCK = ("current_date", "current_timestamp", "now", "today", "get_current_timestamp")

NAMES = sorted(path.stem for path in queries.QUERY_DIR.glob("*.sql"))


def statement(name: str) -> str:
    """One query's SQL with its comments cut — every rule below reads what runs."""
    return re.sub(r"--[^\n]*", "", queries.load(name))


def identifiers(name: str) -> set[str]:
    """Every bare identifier in one query file, for the static rules below."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", statement(name)))


def relations(name: str) -> set[str]:
    """What a query reads: the identifier after each FROM or JOIN, CTE names included.

    A rollup column is named after the table it counts (`turns`, `api_calls`), so a bare
    identifier scan cannot tell a table read from a column selected.
    """
    return set(re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", statement(name)))


def reads_enrichment(name: str) -> bool:
    """Whether a query needs a store an enrichment pass has already written to."""
    read = relations(name)
    return bool(read & ENRICHMENT_TABLES) or any(
        relation.startswith(ENRICHMENT_VIEWS) for relation in read
    )


def declared_parameters(name: str) -> set[str]:
    """The `$name` parameters the SQL text itself references."""
    return set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", statement(name)))


@pytest.mark.parametrize("name", NAMES)
def test_every_query_runs(name: str, run_query: QueryRunner, enriched_query: QueryRunner) -> None:
    """Every shipped query executes against a real store — an empty result is fine."""
    query = QUERIES[name]
    runner = enriched_query if reads_enrichment(name) else run_query
    # If a parameter is required with no default, this tier has to say what to bind...
    bindings = FIXTURE_BINDINGS.get(name, {})
    for parameter, spec in query.params.items():
        assert spec.default is not queries.REQUIRED or parameter in bindings, (
            f"{name} requires ${parameter}: add it to FIXTURE_BINDINGS"
        )
    arguments = [part for key, value in bindings.items() for part in ("--param", f"{key}={value}")]
    if query.scope is Scope.CORPUS:
        # `--as-of` defaults to today, and the runner's trailing window is 28 days wide, so
        # an unbound run asks a frozen corpus a question about the last four weeks. Every
        # fixture session recedes past that edge on its own schedule, which turns each
        # windowed query into a time bomb: `select_runs` went red the morning the last
        # session carrying agent runs aged out. Pinned at the `$as_of` that opens the
        # window before the earliest fixture session, so the whole corpus stays in view.
        arguments += ["--project", MYCELIA, "--as-of", AS_OF_WHOLE]
    # ...and the run completes, which is what catches a query a schema bump broke...
    printed = runner(name, "--csv", *arguments)
    # ...having answered with rows. A query that returns nothing on this corpus runs green
    # while asking its question of no data at all, which is the failure this tier is for.
    assert len(printed.csv_rows()) > 1, f"{name} returned no rows: bind it in FIXTURE_BINDINGS"


def test_every_query_file_has_a_manifest_entry() -> None:
    """The manifest and the directory hold the same set of queries."""
    assert sorted(QUERIES) == NAMES


def test_a_citation_with_nothing_bound_ends_at_the_query_file() -> None:
    """A citation is a line someone pastes into a report, so it never trails whitespace."""
    # Every shipped query resolves at least one binding, so this is the contract for a caller
    # that composes its own — the viewer builds citations from what it bound, not a manifest.
    assert queries.citation("sessions", {}) == "-- queries/sessions.sql"


@pytest.mark.parametrize("name", NAMES)
def test_the_manifest_declares_exactly_the_parameters_the_sql_uses(name: str) -> None:
    """No parameter goes unbound, and no manifest entry describes one that is gone."""
    assert declared_parameters(name) == set(QUERIES[name].params)


@pytest.mark.parametrize("name", NAMES)
def test_a_cross_session_query_counts_through_the_corpus_views(name: str) -> None:
    """A corpus query reads `corpus_*`, so a resumed session is counted once."""
    if QUERIES[name].scope is not Scope.CORPUS:
        pytest.skip("keyed queries fetch one session's own rows")
    read = relations(name)
    # The `live_*` family counts a resume's copied rows twice across sessions, and a base
    # table counts a fork's replays as well. A corpus query reads neither: it joins the
    # `corpus_*` views to one of the relations the runner builds from `--project`.
    assert not {word for word in read if word.startswith("live_")}
    assert not (read & set(TABLES))
    assert read & set(CORPUS_RELATIONS)


@pytest.mark.parametrize("name", NAMES)
def test_a_cost_is_never_reported_without_its_unpriced_count(name: str) -> None:
    """A cost total says how many calls our price table left out."""
    used = identifiers(name)
    if "cost_usd" in used:
        assert "unpriced_api_calls" in used


@pytest.mark.parametrize("name", NAMES)
def test_no_query_reads_the_clock(name: str) -> None:
    """Anything time-relative rides `$as_of`, so a frozen store answers the same tomorrow."""
    assert not (identifiers(name) & set(CLOCK))
