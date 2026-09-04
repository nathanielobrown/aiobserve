"""The corpus descriptions: what kind of session each one was, and what each thing cost.

These six queries answer "describe this corpus" rather than "count this event". A description
is only worth citing if the reader can see the rule behind it, so the leaves here are about
the rules: which sessions a bound threshold moves between shapes, whose rows a per-run number
counts, and what a share of spend is a share of.

Two of the rules cannot bite on the recorded corpus at all — it holds no `Edit`, `Write` or
`NotebookEdit` call and no `Skill` call — so those leaves plant one onto real rows and say so.
Both absences are asserted before the plant, because an unreachable arm is worth knowing about.
"""

import json
from pathlib import Path

import duckdb
import pytest

from tests.analyze.conftest import (
    AGENT_TYPES,
    AS_OF_WHOLE,
    MYCELIA_SESSIONS,
    Output,
    QueryRunner,
    mappings,
    query,
    scalar,
)
from tests.conftest import FORK_ORIGIN, MYCELIA, SPINE, SPINE_LEAF, SPINE_RUN

# How the recorded corpus classifies under the manifest's own thresholds, measured on
# 2026-08-27 by building the fixture store: four of the seven shapes, over all 15 mycelia
# sessions. The rebindings below are read against this.
RECORDED_SHAPES = {
    "conversational": 7,
    "no-work": 3,
    "read-only-analysis": 4,
    "skill-orchestrated": 2,
}

# The three sessions `corpus_rollups` credits with no turns and no agent runs, and what makes
# them worth a shape of their own: one compacted, so a no-work session can be one that did work
# for a thread elsewhere rather than one where nothing happened.
NO_WORK_SESSIONS = 3
NO_WORK_COMPACTIONS = 1

# The session the editing plant lands on, and what the plant is worth: `FORK_ORIGIN` holds 8
# recorded `Read` calls, of which the corpus views keep 4 — its fork replays the other half
# under a second source, and a replayed call is not the corpus's to count twice.
PLANTED_EDIT_CALLS = 4
# Where the plant puts that session as `$editing_calls` moves across it. At 5 the ladder runs
# off the end of its named shapes, which no binding over the recorded corpus can reach: a
# session with edit calls is neither read-only nor, at 7 tool calls, conversational.
EDITING_AT_OR_BELOW = "solo-editing"
EDITING_ABOVE = "mixed"

# The skills the recorded corpus attributes api calls to, and how many each carries. No fixture
# records a `Skill` tool call, so every one of these is invoked zero times — the halves of this
# query are independent, which is the reason it joins them rather than reading either alone.
RECORDED_SKILLS = {
    "pr-and-document": (4, 1),
    "grill-me": (2, 2),
    "deep-research": (2, 1),
    "manager": (1, 1),
    "night-run": (2, 1),
}
# The skills the plant invokes: one the corpus already attributes calls to, so the two halves
# meet in one row; one it does not, so the invoked half stands alone at zero api calls.
INVOKED_ATTRIBUTED = "grill-me"
INVOKED_ALONE = "commit"
# A `Skill` call whose input is not JSON, which the query keeps under a NULL skill rather than
# filtering away — a shape change should arrive as a row a reader can see.
UNREADABLE_INPUT = "not json at all"

# The command turn the corpus records with any spend of its own: one turn of `SPINE`, holding
# two api calls and the three tool calls under them.
BILLED_COMMAND = "/night-run"
BILLED_API_CALLS = 2
BILLED_TOOL_CALLS = 3

# What the costliest tenth of 15 sessions is: `percent_rank` puts two of them at or above 0.9,
# and their share of the corpus bill is what a mean would hide.
TOP_DECILE_SESSIONS = 2


def rows_of(
    run: QueryRunner,
    name: str,
    *,
    bindings: dict[str, str | int] | None = None,
    as_of: str = AS_OF_WHOLE,
    period: str = "corpus",
) -> list[dict[str, str]]:
    """One query over the fixture project, as a column mapping per row of one period."""
    arguments = [
        part for key, value in (bindings or {}).items() for part in ("--param", f"{key}={value}")
    ]
    output = run(name, "--project", MYCELIA, "--as-of", as_of, "--csv", *arguments)
    return [row for row in mappings(output) if row["period"] == period]


def shapes(run: QueryRunner, bindings: dict[str, str | int] | None = None) -> dict[str, int]:
    """`session_shapes` over the whole corpus, as how many sessions each shape holds."""
    return {
        row["shape"]: int(row["sessions"])
        for row in rows_of(run, "session_shapes", bindings=bindings)
    }


