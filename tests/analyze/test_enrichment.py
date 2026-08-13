"""The enrichment slice of the library: what is described, what one session says, what to check.

Enrichment writes a model's words beside a run, a turn and a session, and nothing in the
store says whether those words are true. So the leaves here are about the three things a
reader needs before trusting them: a coverage row that counts the items a pass could have
described rather than every row of a level, a per-session sheet that pairs an item's
description with the digest that shows what the item did, and a draw that puts every
category in front of a validation reader instead of the common ones.

The rows come from `enriched_db`, which plants through the real writer: the keys are a
pass's own, the four model-written fields are invented, and the last item of each level is
left undescribed so coverage has a gap to report.
"""

from pathlib import Path

import duckdb
import pytest

from tests.analyze.conftest import (
    AS_OF_WHOLE,
    PLANTED_MODELS,
    QueryRunner,
    mappings,
    scalar,
)
from tests.conftest import MYCELIA, SPINE

# The level names the three queries share, as `enrich/prompts.py` spells them.
TURN = "turn"
AGENT_RUN = "agent_run"
SESSION = "session"


def coverage(run: QueryRunner, level: str) -> list[dict[str, str]]:
    """One level's coverage rows over the whole corpus, the window period dropped."""
    output = run("enrichment_coverage", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv")
    return [row for row in mappings(output) if row["level"] == level and row["period"] == "corpus"]


def draw(run: QueryRunner, level: str, *arguments: str) -> list[dict[str, str]]:
    """One `select_enrichments` draw over the whole corpus."""
    output = run(
        "select_enrichments",
        "--project",
        MYCELIA,
        "--as-of",
        AS_OF_WHOLE,
        "--param",
        f"level={level}",
        "--csv",
        *arguments,
    )
    return mappings(output)


def digest(run: QueryRunner, session_id: str, *arguments: str) -> list[dict[str, str]]:
    """One session's enrichment sheet."""
    output = run("enrichment_digest", "--param", f"session_id={session_id}", "--csv", *arguments)
    return mappings(output)


def test_coverage_counts_the_items_a_pass_could_have_described(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """A level's denominator is its enrichable items, and the undescribed ones are one row."""
    # If the corpus holds agent runs, all of which a pass would describe...
    runs = scalar(
        enriched_db,
        "SELECT count(*) FROM corpus_agent_runs a"
        " JOIN sessions s ON s.id = a.session_id WHERE starts_with(s.project_dir, ?)",
        MYCELIA,
    )
    described = scalar(
        enriched_db,
        "SELECT count(*) FROM agent_run_enrichments e"
        " JOIN corpus_agent_runs a ON a.session_id = e.session_id AND a.id = e.agent_run_id"
        " JOIN sessions s ON s.id = a.session_id WHERE starts_with(s.project_dir, ?)",
        MYCELIA,
    )
    assert 0 < described < runs
    rows = coverage(enriched_query, AGENT_RUN)
    # ...then every row of the level carries the same denominator, which is that count...
    assert {int(row["level_items"]) for row in rows} == {runs}
    # ...the described rows add up to the rows a pass wrote...
    assert sum(int(row["items"]) for row in rows if row["category"]) == described
    # ...and the ones it has not reached are one row with no category, so a reader sees the
    # gap without subtracting anything.
    gap = [row for row in rows if not row["category"]]
    assert [int(row["items"]) for row in gap] == [runs - described]


def test_coverage_leaves_out_the_sessions_enrichment_never_describes(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """A session with no turn and no agent run is out of the denominator, not a permanent gap."""
    # If the corpus holds sessions that did no work of their own, which a pass skips...
    total, enrichable = scalar(
        enriched_db,
        "SELECT count(*), count(*) FILTER (r.turns > 0 OR r.agent_runs > 0)"
        " FROM corpus_rollups r WHERE starts_with(r.project_dir, ?)",
        MYCELIA,
        columns=2,
    )
    assert enrichable < total
    # ...then the session level counts the ones it would describe and no others.
    rows = coverage(enriched_query, SESSION)
    assert {int(row["level_items"]) for row in rows} == {enrichable}


def test_coverage_splits_a_level_by_the_stamp_its_rows_were_written_under(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """Model and prompt version are grouping keys, so a half-migrated corpus reads as one."""
    # If a level's rows were written by two models, and some under an older prompt...
    rows = [row for row in coverage(enriched_query, TURN) if row["category"]]
    assert {row["enrichment_model"] for row in rows} == set(PLANTED_MODELS)
    assert len({row["prompt_version"] for row in rows}) == 2
    # ...then each model-and-version pair is counted on its own, matching the store...
    for model in PLANTED_MODELS:
        for version in {row["prompt_version"] for row in rows}:
            stamped = sum(
                int(row["items"])
                for row in rows
                if row["enrichment_model"] == model and row["prompt_version"] == version
            )
            assert stamped == scalar(
                enriched_db,
                "SELECT count(*) FROM turn_enrichments e"
                " JOIN corpus_turns t ON t.session_id = e.session_id"
                " AND t.source = e.source AND t.id = e.turn_id"
                " JOIN sessions s ON s.id = t.session_id"
                " WHERE starts_with(s.project_dir, ?) AND e.model = ? AND e.prompt_version = ?",
                MYCELIA,
                model,
                int(version),
            )
    # ...and the pairs are not the same split as the categories, which would make either
    # column unreadable from the other.
    assert len({(row["enrichment_model"], row["prompt_version"]) for row in rows}) > 1
    assert len({row["category"] for row in rows}) > 1


def test_a_digest_lists_one_session_at_every_level_and_says_what_is_undescribed(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """A session's sheet holds its own turns, runs and session row, keyed as the digests key."""
    rows = digest(enriched_query, SPINE)
    # If the session's sheet holds a row per main turn, per agent run, and one for itself...
    assert {row["level"] for row in rows} == {TURN, AGENT_RUN, SESSION}
    for level, sql in (
        (TURN, "SELECT list(id) FROM live_turns WHERE session_id = ? AND source = 'main'"),
        (AGENT_RUN, "SELECT list(id) FROM live_agent_runs WHERE session_id = ?"),
    ):
        listed = {row["item_id"] for row in rows if row["level"] == level}
        assert listed == set(scalar(enriched_db, sql, SPINE))
    # ...then the described ones carry the words and the stamp a reader is checking...
    described = [row for row in rows if row["description"]]
    assert described
    for row in described:
        assert row["category"] and row["outcome"] and row["enrichment_model"]
        assert row["taxonomy_version"] and row["prompt_version"]


def test_an_item_no_pass_has_described_keeps_its_row_on_the_sheet(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """An unenriched item is visible as a row with nothing in it, not as an absence."""
    # If a session the pass would describe has no enrichment row yet...
    undescribed = scalar(
        enriched_db,
        "SELECT list(r.session_id) FROM session_rollups r"
        " LEFT JOIN session_enrichments e USING (session_id)"
        " WHERE (r.turns > 0 OR r.agent_runs > 0) AND e.session_id IS NULL",
    )
    assert undescribed
    # ...then its sheet still holds the session row, with the model's columns empty — which
    # is what tells a reader the pass has not reached it rather than that it does not exist.
    rows = digest(enriched_query, undescribed[0], "--param", f"level={SESSION}")
    assert [row["description"] for row in rows] == [""]
    assert [row["category"] for row in rows] == [""]
    assert [row["enrichment_model"] for row in rows] == [""]


def test_a_digest_narrows_to_one_level(enriched_query: QueryRunner) -> None:
    """Bind `$level` and the sheet is that level's rows — the rest is the same sheet."""
    every = digest(enriched_query, SPINE)
    runs = digest(enriched_query, SPINE, "--param", f"level={AGENT_RUN}")
    assert runs == [row for row in every if row["level"] == AGENT_RUN]
    assert 0 < len(runs) < len(every)


def test_the_draw_takes_the_same_rows_every_time_it_runs(enriched_query: QueryRunner) -> None:
    """One seed draws one sample; a different seed draws a different one."""
    # If the same bindings run twice, they return the same rows in the same order...
    first = draw(enriched_query, TURN, "--param", "per_category=1")
    assert first == draw(enriched_query, TURN, "--param", "per_category=1")
    # ...then the seed is what a reader rotates to see other items. The turn level, because
    # a stratum has to hold more items than the draw takes for a rotation to have anywhere
    # to go — the corpus's agent runs are two to a category.
    rotated = draw(enriched_query, TURN, "--param", "per_category=1", "--param", "seed=rotated")
    assert {row["item_id"] for row in rotated} != {row["item_id"] for row in first}


def test_the_draw_gives_every_category_the_same_number_of_slots(
    enriched_query: QueryRunner,
) -> None:
    """A rare category is read as often as a common one — that is what stratifying buys."""
    # If a level's described rows fall into several categories of uneven size...
    every = draw(enriched_query, TURN, "--param", "per_category=99")
    sizes = {
        stratum: len([row for row in every if row["stratum"] == stratum])
        for stratum in {row["stratum"] for row in every}
    }
    assert len(sizes) > 1 and len(set(sizes.values())) > 1
    # ...then a draw of one apiece takes one from each, however big the category is.
    one_each = draw(enriched_query, TURN, "--param", "per_category=1")
    assert sorted(row["stratum"] for row in one_each) == sorted(sizes)


def test_the_draw_carries_what_a_reader_needs_to_pick_and_open_an_item(
    enriched_query: QueryRunner, enriched_db: Path
) -> None:
    """A drawn run comes with its identity and its size, so nothing is opened blind."""
    rows = draw(enriched_query, AGENT_RUN, "--param", "per_category=99")
    for row in rows:
        # If a drawn row names the session and the source it sits at...
        assert row["session_id"] and row["source"] == row["item_id"] and row["agent_type"]
        # ...then its size is the run's own thread, as the store counts it — the runs it
        # spawned have sources of their own.
        calls, cost = scalar(
            enriched_db,
            "SELECT count(*), coalesce(round(sum(cost_usd), 4), 0) FROM corpus_api_calls"
            " WHERE session_id = ? AND source = ?",
            row["session_id"],
            row["source"],
            columns=2,
        )
        assert int(row["api_calls"]) == calls
        assert float(row["cost_usd"]) == pytest.approx(float(cost))


def test_a_query_that_reads_the_enrichment_tables_says_so_on_a_store_without_them(
    run_query: QueryRunner,
) -> None:
    """Ask the bare corpus for coverage and it fails naming the missing table, not silently."""
    with pytest.raises(duckdb.CatalogException, match="_enrichments"):
        run_query("enrichment_coverage", "--project", MYCELIA, "--as-of", AS_OF_WHOLE, "--csv")


def test_the_draw_and_the_digest_agree_on_one_item(
    enriched_query: QueryRunner,
) -> None:
    """What the draw says about a run is what the session's sheet says about it."""
    # If a draw names a run to check...
    drawn = next(row for row in draw(enriched_query, AGENT_RUN, "--param", "per_category=99"))
    # ...then opening that session's sheet shows the same description under the same key,
    # which is the pairing a validation read is built on.
    sheet = digest(enriched_query, drawn["session_id"], "--param", f"level={AGENT_RUN}")
    row = next(row for row in sheet if row["item_id"] == drawn["item_id"])
    assert row["description"] == drawn["description"]
    assert row["category"] == drawn["stratum"]
    assert row["outcome"] == drawn["outcome"]
