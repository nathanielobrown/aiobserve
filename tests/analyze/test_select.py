"""Which sessions and runs an iteration reads, drawn at fixture-sized quotas.

Selection is the one part of the process that decides what nobody will look at, so the
leaves here pin the mechanics a report's realized composition is built from: strata fill in
order, each walks down past what an earlier stratum took, a stratum whose metric runs out
stops short rather than padding, and the slots nobody used fall through to discovery.

Every quota is bound small — the fixture pool is ten sessions — except the last leaf, which
pins the production defaults a committed report cites.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from aiobserve.analyze.queries import QUERIES
from tests.analyze.conftest import (
    AGENT_TYPES,
    ANCESTOR,
    AS_OF_PARTIAL,
    AS_OF_WHOLE,
    DEEP_RESEARCH_SESSION,
    FORK_ORIGIN,
    MYCELIA,
    NO_WORK_SESSIONS,
    POOL_AT_PARTIAL,
    POOL_AT_WHOLE,
    REGISTRY_ZOO,
    SERVER_TOOLS,
    SPINE,
    Output,
    QueryRunner,
    analyze_transcripts,
    query,
)
from tests.conftest import build_store

# A selected session as the report reads it: the stratum that took it, and which session.
Pick = tuple[str, str]

COST = "cost"
ERRORS = "tool-errors"
COMPACTIONS = "compactions"
DISCOVERY = "discovery"
GRILL_ME = "skill:grill-me"

# Quotas small enough that a ten-session pool can show a stratum running out. Each leaf
# overrides the ones it is about; anything it leaves alone stays off, so the set it asserts
# on is only the mechanism it names.
OFF: dict[str, int | str] = {
    "cost_quota": 0,
    "error_quota": 0,
    "compaction_quota": 0,
    "discovery_quota": 0,
    # Above every fixture skill's user count, so no skill qualifies unless a leaf lowers it.
    "skill_threshold": 99,
}


def test_the_same_bindings_select_the_same_sessions_however_the_store_was_built(
    run_query: QueryRunner, reversed_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One store and one set of bindings give one selection — sessions, tags, and order."""

    def reversed_query(name: str, *arguments: str) -> Output:
        return query(reversed_db, capsys, name, *arguments)

    # If the same bindings are drawn twice from one store, and once from a store built by
    # extracting the same transcripts in the opposite order...
    bindings: dict[str, int | str] = {
        "cost_quota": 2,
        "error_quota": 1,
        "compaction_quota": 1,
        "discovery_quota": 2,
        "skill_threshold": 2,
    }
    first = _select(run_query, bindings)
    second = _select(run_query, bindings)
    reversed_build = _select(reversed_query, bindings)
    # ...then all three are the same list: reproducibility is the whole claim the selection
    # makes, and a draw that depended on insertion order would break here and nowhere else.
    assert first == second == reversed_build
    # ...and the ranked strata took what their rankings say, with `grill-me`'s only two pool
    # users already taken by cost, so its slot fell through to discovery.
    assert first[:4] == [
        (COST, SPINE),
        (COST, SERVER_TOOLS),
        (ERRORS, FORK_ORIGIN),
        (COMPACTIONS, ANCESTOR),
    ]
    assert [stratum for stratum, _ in first[4:]] == [DISCOVERY] * 3


def test_a_later_stratum_walks_past_what_an_earlier_one_took(run_query: QueryRunner) -> None:
    """A session an earlier stratum took does not spend a later stratum's quota."""
    # If the cost stratum's four take `ANCESTOR`, one of only two sessions that compacted...
    picks = _select(run_query, {"cost_quota": 4, "compaction_quota": 1})
    assert (COST, ANCESTOR) in picks
    # ...then the compaction stratum walks down to the other one and still meets its quota,
    # rather than reporting a session the cost stratum already accounted for.
    assert [pick for pick in picks if pick[0] == COMPACTIONS] == [(COMPACTIONS, REGISTRY_ZOO)]


def test_a_ranked_stratum_takes_only_nonzero_sessions_and_stops_short(
    run_query: QueryRunner,
) -> None:
    """A stratum whose metric runs out returns fewer sessions instead of padding to quota."""
    # If only two pool sessions hold an error tool call and the quota asks for three...
    picks = _select(run_query, {"error_quota": 3})
    # ...then the stratum stops at two, and the eight error-free pool sessions carry no
    # `tool-errors` tag: the tag is what a report's realized composition is counted from, and
    # a stratum that padded to quota would make every one of those counts a lie.
    assert [pick for pick in picks if pick[0] == ERRORS] == [
        (ERRORS, SERVER_TOOLS),
        (ERRORS, FORK_ORIGIN),
    ]
    # ...while the slot it could not fill is spent, not lost — the next leaf is about where.
    assert [stratum for stratum, _ in picks] == [ERRORS, ERRORS, DISCOVERY]