def test_every_corpus_session_lands_in_exactly_one_shape(run_query: QueryRunner) -> None:
    """The shapes partition the corpus: every session is described, and none twice."""
    # Under the thresholds the manifest ships, the recorded corpus falls into four shapes...
    assert shapes(run_query) == RECORDED_SHAPES
    # ...which between them account for every session the window holds, so a reader comparing
    # two shapes' costs is comparing parts of one whole rather than two samples.
    assert sum(RECORDED_SHAPES.values()) == MYCELIA_SESSIONS


def test_a_session_that_did_no_work_is_shaped_before_any_threshold_is_read(
    run_query: QueryRunner,
) -> None:
    """A session with nothing of its own is called that, whatever the thresholds are bound to.

    The ladder is ordered and first match wins, which only matters at the top: `no-work` is a
    statement about the session, and the shapes under it are statements about a threshold.
    """
    # The no-work sessions are not empty recordings — one compacted, and their spend is
    # counted — so a ladder that read the metrics first would have something to say about them.
    (row,) = [row for row in rows_of(run_query, "session_shapes") if row["shape"] == "no-work"]
    assert int(row["sessions"]) == NO_WORK_SESSIONS
    assert int(row["compactions"]) == NO_WORK_COMPACTIONS
    assert float(row["cost_usd"]) > 0
    # With `$skill_share_pct` bound at 0 every session that made an api call while a skill was
    # loaded matches the arm below, so the whole corpus would be skill-orchestrated if the
    # first arm did not win. The two stay where they are.
    assert shapes(run_query, {"skill_share_pct": 0})["no-work"] == NO_WORK_SESSIONS


@pytest.mark.parametrize(
    ("binding", "moved"),
    [
        # Three of the four read-only-analysis sessions ran 2 agent runs each, so lowering
        # the bar for delegation takes them and leaves the fourth...
        (
            {"delegating_runs": 2},
            {
                "conversational": 7,
                "no-work": 3,
                "delegation-heavy": 3,
                "skill-orchestrated": 2,
                "read-only-analysis": 1,
            },
        ),
        # ...a share no session can reach empties the skill shape, and its two sessions fall
        # through to whatever the arms below say they are...
        (
            {"skill_share_pct": 101},
            {"conversational": 8, "read-only-analysis": 5, "no-work": 3},
        ),
        # ...and raising what counts as busy moves sessions the other way, out of analysis and
        # into conversation, because the same threshold decides both arms.
        (
            {"busy_tool_calls": 8},
            {
                "conversational": 10,
                "no-work": 3,
                "skill-orchestrated": 2,
                "read-only-analysis": 1,
            },
        ),
    ],
    ids=["delegating_runs", "skill_share_pct", "busy_tool_calls"],
)
def test_a_rebound_threshold_moves_sessions_between_shapes(
    run_query: QueryRunner, binding: dict[str, str | int], moved: dict[str, int]
) -> None:
    """Each bound threshold is a cut point a reader can move, and moving one re-describes the
    corpus.

    The shapes are a starting vocabulary rather than a finding, so what has to hold is that the
    binding in a citation is the whole classifier: re-run it rebound and the sessions move.
    """
    assert shapes(run_query, binding) == moved
    assert sum(moved.values()) == MYCELIA_SESSIONS


def test_the_editing_shapes_need_edit_calls_no_fixture_recorded(
    planted_edits_db: Path, capsys: pytest.CaptureFixture[str], corpus_db: Path
) -> None:
    """A session that edits is shaped by how much it edited, and the ladder's last arm exists.

    Bounding the absence first: the recorded corpus holds no edit call at all, so `solo-editing`
    and `mixed` are arms nothing can reach and the plant is what reaches them.
    """
    assert (
        scalar(
            corpus_db,
            """SELECT count(*) FROM corpus_tool_calls
               WHERE name IN ('Edit', 'Write', 'NotebookEdit')""",
        )
        == 0
    )

    def planted(name: str, *arguments: str) -> Output:
        return query(planted_edits_db, capsys, name, *arguments)

    # With one session's reads rewritten as edits, a threshold at or under its 4 edit calls
    # calls it solo editing — the shape that reads "this session did its own work"...
    at_four = [
        row
        for row in rows_of(planted, "session_shapes", bindings={"editing_calls": 4})
        if row["shape"] == EDITING_AT_OR_BELOW
    ]
    assert [(int(row["sessions"]), int(row["edit_calls"])) for row in at_four] == [
        (1, PLANTED_EDIT_CALLS)
    ]
    # ...and a threshold above them drops the session past every named shape into the ladder's
    # last arm: it edited, so it is not read-only, and it is busy, so it is not conversational.
    above = shapes(planted, {"editing_calls": PLANTED_EDIT_CALLS + 1})
    assert above[EDITING_ABOVE] == 1
    assert EDITING_AT_OR_BELOW not in above


