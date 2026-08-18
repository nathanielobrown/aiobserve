"""The session page's context panel: what the query buckets, and what the geometry draws.

Two levels, because the panel is two pieces. `view_context_timeline.sql` decides which turns
become points and how many turns one point stands for, and it is checked against the recorded
corpus with `$max_points` bound down to reach a boundary no fixture thread is long enough to
cross. `chart.build` decides where those points land, and it is checked against invented rows
— a climb, a compaction between two turns, a thread with nothing to plot — because the shape
is the whole point and no recorded thread holds the ones worth asserting on.
"""

import datetime as dt

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import chart
from aiobserve.view.chart import Band, Mark, Tick, TokenType
from aiobserve.view.store import Page, Row, page_rows
from tests.conftest import MAIN, MODEL_ONLY, SPINE
from tests.view.conftest import one, values

# Invented rows in the shape `view_context_timeline.sql` returns. Invented and unavoidably so:
# the geometry worth asserting on is a long climb, a compaction between two points and a
# bucketed thread, and the deepest fixture thread makes calls in three turns.
EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def point(index: int, context: int, **spend: int) -> Row:
    """One turn's row: the context it ended at, and what it spent getting there."""
    return {
        "bucket_index": index,
        "first_turn_index": index,
        "last_turn_index": index,
        "started_at": EPOCH + dt.timedelta(minutes=index),
        "api_calls": 1,
        "context_tokens": context,
        **{token.value: spend.get(token.value, 0) for token in TokenType},
    }


def compaction(minutes: int, pre: int, post: int) -> Row:
    """One compaction marker, in the shape `view_compactions.sql` returns."""
    return {
        "compaction_id": f"compaction-{minutes}",
        "timestamp": EPOCH + dt.timedelta(minutes=minutes),
        "trigger": "auto",
        "pre_tokens": pre,
        "post_tokens": post,
        "duration_ms": 1_000,
    }


def coordinates(points: str) -> list[tuple[int, int]]:
    """A `points` or `d` run parsed back into pairs — what the browser would draw."""
    return [
        (int(x), int(y))
        for pair in points.replace("M ", "").replace(" Z", "").split()
        for x, y in [pair.split(",")]
    ]