def test_an_unused_ranked_slot_falls_through_to_discovery(run_query: QueryRunner) -> None:
    """Slots a ranked stratum could not fill are drawn at random rather than lost."""
    # If the error stratum leaves one of its three slots unused and discovery asks for two...
    picks = _select(run_query, {"error_quota": 3, "discovery_quota": 2})
    # ...then discovery draws three, and the set is the quota sum it was given...
    assert [stratum for stratum, _ in picks].count(DISCOVERY) == 3
    assert len(picks) == 3 + 2
    # ...while a discovery quota larger than the pool stops at the pool, not in a loop.
    exhausted = _select(run_query, {"error_quota": 3, "discovery_quota": 20})
    assert len(exhausted) == POOL_AT_WHOLE


def test_a_stratum_ranks_by_its_metric_then_by_session_id(run_query: QueryRunner) -> None:
    """Sessions tied on a stratum's metric are ordered by session id, so the draw is fixed."""
    # If the two sessions that compacted both compacted once, the tie is all that decides...
    picks = _select(run_query, {"compaction_quota": 1})
    # ...and the lower session id is taken. Without the tiebreak the draw is whatever the
    # storage layer felt like returning that day.
    assert picks == [(COMPACTIONS, ANCESTOR)]
    assert ANCESTOR < REGISTRY_ZOO


def test_a_major_skill_is_one_used_across_sessions_not_one_used_often(
    run_query: QueryRunner,
) -> None:
    """A skill qualifies on how many sessions used it, not how many calls it made."""
    # If `pr-and-document` made four calls inside one pool session while `grill-me` made two
    # across two, and the threshold is two sessions...
    picks = _select(run_query, {"skill_threshold": 2})
    # ...then only `grill-me` earns a slot, and it takes its most recent user. A call-counting
    # implementation ranks `pr-and-document` first and reads the wrong session.
    assert picks == [(GRILL_ME, SPINE)]


def test_skills_are_iterated_in_name_order_each_taking_its_most_recent_user(
    run_query: QueryRunner,
) -> None:
    """Every major skill gets a reader, walking down its own users past what is taken."""
    # If the cost stratum takes `SPINE` — the most recent user of both `grill-me` and
    # `night-run` — and every fixture skill qualifies...
    picks = _select(run_query, {"cost_quota": 1, "skill_threshold": 1})
    skills = [pick for pick in picks if pick[0].startswith("skill:")]
    # ...then the skills are walked in name order, `grill-me` falls to its other user, and
    # `night-run`, whose only user is already selected, contributes nothing.
    assert skills == [
        ("skill:deep-research", DEEP_RESEARCH_SESSION),
        (GRILL_ME, SERVER_TOOLS),
        ("skill:pr-and-document", ANCESTOR),
    ]


def test_a_skill_whose_users_are_all_selected_gives_its_slot_to_discovery(
    run_query: QueryRunner,
) -> None:
    """A skill with nothing left to offer costs the iteration a slot, not a session."""
    # If the cost stratum's two take both of `grill-me`'s pool users...
    picks = _select(run_query, {"cost_quota": 2, "discovery_quota": 1, "skill_threshold": 2})
    # ...then no skill row appears, and the skill's slot turns up in discovery: the budget
    # the citation reports is still the budget that was read.
    assert not [pick for pick in picks if pick[0].startswith("skill:")]
    assert [stratum for stratum, _ in picks] == [COST, COST, DISCOVERY, DISCOVERY]


def test_discovery_is_a_function_of_its_seed_and_never_re_picks(run_query: QueryRunner) -> None:
    """The random stratum is reproducible from its seed and disjoint from the ranked draw."""
    # If the same seed is drawn twice and a different one once...
    first = _select(
        run_query, {"cost_quota": 2, "error_quota": 1, "discovery_quota": 3, "seed": "a"}
    )
    again = _select(
        run_query, {"cost_quota": 2, "error_quota": 1, "discovery_quota": 3, "seed": "a"}
    )
    other = _select(
        run_query, {"cost_quota": 2, "error_quota": 1, "discovery_quota": 3, "seed": "b"}
    )
    # ...then the seed alone decides the draw...
    assert first == again
    assert first != other
    # ...and discovery draws from what the ranked strata left, so no session is read twice.
    for picks in (first, other):
        ranked = {session for stratum, session in picks if stratum != DISCOVERY}
        discovered = {session for stratum, session in picks if stratum == DISCOVERY}
        assert len(discovered) == 3
        assert not (ranked & discovered)