def test_an_agent_types_numbers_are_the_runs_own_thread_not_its_subtree(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """A run's counts are the rows written under its own agent id, not its children's.

    `SPINE` is the fixture with a run that spawned a run. Sum a subtree instead and the parent
    definition's per-run average silently doubles-counts the work its children did.
    """
    rows = {row["agent_type"]: row for row in rows_of(run_query, "agent_types")}
    # Every recorded run is counted once, under the definition that ran it, so `runs` cannot
    # hide a fan-out...
    assert len(rows) == AGENT_TYPES
    assert sum(int(row["runs"]) for row in rows.values()) == scalar(
        corpus_db,
        """SELECT count(*) FROM corpus_agent_runs a JOIN sessions s ON s.id = a.session_id
           WHERE s.project_dir = ?""",
        MYCELIA,
    )
    # ...and the parent's tool calls are its own thread's, with the child's counted only under
    # the child. Read from the store by source, which is what the query claims to do.
    parent, child = (
        scalar(
            corpus_db,
            "SELECT count(*) FROM corpus_tool_calls WHERE session_id = ? AND source = ?",
            SPINE,
            source,
        )
        for source in (SPINE_RUN, SPINE_LEAF)
    )
    assert child > 0, "the child run recorded no tool calls: it no longer proves the case"
    assert int(rows["claude"]["tool_calls"]) == parent
    assert int(rows["Explore"]["tool_calls"]) == child
    # And a run that forked its parent's thread is flagged as one, which is what stops a fork's
    # replayed opening being read as a definition that gets spawned twice as often as it is.
    assert int(rows["fork"]["forks"]) == 1


def test_the_top_decile_share_is_what_the_costliest_sessions_carry(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """The distribution says what the costliest tenth of sessions is worth, not just the mean.

    Checked against the costliest sessions read off the store by cost order — a different
    mechanism from the query's `percent_rank`, so the two agreeing is evidence.
    """
    (row,) = rows_of(run_query, "cost_distribution")
    costs = scalar(
        corpus_db,
        """SELECT list(r.cost_usd ORDER BY r.cost_usd DESC) FROM corpus_rollups r
           JOIN sessions s ON s.id = r.session_id WHERE s.project_dir = ?""",
        MYCELIA,
    )
    assert len(costs) == MYCELIA_SESSIONS == int(row["sessions"])
    # The whole bill, its mean and its maximum are the corpus's own...
    assert float(row["cost_usd"]) == round(sum(costs), 4)
    assert float(row["mean_cost_usd"]) == round(sum(costs) / len(costs), 4)
    assert float(row["max_cost_usd"]) == round(max(costs), 4)
    # ...and the share is the top two sessions' — over 15 sessions, the two `percent_rank`
    # puts at or above 0.9 — which is a third of the spend from an eighth of the sessions.
    assert float(row["top_decile_share"]) == round(sum(costs[:TOP_DECILE_SESSIONS]) / sum(costs), 4)


def test_skill_activity_counts_the_calls_made_while_a_skill_was_loaded(
    run_query: QueryRunner,
) -> None:
    """Every skill the corpus attributes calls to is listed with its own spread of sessions."""
    rows = {row["skill"]: row for row in rows_of(run_query, "skill_activity")}
    assert {
        skill: (int(row["api_calls"]), int(row["sessions"])) for skill, row in rows.items()
    } == RECORDED_SKILLS
    # None of them was invoked: no fixture records a `Skill` tool call, so the recorded corpus
    # is evidence about attribution alone. The planted leaf below covers the other half.
    assert {int(row["invocations"]) for row in rows.values()} == {0}


def test_a_skill_invocation_joins_its_attributed_calls_or_stands_alone(
    planted_skills_db: Path, capsys: pytest.CaptureFixture[str], corpus_db: Path
) -> None:
    """A skill is listed whether it was invoked, attributed calls, or both.

    Bounding the absence first: no fixture records a `Skill` call, so the invoked half of this
    query and both outer arms of its join are unreachable without the plant.
    """
    assert scalar(corpus_db, "SELECT count(*) FROM corpus_tool_calls WHERE name = 'Skill'") == 0

    def planted(name: str, *arguments: str) -> Output:
        return query(planted_skills_db, capsys, name, *arguments)

    rows = {row["skill"]: row for row in rows_of(planted, "skill_activity")}
    # A skill invoked in a session that also attributed calls to it is one row, not two: the
    # two halves are the same skill seen from either end...
    merged = rows[INVOKED_ATTRIBUTED]
    assert (int(merged["invocations"]), int(merged["invoking_sessions"])) == (1, 1)
    assert (int(merged["api_calls"]), int(merged["sessions"])) == RECORDED_SKILLS[
        INVOKED_ATTRIBUTED
    ]
    # ...a skill invoked and never attributed a call still gets a row, at zero — the shape of
    # a skill whose work runs as plain turns...
    alone = rows[INVOKED_ALONE]
    assert (int(alone["invocations"]), int(alone["api_calls"])) == (1, 0)
    # ...and a call whose input the parser could not read lands under a nameless skill instead
    # of vanishing, so a schema change shows up as a row rather than a smaller count.
    assert int(rows[""]["invocations"]) == 1


def test_a_commands_bill_is_its_own_turns_calls_and_nothing_elses(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """A slash command is billed for the turn it started — not for the rest of that thread, and
    not for what an agent run did.

    The corpus's one billed command sits in a session that holds other turns and two agent
    runs. The runs write under their own sources, so a bill counted per thread would swallow
    them; counted per turn it takes neither them nor the neighbouring turns.
    """
    rows = {row["command"]: row for row in rows_of(run_query, "slash_commands")}
    billed = rows[BILLED_COMMAND]
    assert (int(billed["api_calls"]), int(billed["tool_calls"])) == (
        BILLED_API_CALLS,
        BILLED_TOOL_CALLS,
    )
    # The session's main thread holds more tool calls than the command's turn is billed for,
    # and the session holds more again once the agent runs are counted. Both gaps are real, so
    # a bill widened to either would be a bigger number than this one.
    main_thread = scalar(
        corpus_db,
        "SELECT count(*) FROM corpus_tool_calls WHERE session_id = ? AND source = 'main'",
        SPINE,
    )
    whole_session = scalar(
        corpus_db, "SELECT count(*) FROM corpus_tool_calls WHERE session_id = ?", SPINE
    )
    assert BILLED_TOOL_CALLS < main_thread < whole_session


def test_tool_failures_reports_each_error_beside_the_calls_it_is_a_rate_over(
    run_query: QueryRunner, corpus_db: Path
) -> None:
    """Every tool the corpus called is listed with its errors, its calls and its spread."""
    rows = rows_of(run_query, "tool_failures")
    # Every call the corpus holds is counted once, under the tool that made it...
    assert sum(int(row["calls"]) for row in rows) == scalar(
        corpus_db,
        """SELECT count(*) FROM corpus_tool_calls t JOIN sessions s ON s.id = t.session_id
           WHERE s.project_dir = ?""",
        MYCELIA,
    )
    # ...and the two recorded failures come back as rates over very different denominators,
    # which is the pair this query exists to keep together: one `Agent` call in ten failed,
    # in one of the five sessions that called it, against one server-side `advisor` call in
    # three, in the only session that called it at all.
    failing = {row["tool"]: row for row in rows if int(row["errors"]) > 0}
    assert {
        tool: (int(row["errors"]), int(row["calls"]), float(row["error_rate"]))
        for tool, row in failing.items()
    } == {"Agent": (1, 10, 0.1), "advisor": (1, 3, 0.3333)}
    assert (int(failing["Agent"]["sessions"]), int(failing["Agent"]["erring_sessions"])) == (5, 1)


@pytest.fixture(scope="session")
def planted_edits_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with one session's `Read` calls rewritten as `Edit` calls.

    Invented, and it has to be: no recorded fixture edits a file, so the shapes that read
    `edit_calls` are arms nothing exercises. What is real is the rows — their session, its
    period and its other counts — and the fork among them, which is why the corpus views keep
    four of the eight rewritten calls.
    """
    path = tmp_path_factory.mktemp("edits") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            "UPDATE tool_calls SET name = 'Edit' WHERE name = 'Read' AND session_id = ?",
            [FORK_ORIGIN],
        )
    finally:
        connection.close()
    return path


@pytest.fixture(scope="session")
def planted_skills_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus with three of `SPINE`'s reads rewritten as `Skill` invocations.

    Invented inputs — fixture redaction replaces every tool input, and no fixture calls `Skill`
    at all — but the shape is the one `docs/schema.md` records: the skill's name sits at
    `$.skill` of the call's input. One invokes a skill the corpus attributes calls to, one a
    skill it does not, and one carries an input no parser can read.
    """
    path = tmp_path_factory.mktemp("skills") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    connection = duckdb.connect(str(path))
    try:
        ids = [
            row[0]
            for row in connection.execute(
                """SELECT id FROM tool_calls
                   WHERE name = 'Read' AND session_id = ? AND source = 'main' ORDER BY id""",
                [SPINE],
            ).fetchall()
        ]
        inputs = [
            json.dumps({"skill": INVOKED_ATTRIBUTED}),
            json.dumps({"skill": INVOKED_ALONE}),
            UNREADABLE_INPUT,
        ]
        assert len(ids) >= len(inputs), "SPINE's main thread lost reads: re-pick the rows"
        for call_id, value in zip(ids, inputs, strict=False):
            connection.execute(
                "UPDATE tool_calls SET name = 'Skill', input = ? WHERE id = ? AND session_id = ?",
                [value, call_id, SPINE],
            )
    finally:
        connection.close()
    return path
