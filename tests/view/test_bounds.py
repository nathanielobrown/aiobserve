"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Three mechanisms, checked separately: the queries behind the pages and fragments never select
an unbounded fat column, what they do select is truncated in SQL rather than in the template,
and every page size is a bound parameter whose production default is pinned here. Together
they are what makes the bound hold by construction rather than by the fixture corpus's luck —
a per-value fetch is the one exception, and it is exempt because its unit *is* one value.
"""

import re
from pathlib import Path

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.analyze.queries import QUERIES, VIEW_PREFIX
from aiobserve.view.app import (
    PAGE_SESSIONS,
    Fragment,
    Page,
    Value,
    build_app,
)
from tests.conftest import (
    ANCESTOR,
    DENSE_CALL,
    DENSE_CALL_SOURCE,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    SPINE,
)
from tests.view.conftest import Planter, fields, one, values

# The columns that hold whatever the agent read or wrote: one of them can be megabytes, and
# none of them belongs on a page whole. `raw` is a transcript line, `result` a tool's output,
# `input` its arguments, `text` and `thinking` a model's answer.
FAT = ("raw", "text", "thinking", "result", "input", "content")

# What a page may weigh, and what one session's row in the list may add to it. The list is
# the page a corpus grows, so `PAGE_SESSIONS` rows at `SESSION_BYTES` each have to fit.
PAGE_BYTES = 350_000
SESSION_BYTES = 2_000

# How much of a turn's prompt the timeline shows, from `session_digest`'s own `substr`.
PROMPT_CHARS = 300
# The same, for what a fragment shows of an api call's text and a tool call's arguments.
TEXT_CHARS = 2_000
INPUT_CHARS = 200
# What one rendered tool row costs, from the design's arithmetic for the payload bound.
TOOL_ROW_BYTES = 300


# What a query may wrap a fat column in and still be bounded: a fixed-width prefix of it, or
# a count of what it holds. Anything else puts the whole value on the page.
BOUNDING = ("substr", "length")


def unbounded(sql: str) -> set[str]:
    """The fat columns a statement selects outside a bounding call — what a page can't afford."""
    without_comments = re.sub(r"--[^\n]*", " ", sql)
    truncated = re.sub(rf"(?:{'|'.join(BOUNDING)})\s*\([^()]*\)", " ", without_comments)
    return {word for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", truncated) if word in FAT}


def test_the_fat_column_scan_catches_one() -> None:
    """The scan below is worth its green: it flags a select the pages must not contain.

    The statements are invented — no shipped query selects a fat column whole, which is
    exactly why the instrument needs its own case.
    """
    assert unbounded("SELECT r.raw FROM raw_records r -- text") == {"raw"}
    assert unbounded("SELECT substr(r.raw, 1, 200) AS raw_head FROM raw_records r") == set()
    # A count of a value is a number, and a page can afford any number.
    assert unbounded("SELECT length(r.raw) AS raw_chars FROM raw_records r") == set()


@pytest.mark.parametrize("name", sorted(Page) + sorted(Fragment))
def test_no_page_or_fragment_query_selects_a_fat_column_whole(name: str) -> None:
    """Every query behind a page or a fragment is bounded in SQL, however large the record."""
    assert unbounded(queries.load(name)) == set()


@pytest.mark.parametrize("value", sorted(Value))
def test_a_per_value_query_returns_the_one_value_it_is_named_for(value: Value) -> None:
    """The per-value queries are the exception, and they are the exception by declaration.

    They select a fat column whole — that is what they are for. What keeps the bound is that
    the unit is one row of one value, so the fetch tops out at the largest value in the store
    rather than at a page's worth of them.
    """
    assert unbounded(queries.load(value)) != set()


def test_every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value() -> None:
    """A viewer query lands in one of the three sets, so the scans above cannot miss it.

    Without this, a query shipped under `view_` but named in no enum is scanned by nothing
    and can select a fat column onto a page with the whole tier still green.
    """
    declared = set(Page) | set(Fragment) | set(Value)
    # Every query the viewer owns is scanned by one of the leaves above...
    assert {name for name in QUERIES if name.startswith(VIEW_PREFIX)} <= declared
    # ...and every name declared is a query that ships, digests shared with the runner too.
    assert declared <= set(QUERIES)


def test_the_manifest_pins_the_production_page_sizes() -> None:
    """The page sizes the payload bound is computed from are the ones production runs.

    Every other leaf in this file binds fixture-sized values, so without this pin the whole
    section would pass against any defaults at all — a `page_tools` of 5,000 would break the
    bound in production while CI stayed green. The numbers come from the turn-expand
    paragraph of `plans/trace-viewer/design.md`.
    """
    assert QUERIES["view_turn_calls"].params["page_calls"].default == 25
    assert QUERIES["view_call_tools"].params["page_tools"].default == 40
    # And they are the numbers the design's own arithmetic uses: a page of calls carries at
    # most this much text plus this many tool rows, which is what `PAGE_BYTES` was set from.
    assert queries.PAGE_CALLS * (TEXT_CHARS + queries.PAGE_TOOLS * TOOL_ROW_BYTES) <= PAGE_BYTES


def limits(sql: str) -> list[str]:
    """What follows each LIMIT in a statement, comments cut — a parameter, or a number."""
    return re.findall(r"\bLIMIT\s+([^\s;]+)", re.sub(r"--[^\n]*", " ", sql))


def test_the_limit_scan_catches_a_literal_page_size() -> None:
    """The scan below is worth its green: it flags the page size no caller can change.

    Both statements are invented — every shipped query binds its limit, which is exactly why
    the instrument needs a case of its own.
    """
    assert limits("SELECT * FROM raw_records LIMIT 100;") == ["100"]
    assert limits("SELECT * FROM raw_records LIMIT $page_records -- LIMIT 100") == ["$page_records"]


@pytest.mark.parametrize("name", sorted(name for name in QUERIES if name.startswith(VIEW_PREFIX)))
def test_every_page_size_in_a_viewer_query_is_a_bound_parameter(name: str) -> None:
    """No viewer query hides a page size in its text, so every bound is one a reader can see.

    The rule rather than a list of the parameters that exist today: a query landing with a
    literal `LIMIT 100` is a size nobody can bind down to reach its boundary in a test, and
    nobody can bind up when a real corpus needs more.
    """
    for limit in limits(queries.load(name)):
        assert limit.startswith("$"), f"{name} limits by a literal: {limit}"
        assert limit.lstrip("$") in QUERIES[name].params


def test_every_fat_column_is_still_a_column(store: duckdb.DuckDBPyConnection) -> None:
    """The scan is spelled in column names, so a rename must fail here rather than pass."""
    named = {
        row[0]
        for row in store.execute(
            "SELECT column_name FROM duckdb_columns() WHERE schema_name = 'main'"
        ).fetchall()
    }
    assert set(FAT) <= named


def test_a_served_page_stays_under_its_ceiling(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """No page the viewer serves is large enough to stall a browser, at any corpus size."""
    listing = len(client.get("/").content)
    (count,) = one(store, "SELECT count(*) FROM sessions")
    assert listing < PAGE_BYTES
    # The fixture corpus is smaller than a page, so its own weight proves nothing about a
    # large one. What does is the marginal cost of a row — the whole list less the same page
    # holding one session — which is what a growing corpus multiplies.
    chrome = len(client.get("/?size=1").content)
    per_session = (listing - chrome) / (count - 1)
    assert per_session < SESSION_BYTES
    # A full page is the most the list ever serves, and that is the number under the ceiling.
    assert chrome + per_session * PAGE_SESSIONS < PAGE_BYTES
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        assert len(client.get(f"/session/{session_id}").content) < PAGE_BYTES, session_id


# One real URL per route the app exposes, keyed by the route's own path template. The sweep
# below reads this as a set, so a route added with no entry fails rather than going unswept.
ROUTES: dict[str, str] = {
    "/": "/",
    "/session/{session_id}": f"/session/{SPINE}",
    "/fragment/turn/{session_id}/{source}/{turn_id}": (
        f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}"
    ),
    "/fragment/tools/{session_id}/{source}/{api_call_id}": (
        f"/fragment/tools/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_CALL}"
    ),
    "/fragment/text/{session_id}/{source}/{api_call_id}": (
        f"/fragment/text/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_CALL}"
    ),
    "/fragment/thinking/{session_id}/{source}/{api_call_id}": (
        f"/fragment/thinking/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_CALL}"
    ),
    "/fragment/tool/{session_id}/{source}/{tool_call_id}": (
        f"/fragment/tool/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_TOOL}"
    ),
}


def test_every_route_the_viewer_exposes_is_in_the_payload_sweep(client: TestClient) -> None:
    """The sweep covers the routes the app has, not the ones someone remembered to list.

    Without this, a route shipped later is a page nothing weighs — and a route that selects
    a fat column is exactly the kind of thing that arrives quietly.
    """
    exposed = {
        route.path
        for route in client.app.routes  # pyrefly: ignore
        if isinstance(route, APIRoute)
    }
    assert exposed == set(ROUTES)


@pytest.mark.parametrize("path", sorted(ROUTES.values()))
def test_no_route_serves_more_than_the_page_ceiling(path: str, client: TestClient) -> None:
    """Every route answers under the ceiling at the production defaults — no size bound down.

    A smoke check rather than the proof: the fixture corpus is far smaller than a page, so
    what makes the bound hold is the fat-column scan and the page-size arithmetic above. What
    this catches is the route that ships a whole column anyway.
    """
    response = client.get(path)
    assert response.status_code == 200, path
    assert len(response.content) < PAGE_BYTES, path


def test_the_turn_fragment_pages_its_calls_and_partitions_them(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Following a turn's pages shows every call it made once, in order, with none skipped.

    Bound at two calls against the corpus's densest recorded turn, which holds four — so the
    page boundary is a real overflow of recorded data rather than a staged one.
    """
    indexes = [
        row[0]
        for row in store.execute(
            'SELECT "index" FROM live_api_calls'
            " WHERE session_id = ? AND source = 'main' AND turn_id = ? ORDER BY \"index\"",
            [ANCESTOR, DENSE_TURN],
        ).fetchall()
    ]
    assert len(indexes) == 4, "the densest fixture turn moved: re-pick DENSE_TURN"
    seen: list[str] = []
    after = -1
    # Two at a time, following the fragment's own continuation each round...
    for _ in range(4):
        html = client.get(
            f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}", params={"after": after, "calls": 2}
        ).text
        page = values(html, "data-call-index")
        assert len(page) <= 2
        seen += page
        following = values(html, "data-more-calls")
        if not following:
            break
        after = int(following[0])
    else:
        pytest.fail("the fragment never ran out of pages")
    # ...walks the turn's calls exactly once each, in index order.
    assert seen == [str(index) for index in indexes]
    # Keyset, never OFFSET: a page computed by counting rows re-reads what a page missed.
    assert "OFFSET" not in queries.load(Fragment.TURN_CALLS).upper()


def test_the_tool_cap_truncates_the_list_and_says_how_much_it_cut(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A call's tool rows are capped in SQL, and the indicator pages the rest rather than
    losing them.

    Bound at two tool rows against the recorded call that made four. The count in the
    indicator is what gives this leaf teeth: a cap that renders two rows without saying how
    many it dropped looks identical to a call that only made two.
    """
    (total,) = one(
        store,
        "SELECT count(*) FROM live_tool_calls"
        " WHERE session_id = ? AND source = ? AND api_call_id = ?",
        [FORK_ORIGIN, DENSE_CALL_SOURCE, DENSE_CALL],
    )
    assert total == 4, "the densest fixture call moved: re-pick DENSE_CALL"
    url = f"/fragment/tools/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_CALL}"
    first = client.get(url, params={"after": -1, "tools": 2}).text
    # Two rows, and the page says two more are behind the indicator...
    shown = values(first, "data-tool-call")
    assert len(shown) == 2
    assert fields(first, "data-more", DENSE_CALL)["count"] == "+2 more"
    # ...which fetches exactly those two, so the two pages partition the call's tools.
    after = int(values(first, "data-more-tools")[0])
    rest = values(client.get(url, params={"after": after, "tools": 2}).text, "data-tool-call")
    assert len(rest) == 2
    assert set(shown) & set(rest) == set()
    assert len(set(shown) | set(rest)) == total


def test_a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every preview is truncated in the query, so no one huge value can bloat what shows it.

    The three previews the viewer renders — a turn's prompt on the session page, an api
    call's text and a tool call's arguments in the turn fragment — checked at once against
    one planted store. The oversized values are invented: redaction flattened every recorded
    string to a few characters, so no fixture reaches a cap.
    """
    turn_id, _ = one(
        store,
        'SELECT id, "index" FROM turns WHERE session_id = ? AND source = \'main\' ORDER BY "index"',
        [SPINE],
    )
    # Each value is planted well past its own cap, onto the real row a fixture recorded...
    long = "x" * 5_000
    path: Path = plant(
        ("UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?", [long, SPINE, turn_id]),
        ("UPDATE api_calls SET text = ? WHERE session_id = ?", [long, ANCESTOR]),
        ("UPDATE tool_calls SET input = ? WHERE session_id = ?", [long, FORK_ORIGIN]),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
        turn = planted.get(f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}").text
        tools = planted.get(f"/fragment/tools/{FORK_ORIGIN}/{DENSE_CALL_SOURCE}/{DENSE_CALL}").text
    # ...and what each of them shows is its cap, not the value.
    assert len(fields(page, "data-turn", turn_id)["prompt"]) == PROMPT_CHARS
    assert len(fields(turn, "data-api-call", DENSE_TURN_CALL)["text_head"]) == TEXT_CHARS
    assert len(fields(tools, "data-tool-call", DENSE_TOOL)["input_head"]) == INPUT_CHARS
