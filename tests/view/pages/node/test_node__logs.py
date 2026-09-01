"""The children log: one page of one kind of child, and the words above its columns.

A pane lists one kind of child at a time, `?log=` steps through the level a page at a time, and
the head says which of how many. These leaves walk every shape of log through every page it
has, and read the head back off the served table rather than off the column table behind it.
"""

import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view.nodes import BODY_URL
from hyphae.view.pages.node.columns import COLUMNS, Shape
from hyphae.view.pages.node.knobs import numbered
from hyphae.view.text.labels import label
from tests.view.conftest import (
    fields,
    headings,
    icons,
    inside,
    one,
    values,
)
from tests.view.selections import (
    LEVELS,
    TURN,
    node_url,
)

# One cell of a log, and the class it carries — the attributes htpy writes in that order, and
# no class at all where the column declares none.
CELL = re.compile(r'<td data-column="([^"]+)"(?: class="([^"]+)")?')


@pytest.mark.parametrize("parent", list(LEVELS))
def test_every_shape_of_log_serves_the_page_asked_for_and_counts_its_level(
    client: TestClient, store: duckdb.DuckDBPyConnection, parent: str
) -> None:
    """A page holds what the URL asked for, and the heading above it counts the level.

    Swept per shape at `?log=1`: the corpus's widest level is five children against a page of a
    hundred, so at the production size every page is its whole level and both clauses read true
    however the code got there. One row a page is what tells a page from the level it came from.
    """
    sql, template, shape = LEVELS[parent]
    url = template.format(*one(store, sql))
    children = values(client.get(url).text, "data-child")
    assert len(children) > 1, f"{url}: the widest {parent} has to hold a level worth paging"
    for number, child in enumerate(children, start=1):
        page = client.get(url, params={"log": 1, "page": number}).text
        # The page is the one row the URL asked for, in the level's own order...
        assert values(page, "data-child") == [child], f"{url} page {number}"
        # ...under a heading counting the level rather than the row beneath it...
        assert fields(page, "data-log", shape)["children"] == str(len(children)), url
        # ...and a pager placing the page in the level.
        place = fields(page, "data-pager", shape)["place"]
        assert place == f"Page {number} of {len(children)}", url


def walked_log(client: TestClient, at: str, held: int) -> list[str]:
    """Every child a log lists, gathered by following its pager from the page given.

    Bounded by the level's own size: a pager that offered a way on from its last page would
    otherwise walk for as long as the store answers.
    """
    found: list[str] = []
    following: str | None = at
    for _ in range(held + 1):
        if following is None:
            return found
        page = client.get(following).text
        found += values(page, "data-child")
        onward = inside(page, "data-page", "next", "href")
        following = onward[0] if onward else None
    raise AssertionError(f"{at}: the pager never reached a last page")