def test_a_thread_shorter_than_the_cap_draws_a_point_for_every_answering_turn(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Under the point cap nothing is grouped: each point is one turn that called the model."""
    # If a thread holds fewer turns than the cap — `spine/` answers in three of its turns...
    answered = [
        row[0]
        for row in store.execute(
            'SELECT DISTINCT t."index" FROM live_turns t JOIN live_api_calls c'
            " ON c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id"
            ' WHERE t.session_id = ? AND t.source = ? ORDER BY t."index"',
            [SPINE, MAIN],
        ).fetchall()
    ]
    assert len(answered) == 3, "the deepest fixture thread moved: re-pick the session"
    # ...then every one of them is a bucket of its own, in turn order...
    rows = page_rows(
        store,
        Page.CONTEXT_TIMELINE,
        session_id=SPINE,
        source=MAIN,
        max_points=queries.CONTEXT_POINTS,
    )
    assert [row["first_turn_index"] for row in rows] == answered
    assert [row["last_turn_index"] for row in rows] == answered
    # ...counted against the turns that answered rather than every turn the thread holds, and
    # each one carrying the size its own last call reported.
    assert {row["total_turns"] for row in rows} == {len(answered)}
    (context,) = one(
        store,
        "SELECT c.input_tokens + c.cache_read_tokens + c.cache_creation_tokens"
        " FROM live_api_calls c JOIN live_turns t ON t.id = c.turn_id"
        " AND t.session_id = c.session_id AND t.source = c.source"
        ' WHERE c.session_id = ? AND c.source = ? AND t."index" = ?'
        ' ORDER BY c."index" DESC LIMIT 1',
        [SPINE, MAIN, answered[-1]],
    )
    assert rows[-1]["context_tokens"] == context
    # And a chart of unbucketed points says nothing about grouping, because nothing grouped.
    drawn = chart.build(rows, [])
    assert drawn is not None and not drawn.bucketed


def test_a_thread_past_the_point_cap_groups_consecutive_turns(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Past the cap a point stands for several turns: their spend totalled, their last context.

    The cap is bound down to two rather than planting a 268-turn thread, because two over
    three recorded turns is the same arithmetic the production 100 does over 268.
    """
    # If a thread holds more answering turns than the cap allows points...
    whole = page_rows(store, Page.CONTEXT_TIMELINE, session_id=SPINE, source=MAIN, max_points=100)
    rows = page_rows(store, Page.CONTEXT_TIMELINE, session_id=SPINE, source=MAIN, max_points=2)
    # ...consecutive turns group in index order, at most `$max_points` groups of them...
    assert [(row["first_turn_index"], row["last_turn_index"]) for row in rows] == [
        (whole[0]["first_turn_index"], whole[1]["last_turn_index"]),
        (whole[2]["first_turn_index"], whole[2]["last_turn_index"]),
    ]
    # ...spend totals over the group, while context keeps the last turn's snapshot...
    assert rows[0]["input_tokens"] == whole[0]["input_tokens"] + whole[1]["input_tokens"]
    assert rows[0]["api_calls"] == whole[0]["api_calls"] + whole[1]["api_calls"]
    assert rows[0]["context_tokens"] == whole[1]["context_tokens"]
    # ...and the chart says so, because a point that is several turns reads as one turn.
    drawn = chart.build(rows, [])
    assert drawn is not None and drawn.bucketed


def test_a_climbing_thread_draws_a_line_that_rises_left_to_right() -> None:
    """The context line spans the box, one vertex a point, with a climb drawn as a climb."""
    # If context climbs turn over turn...
    rows = [point(index, context) for index, context in enumerate((10_000, 40_000, 80_000))]
    drawn = chart.build(rows, [])
    assert drawn is not None
    # ...the line has a vertex per point, spanning the box's whole width...
    drawn_points = coordinates(drawn.context_line)
    assert [x for x, _ in drawn_points] == [0, chart.WIDTH // 2, chart.WIDTH]
    # ...each one placed against the largest context the thread reached, which is the number
    # the caption prints — so the last point sits on the top edge and the climb reads as one.
    assert drawn.y_max == 80_000
    assert [y for _, y in drawn_points] == [chart.HEIGHT - 18, chart.HEIGHT - 70, 0]
    # And the axis labels the turns rather than the points, sparsely: three points, three ticks.
    assert drawn.x_ticks == (Tick(0, 0), Tick(chart.WIDTH // 2, 1), Tick(chart.WIDTH, 2))


def test_the_composition_bands_stack_bottom_up_in_token_order() -> None:
    """Each token type is a closed band drawn on the ones below it, in `TokenType` order."""
    # If two turns spent one of each token type...
    rows = [
        point(
            index,
            100,
            input_tokens=10,
            cache_read_tokens=20,
            cache_creation_tokens=30,
            output_tokens=40,
        )
        for index in range(2)
    ]
    drawn = chart.build(rows, [])
    assert drawn is not None
    # ...there is a band per type, in the order they stack...
    assert tuple(band.type for band in drawn.bands) == tuple(TokenType)
    # ...each closed over its own two edges — a point of lower edge and a point of upper for
    # every point on the chart...
    assert all(len(coordinates(band.outline)) == 2 * len(rows) for band in drawn.bands)
    # ...the first sitting on the floor of the box, and the last reaching its ceiling, because
    # the four together are the whole of what the busiest turn spent.
    assert drawn.spend_max == 100
    assert coordinates(drawn.bands[0].outline)[0] == (0, chart.HEIGHT)
    assert coordinates(drawn.bands[-1].outline)[-1] == (0, 0)


def test_a_compaction_rules_the_chart_between_the_turns_it_fell_between() -> None:
    """A compaction is drawn where it happened: between the point before it and the one after.

    The same landing rule the turn timeline places its markers by (`threads.lands`), so the
    two surfaces cannot disagree about which turns a compaction fell between.
    """
    # If a compaction ran between the second turn and the third...
    rows = [point(index, 10_000 * (index + 1)) for index in range(3)]
    drawn = chart.build(rows, [compaction(minutes=1, pre=180_000, post=40_000)])
    assert drawn is not None
    # ...its rule sits between the two points, carrying the drop it made and no other text.
    assert drawn.compaction_marks == (Mark(chart.WIDTH // 4, 180_000, 40_000),)
    # ...and one that trails every turn of the thread rules its right edge instead.
    trailing = chart.build(rows, [compaction(minutes=99, pre=180_000, post=40_000)])
    assert trailing is not None
    assert trailing.compaction_marks == (Mark(chart.WIDTH, 180_000, 40_000),)


def test_a_thread_with_nothing_to_plot_gets_no_chart() -> None:
    """A thread whose turns made no call, or made them all in one turn, has no shape to draw."""
    assert chart.build([], []) is None
    assert chart.build([point(0, 10_000)], [compaction(minutes=1, pre=1, post=1)]) is None


def test_the_session_page_draws_the_panel_above_its_timeline(client: TestClient) -> None:
    """A session whose thread has a shape shows it, before the turns it summarises."""
    page = client.get(f"/session/{SPINE}").text
    # The panel is on the page, above the timeline it gives the overview of...
    assert page.index('id="context-chart"') < page.index('id="timeline"')
    # ...drawing a vertex per answering turn and a band per token type...
    assert len(values(page, "points")[0].split()) == 3
    assert values(page, "data-band") == [token.value for token in TokenType]
    # ...and the page cites the query that produced it, like every other query it ran.
    assert f"queries/{Page.CONTEXT_TIMELINE}.sql" in page


def test_a_session_that_never_called_the_model_gets_no_panel(client: TestClient) -> None:
    """A `/model`-only session gets no empty chart: the section is absent, not blank."""
    page = client.get(f"/session/{MODEL_ONLY}").text
    assert 'id="context-chart"' not in page
    # The query still ran, so the page still cites it — a citation says what produced the page,
    # including the read that came back empty.
    assert f"queries/{Page.CONTEXT_TIMELINE}.sql" in page


def test_a_band_names_itself_for_the_column_it_sums() -> None:
    """Each band is named for the token column it stacks, and labelled by that name."""
    assert Band(TokenType.CACHE_READ, "M 0,0 Z").type.label == "cache read"
    assert {token.value for token in TokenType} == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    }