def test_a_session_that_did_no_work_of_its_own_is_outside_the_pool(
    run_query: QueryRunner,
) -> None:
    """Sessions with no turns and no agent runs are unreadable, so no stratum reaches them."""
    # If discovery is asked for more sessions than the pool holds, every stratum runs dry...
    picks = _select(run_query, {"cost_quota": 4, "compaction_quota": 2, "discovery_quota": 20})
    # ...and what comes back is the pool itself — which excludes the three sessions whose
    # work belongs to another session, two of them despite having compacted.
    assert len(picks) == POOL_AT_WHOLE
    assert not ({session for _, session in picks} & set(NO_WORK_SESSIONS))


def test_the_selection_window_rides_as_of(run_query: QueryRunner) -> None:
    """Moving the as-of date moves the pool the draw is made from, and nothing else."""
    # If the same bindings are drawn against a window covering all thirteen sessions and then
    # one opening mid-corpus...
    bindings: dict[str, int | str] = {"cost_quota": 3, "discovery_quota": 20}
    whole = {session for _, session in _select(run_query, bindings)}
    partial = {session for _, session in _select(run_query, bindings, as_of=AS_OF_PARTIAL)}
    # ...then the second draw is made entirely from the smaller pool, and stops at its size.
    assert len(whole) == POOL_AT_WHOLE
    assert len(partial) == POOL_AT_PARTIAL
    assert partial < whole


def test_the_production_quotas_are_the_designed_reading_budget() -> None:
    """A bare selection run draws the budget the committed reports quote."""
    # Every other leaf here binds fixture-sized values, so this is the only thing standing
    # between an edited quota and a report citing a number nobody ran.
    defaults = {name: spec.default for name, spec in QUERIES["select_sessions"].params.items()}
    assert defaults == {
        "cost_quota": 8,
        "error_quota": 5,
        "compaction_quota": 4,
        "discovery_quota": 8,
        "skill_threshold": 5,
        "seed": "aiobserve",
    }
    # The run draw rides the same pin: its floor is what keeps a corpus of one-off agent
    # names from turning a ~20-run reading budget into one run per name.
    assert {name: spec.default for name, spec in QUERIES["select_runs"].params.items()} == {
        "runs_per_stratum": 1,
        "min_runs": 5,
    }


def test_every_agent_type_gives_up_its_worst_and_its_costliest_run(
    run_query: QueryRunner,
) -> None:
    """A commonly used agent definition is read every iteration, through its furthest runs."""
    # If the corpus holds seven runs across seven distinct agent types, one of which hit a
    # tool error, and the threshold is set low enough to admit all seven...
    output = run_query(
        "select_runs",
        "--project",
        MYCELIA,
        "--as-of",
        AS_OF_WHOLE,
        "--param",
        "min_runs=1",
        "--csv",
    )
    rows = list(zip(output.column("stratum"), output.column("agent_type"), strict=True))
    # ...then every type contributes exactly one run, tagged with the stratum that took it:
    # the errored run by its errors, and the rest by what they spent.
    assert len(rows) == AGENT_TYPES
    assert sorted(agent_type for _, agent_type in rows) == sorted(
        {agent_type for _, agent_type in rows}
    )
    assert sorted(rows) == sorted(
        [("run-errors", "fork")]
        + [
            ("run-cost", agent_type)
            for agent_type in (
                "Explore",
                "architect",
                "auditor",
                "claude",
                "general-purpose",
                "workflow-subagent",
            )
        ]
    )
    # ...but `agent_type` is an open set — a session names its own subagents, and a name used
    # once is not a definition worth a reading slot every iteration. Raise the threshold above
    # every fixture type's run count and the same draw over the same runs comes back empty.
    quiet = run_query(
        "select_runs",
        "--project",
        MYCELIA,
        "--as-of",
        AS_OF_WHOLE,
        "--param",
        "min_runs=2",
        "--csv",
    )
    assert len(quiet.csv_rows()) <= 1


@pytest.fixture(scope="session")
def reversed_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same corpus, extracted in the opposite order — a different insertion order."""
    path = tmp_path_factory.mktemp("reversed") / "traces.duckdb"
    build_store(path, tuple(reversed(analyze_transcripts())))
    return path


def _select(
    run: QueryRunner, bindings: Mapping[str, int | str], *, as_of: str = AS_OF_WHOLE
) -> list[Pick]:
    """`select_sessions` at fixture-sized quotas, as the (stratum, session) list it returns."""
    bound = {**OFF, **bindings}
    arguments = [part for name, value in bound.items() for part in ("--param", f"{name}={value}")]
    output = run("select_sessions", "--project", MYCELIA, "--as-of", as_of, "--csv", *arguments)
    return list(zip(output.column("stratum"), output.column("session_id"), strict=True))