def test_a_children_log_pages_by_number_and_counts_the_whole_level(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The log is one numbered page of a level, and the heading counts the level.

    Driven below the corpus's fan-out with `?log=`, because no recorded turn has more children
    than the production page. What is read is that the pages concatenate to the level exactly
    once, that each says which of how many it is, and that the count above them is the level's
    own — a heading counting the rows in front of the reader says a turn of four calls has one.
    """
    whole = client.get(TURN).text
    children = values(whole, "data-child")
    assert len(children) > 2, "the log has to have something to page"
    # One child to a page: the first page holds the first child...
    first = client.get(TURN, params={"log": 1}).text
    assert values(first, "data-child") == children[:1]
    # ...under a heading counting the whole level rather than the row beneath it...
    assert fields(first, "data-log", "calls")["children"] == str(len(children))
    # ...and a pager saying which page of how many this is.
    assert fields(first, "data-pager", "calls")["place"] == f"Page 1 of {len(children)}"
    # The first page offers no way back, and its way on is numbered rather than a cursor.
    assert not inside(first, "data-page", "previous", "href")
    (onward,) = inside(first, "data-page", "next", "href")
    assert "page=2" in onward and "after=" not in onward
    second = client.get(onward).text
    assert values(second, "data-child") == children[1:2]
    assert fields(second, "data-pager", "calls")["place"] == f"Page 2 of {len(children)}"
    # The way back from the second page lands on the first, which is the page with no number.
    (back,) = inside(second, "data-page", "previous", "href")
    assert back == f"{TURN}?log=1"
    assert values(client.get(back).text, "data-child") == children[:1]
    # Walking forward lands on every child exactly once, in the level's own order.
    assert walked_log(client, f"{TURN}?log=1", len(children)) == children


def test_a_level_divides_into_the_pages_it_has_and_no_empty_one(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The page count is the level's own arithmetic, at any size a URL asks for.

    Read at three sizes against one recorded level: one that divides it, one that leaves a
    remainder, and one that holds the whole thing. The arithmetic is where a paginator goes
    wrong, and the failure is quiet — an off-by-one mints a last page with nothing on it.
    """
    children = values(client.get(TURN).text, "data-child")
    held = len(children)
    for size, count in ((1, held), (held - 1, 2), (held, 1)):
        for number in range(1, count + 1):
            page = client.get(TURN, params={"log": size, "page": number}).text
            assert values(page, "data-child") == children[(number - 1) * size : number * size]
            # Every page of the level says the same total, and its own place in it...
            assert fields(page, "data-log", "calls")["children"] == str(held)
            if count > 1:
                assert fields(page, "data-pager", "calls")["place"] == f"Page {number} of {count}"
        # ...and one page past the last is nothing at all, rather than an empty log that reads
        # as a node with no children.
        assert client.get(TURN, params={"log": size, "page": count + 1}).status_code == 404
    # A level that fits on one page carries no pager: there is no page to go to.
    assert "data-pager" not in client.get(TURN, params={"log": held}).text
    # And a page number below the first is a bad ask rather than a miss: no level has one, so
    # it is the number that is wrong and not the node — the answer every other size a URL
    # carries gives (`checked`).
    assert client.get(TURN, params={"page": 0}).status_code == 400
    # A level with nothing in it counts nothing. The count comes off the page's own rows, so an
    # empty page is the one place it has no row to read it from.
    empty = store.execute(
        "SELECT c.session_id, c.source, c.id FROM live_api_calls c"
        " LEFT JOIN live_tool_calls t"
        " ON t.session_id = c.session_id AND t.api_call_id = c.id"
        " GROUP BY ALL HAVING count(t.id) = 0 ORDER BY 1, 2, 3 LIMIT 1"
    ).fetchone()
    assert empty, "the corpus has to hold an api call that called no tool"
    session_id, source, call_id = empty
    childless = client.get(f"/session/{session_id}/thread/{source}/call/{call_id}").text
    assert fields(childless, "data-log", "tools")["children"] == "0"
    assert "data-pager" not in childless


def test_the_bucket_that_pages_in_memory_walks_the_same_way_the_query_does(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The unattached bucket's log pages by slicing, and owes what the queried log owes.

    Its runs arrive with the session's, which every level of the NavTree needs anyway, so this one
    level cuts a list it already holds instead of asking the store for a page. Read on the one
    recorded bucket that holds more than one run: the pages have to concatenate to the level,
    the heading has to count the level rather than the page, and the last page has to be last.
    """
    sessions = [str(row[0]) for row in store.execute("SELECT id FROM sessions").fetchall()]
    bucketed = [
        (f"/session/{session_id}/unattached", page.text)
        for session_id in sessions
        if (page := client.get(f"/session/{session_id}/unattached")).status_code == 200
        and len(values(page.text, "data-child")) > 1
    ]
    assert bucketed, "the corpus has a bucket holding more than one unattached run"
    at, whole = bucketed[0]
    children = values(whole, "data-child")
    first = client.get(at, params={"log": 1}).text
    assert values(first, "data-child") == children[:1]
    assert fields(first, "data-log", "runs")["children"] == str(len(children))
    assert fields(first, "data-pager", "runs")["place"] == f"Page 1 of {len(children)}"
    # Walking to the end lands on every run exactly once, in the level's own order...
    assert walked_log(client, f"{at}?log=1", len(children)) == children
    # ...and the whole level on one page ends the walk there.
    assert "data-pager" not in client.get(at, params={"log": len(children)}).text
    assert client.get(at, params={"log": len(children), "page": 2}).status_code == 404


def test_the_page_the_log_opens_at_is_the_url_with_no_page_on_it(client: TestClient) -> None:
    """`?page=1` serves the same page the URL without it serves.

    The two have to agree or a reader who pages back to the start gets a different document
    from the one they were linked, and the payload sweep prices only one of them.
    """
    bare = client.get(TURN)
    opened = client.get(TURN, params={"page": 1})
    assert opened.status_code == bare.status_code == 200
    assert values(opened.text, "data-child") == values(bare.text, "data-child")
    # Which is what the helper every pager link is minted through says: the first page is the
    # node's own URL, and a later one hangs off whatever knobs the reader is carrying. A `&`
    # where a `?` belongs is a 404, so both arms are read.
    assert numbered(TURN, "", 1) == TURN
    assert numbered(TURN, "?log=1", 1) == f"{TURN}?log=1"
    assert numbered(TURN, "", 3) == f"{TURN}?page=3"
    assert numbered(TURN, "?log=1", 3) == f"{TURN}?log=1&page=3"


@pytest.mark.parametrize("parent", list(LEVELS))
def test_every_children_log_heads_the_columns_its_rows_fill(
    client: TestClient, store: duckdb.DuckDBPyConnection, parent: str
) -> None:
    """The log is a table: one head naming the columns, and every row filling all of them.

    The reason it is a table at all — a row of bare numbers is unreadable, and a reader who
    cannot tell an api-call count from a tool-call count from a time of day is reading nothing.
    So the contract is that head and row agree, column for column, in order: a cell rendered
    under some other column's heading is a number attributed to the wrong question.

    Swept per shape, because the columns are the shape's own — a turn's children are counted
    by what a call did, a call's by what a tool answered.
    """
    sql, template, shape = LEVELS[parent]
    url = template.format(*one(store, sql))
    page = client.get(url).text
    named = [column.field for column in COLUMNS[Shape(shape)]]
    # The head names the shape's columns, in the order the shape declares them...
    assert inside(page, "data-columns", shape, "data-column") == named, url
    # ...each heading an icon over a word from the registry every header on the page reads...
    headed = headings(page)
    # ...each a column heading a screen reader can attribute a cell to...
    assert inside(page, "data-columns", shape, "scope") == ["col"] * len(named), url
    assert headed == {
        column.field: f"{column.icon} {label(column.field)}" for column in COLUMNS[Shape(shape)]
    }, url
    # ...each cell wearing the class its own column declares, which is what right-aligns a
    # number and keeps a time on one line. Read off the served cells against `COLUMNS`, so a
    # cell given a class the column table does not give it fails here rather than quietly
    # stopping being aligned.
    assert dict(CELL.findall(page)) == {
        column.field: column.css for column in COLUMNS[Shape(shape)]
    }, url
    # ...and every row fills every one of them, so no cell sits under a heading not its own.
    children = values(page, "data-child")
    assert children, url
    for key in children:
        assert inside(page, "data-child", key, "data-column") == named, (url, key)
    # And what a row opens spans exactly those columns. `columns.LISTED` says which shape of log
    # a kind lists in, and the expansion's span is read off it — a kind mapped to the wrong
    # shape opens a row narrower or wider than the table it lands in. Checked here, against
    # the page that did the listing, because this is where the shape is known to be right.
    (mount,) = [
        at for at in inside(page, "data-child", children[0], "hx-get") if at.startswith(BODY_URL)
    ]
    body = client.get(mount)
    assert body.status_code == 200, mount
    assert values(body.text, "colspan") == [str(len(named))], mount


# The three kinds a parent's children log counts in a column of its own, beside that column.
SHARED = {"call": "api_calls", "tool": "tool_calls", "run": "agent_type"}


def test_a_kind_is_marked_the_same_in_the_nav_tree_and_in_the_column_that_counts_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A column head and a NavTree row are one reader meeting one thing twice, so they agree.

    `⇄` over a turn's api-call count and `⇄` on an api call's own row are the same fact said in
    two places, and a reader who learned the mark in a table head has to find it again in the
    tree. Both sides are read off served pages rather than off the table behind them, so a
    mapping that let the two drift would show up here as two characters.
    """
    headed: dict[str, str] = {}
    for sql, template, _ in LEVELS.values():
        log = client.get(template.format(*one(store, sql))).text
        headed |= headings(log)
        # The head is where a log says its kind, and the only place: a mark on every row
        # under it would be the same character down a column that already means it, at
        # 49 bytes a row on a page whose tree spends four fifths of the budget.
        assert not [key for key in values(log, "data-child") if icons(log, "data-child", key)]
    for kind, field in SHARED.items():
        page = client.get(node_url(store, kind)).text
        (selected,) = values(page, "data-selected")
        (mark,) = icons(page, "data-nav-tree", selected)
        # The heading is the mark and then the word for the column, which is what `headings`
        # reads back with its whitespace collapsed.
        assert headed[field].startswith(f"{mark} "), (kind, field, headed[field])


def test_a_log_row_opens_the_body_from_a_button_that_says_so(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The expansion is a labelled control, and what it opens stands in the log's own table.

    A `<details>` summary said `body` and looked like text; a reader has to be able to see
    that a row can be opened. And what arrives is a row of the same table — the fragment is
    swapped in after the row that asked for it, so a body wrapped in anything but a `<tr>`
    lands outside the table the browser is drawing.
    """
    url = LEVELS["call"][1].format(*one(store, LEVELS["call"][0]))
    page = client.get(url).text
    for key in values(page, "data-child"):
        # The control names the row it opens, and it is a button rather than a disclosure.
        assert inside(page, "data-child", key, "data-view") == [key], key
        (mount,) = [
            at for at in inside(page, "data-child", key, "hx-get") if at.startswith(BODY_URL)
        ]
        served = client.get(mount)
        assert served.status_code == 200, mount
        # The body arrives as one row spanning the table it opens under.
        assert served.text.lstrip().startswith("<tr"), mount
        (span,) = inside(served.text, "data-expansion", "tool", "colspan")
        assert span == str(len(COLUMNS[Shape.TOOLS])), mount
    # And the disclosure the button replaced is gone from the log. Scoped to the log because
    # the page footer keeps one for the queries it ran, which no reader has to find to read
    # a row.
    (log,) = re.findall(r'<section class="log".*?</section>', page, flags=re.DOTALL)
    assert "<details" not in log
