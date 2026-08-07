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
    MAX_CHUNK_CHARS,
    MAX_PAGE_CALLS,
    MAX_PAGE_RECORDS,
    MAX_PAGE_TOOLS,
    build_app,
)
from aiobserve.view.listing import MAX_PAGE_SESSIONS
from aiobserve.view.store import Fragment, Page, Value
from tests.conftest import (
    ANCESTOR,
    CONFIG_ONLY,
    DENSE_CALL,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    OFFLOAD_FILE,
    SPINE,
    SPINE_RUN,
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
# What a row of the list really costs, measured against `data/traces.duckdb` on 2026-08-08:
# 308,233 B for 300 sessions less 3,698 B of page chrome. The fixture rows are redacted down
# to a few characters, so a projection off them alone says nothing about a real corpus.
MEASURED_SESSION_BYTES = 1_015

# How much of a turn's prompt the timeline shows, from `session_digest`'s own `substr`.
PROMPT_CHARS = 300
# The same, for what a fragment shows of an api call's text and a tool call's arguments.
TEXT_CHARS = 2_000
INPUT_CHARS = 200
# What the markup around one call row and one tool row of a turn fragment costs, with the
# content those rows carry subtracted. Measured against `data/traces.duckdb` on 2026-08-07 as
# the marginal cost of one more row — 18,292 B for 20 tool rows less 1,255 B for one, and
# 50,311 B for 25 call rows less 2,346 B for one — with the rows' own `input` and `text` heads
# taken back off. The call figure was measured at `tools=1`, so it still carries one tool row
# that the arithmetic below counts again: a ceiling should err high. The fixture rows are
# redacted to a few characters and project nothing about either.
MEASURED_TOOL_ROW_MARKUP = 700
MEASURED_CALL_ROW_MARKUP = 2_000
# What a row of the records browser really costs — the preview plus the row's own markup, most
# of it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` on
# 2026-08-08: 83,659 B for a 100-record page less 1,865 B of chrome, over the 99 rows between.
# The fixture records are redacted to a few characters, so they project nothing about this.
MEASURED_RECORD_BYTES = 826

# The most one character of a transcript's own content can weigh on the page that shows it.
# Content has no shape at all — a tool wrote the file, a model wrote the text — so every bound
# over it holds for the worst character rather than the measured average. Markupsafe's longest
# escape is five bytes (`&amp;`, `&#34;`, `&#39;`), and the longest UTF-8 encoding is four, so
# five bytes a character covers both.
ESCAPED_CHAR_BYTES = 5


def worst_call_bytes(page_tools: int) -> int:
    """What one call row of a turn fragment can weigh: markup, text head, and its tool rows.

    Markup is measured, because it is ours; the two content heads are counted at the worst
    character, because a call's `text` and a tool's `input` are whatever the session held.
    """
    tool_row = MEASURED_TOOL_ROW_MARKUP + INPUT_CHARS * ESCAPED_CHAR_BYTES
    return MEASURED_CALL_ROW_MARKUP + TEXT_CHARS * ESCAPED_CHAR_BYTES + page_tools * tool_row


def worst_record_bytes() -> int:
    """What one row of the records browser can weigh: its markup, and a preview of `&`."""
    return (
        MEASURED_RECORD_BYTES - queries.RECORD_PREVIEW + queries.RECORD_PREVIEW * ESCAPED_CHAR_BYTES
    )


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
    rather than at a page's worth of them. Rendering is the other half of that promise, and
    the planted leaf below holds it: what a fragment serves stays proportional to what the
    store holds, however the value nests.
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
    assert QUERIES["view_turn_calls"].params["page_calls"].default == 10
    assert QUERIES["view_call_tools"].params["page_tools"].default == 12
    assert QUERIES["view_records"].params["page_records"].default == 100
    assert QUERIES["view_records"].params["preview_chars"].default == 160
    assert QUERIES["view_offload"].params["chunk_chars"].default == 50_000
    # Every ceiling is projected at the largest page a URL can ask for, because a size is
    # something a reader types. The turn fragment's two sizes multiply, so its ceiling is
    # spent by the defaults themselves and `?calls=` only goes down from here.
    assert MAX_PAGE_CALLS * worst_call_bytes(MAX_PAGE_TOOLS) < PAGE_BYTES
    assert MAX_PAGE_RECORDS * worst_record_bytes() < PAGE_BYTES
    assert MAX_CHUNK_CHARS * ESCAPED_CHAR_BYTES < PAGE_BYTES
    # And no default asks for more than its own ceiling allows, which nothing else checks: a
    # default above the ceiling serves a 400 to a reader who typed no size at all.
    assert queries.PAGE_CALLS <= MAX_PAGE_CALLS
    assert queries.PAGE_TOOLS <= MAX_PAGE_TOOLS
    assert queries.PAGE_RECORDS <= MAX_PAGE_RECORDS
    assert queries.CHUNK_CHARS <= MAX_CHUNK_CHARS


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
    # The largest page anyone can ask for is the most the list ever serves, so that is the
    # number under the ceiling — projecting the default instead leaves `?size=` above it
    # unchecked, and a URL is what a reader pastes.
    assert chrome + per_session * MAX_PAGE_SESSIONS < PAGE_BYTES
    # The fixture rows are redacted and short, so the projection above is optimistic about a
    # real corpus. `data/traces.duckdb` served 1,015 B a row on 2026-08-08 — measured, not
    # assumed — which is what `MAX_PAGE_SESSIONS` was set from.
    assert MAX_PAGE_SESSIONS * MEASURED_SESSION_BYTES < PAGE_BYTES
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        assert len(client.get(f"/session/{session_id}").content) < PAGE_BYTES, session_id


# One real URL per route the app exposes, keyed by the route's own path template. The sweep
# below reads this as a set, so a route added with no entry fails rather than going unswept.
ROUTES: dict[str, str] = {
    "/": "/",
    "/session/{session_id}": f"/session/{SPINE}",
    "/session/{session_id}/run/{run_id}": f"/session/{SPINE}/run/{SPINE_RUN}",
    "/fragment/turn/{session_id}/{source}/{turn_id}": (
        f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}"
    ),
    "/fragment/tools/{session_id}/{source}/{api_call_id}": (
        f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/text/{session_id}/{source}/{api_call_id}": (
        f"/fragment/text/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/thinking/{session_id}/{source}/{api_call_id}": (
        f"/fragment/thinking/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/tool/{session_id}/{source}/{tool_call_id}": (
        f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
    ),
    "/session/{session_id}/records/{source}": f"/session/{ANCESTOR}/records/main",
    "/fragment/record/{session_id}/{source}/{line_no}": f"/fragment/record/{ANCESTOR}/main/1",
    "/session/{session_id}/offload/{name:path}": f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}",
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
        [FORK_ORIGIN, FORK_ORIGIN_RUN, DENSE_CALL],
    )
    assert total == 4, "the densest fixture call moved: re-pick DENSE_CALL"
    url = f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
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


def test_an_offload_of_nothing_but_escapes_still_serves_under_the_ceiling(
    plant: Planter,
) -> None:
    """The largest chunk anyone can ask for stays under the ceiling however the file escapes.

    Every other bound here rests on a measured cost per row. An offload can't: it holds a file
    a tool wrote, and a chunk of pure `&` weighs five times what the same chunk of prose does.
    The content is invented for exactly that reason — no recorded offload is adversarial, and
    the point of the leaf is the character no corpus happens to contain.
    """
    escapes = "&" * MAX_CHUNK_CHARS
    path = plant(
        ("UPDATE offload_files SET content = ? WHERE session_id = ?", [escapes, CONFIG_ONLY])
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(
            f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}", params={"size": MAX_CHUNK_CHARS}
        )
    assert page.status_code == 200
    # Served whole — the chunk is not silently cut — and still under the ceiling.
    assert page.text.count("&amp;") == MAX_CHUNK_CHARS
    assert len(page.content) < PAGE_BYTES


def test_a_turn_fragment_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """A call row and a tool row weigh no more than the ceiling's arithmetic gives them.

    The row costs behind that arithmetic were measured against the canonical store once, and a
    template that grows a row past them puts the ceiling out by the page size it multiplies.
    The content is planted `&` at both caps — the character that escapes to five bytes — for
    the same reason the offload leaf plants one: no recorded row is adversarial.
    """
    path = plant(
        ("UPDATE api_calls SET text = ? WHERE session_id = ?", ["&" * TEXT_CHARS, ANCESTOR]),
        ("UPDATE tool_calls SET input = ? WHERE session_id = ?", ["&" * INPUT_CHARS, FORK_ORIGIN]),
    )
    turn = f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}"
    tools = f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    with TestClient(build_app(path)) as planted:

        def served(url: str, **params: int) -> int:
            response = planted.get(url, params={"after": -1, **params})
            assert response.status_code == 200
            return len(response.content)

        # What one more call row costs on a fragment, and one more tool row — the marginal
        # cost, so the fragment's own chrome is not counted against a row's budget.
        call_row = served(turn, calls=2, tools=1) - served(turn, calls=1, tools=1)
        tool_rows = served(tools, tools=4) - served(tools, tools=1)
    assert call_row <= worst_call_bytes(1)
    assert tool_rows / 3 <= MEASURED_TOOL_ROW_MARKUP + INPUT_CHARS * ESCAPED_CHAR_BYTES


def test_a_deeply_nested_value_is_served_at_the_size_it_was_stored(plant: Planter) -> None:
    """A per-value fetch serves the value it names, not what indenting could turn it into.

    Indenting is the one thing that can break the per-value exemption above, because it is
    quadratic in nesting: 10 KB of nothing but `[` indents to 50 MB, and past the parser's
    own stack the fragment answered 500 rather than anything at all. Both values are invented
    and have to be — nothing recorded nests remotely this deep, which is the point.
    """
    indents_huge = "[" * 5_000 + "]" * 5_000
    overflows_the_parser = "[" * 10_000 + "]" * 10_000
    path = plant(
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
            [indents_huge, indents_huge, FORK_ORIGIN],
        ),
        ("UPDATE raw_records SET raw = ? WHERE session_id = ?", [overflows_the_parser, ANCESTOR]),
    )
    with TestClient(build_app(path)) as planted:
        tool = planted.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}")
        record = planted.get(f"/fragment/record/{ANCESTOR}/main/1")
    # Each fragment answers, and weighs the values it names plus a page of chrome at most.
    for response, stored in ((tool, 2 * len(indents_huge)), (record, len(overflows_the_parser))):
        assert response.status_code == 200
        assert len(response.content) < stored + PAGE_BYTES


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
        tools = planted.get(f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}").text
    # ...and what each of them shows is its cap, not the value.
    assert len(fields(page, "data-turn", turn_id)["prompt"]) == PROMPT_CHARS
    assert len(fields(turn, "data-api-call", DENSE_TURN_CALL)["text_head"]) == TEXT_CHARS
    assert len(fields(tools, "data-tool-call", DENSE_TOOL)["input_head"]) == INPUT_CHARS
