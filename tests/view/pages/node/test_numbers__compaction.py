"""The popover behind a compaction's NavTree row: the window it dropped, and what asked for it.

A compaction is the one node of a session made of no api calls at all, so there is no spend
here and no model — what the boundary record holds is the two token counts either side of the
drop (`docs/schema.md`). The ⊟ row draws the span between them as a bar read backwards, and
this popover is that span in figures.

The expectations come out of `live_compactions` in the test's own SQL, so the popover has
nothing to agree with but the store. Every other kind's popover is `test_numbers.py`.
"""

import duckdb
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hyphae.view.nodes import NUMBERS_URL, Kind
from tests.conftest import COMPACTED, COMPACTED_BOUNDARY, MAIN
from tests.view.conftest import one, values, wired
from tests.view.pages.node.test_numbers import popover, popped

# Where the corpus's first recorded compaction is fetched from, and the key its row carries.
PATH = f"/session/{COMPACTED}/thread/{MAIN}/{Kind.COMPACTION}/{COMPACTED_BOUNDARY}"
KEY = f"{Kind.COMPACTION}:{COMPACTED_BOUNDARY}"


def test_a_compaction_says_the_window_it_gave_back_and_what_asked_for_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Where the window stood either side of the drop, the span between, and the trigger.

    The three numbers are one subtraction, and the point of printing all three is that the bar
    on the row draws only the span: a reader who wants to know whether a compaction was worth
    what it cost needs the two ends it ran between. Read here off `live_compactions` rather
    than off `view_compactions`, which is what the row itself was drawn from — the two would
    otherwise be one derivation agreeing with itself.
    """
    pre, post, trigger = one(
        store,
        "SELECT pre_tokens, post_tokens, trigger FROM live_compactions"
        " WHERE session_id = ? AND source = ? AND id = ?",
        [COMPACTED, MAIN, COMPACTED_BOUNDARY],
    )
    # The fixture's boundary really did give window back, which is what makes the span a
    # reading and not a zero the assertions below would pass on either way.
    assert pre > post > 0
    printed = popover(client, PATH, KEY)
    assert printed["pre_tokens"] == f"{pre:,}"
    assert printed["post_tokens"] == f"{post:,}"
    assert printed["freed"] == f"{pre - post:,}"
    assert printed["trigger"] == trigger
    # And nothing a compaction has no answer for: it is made of no api calls, so a dollar or a
    # window here would be a figure attributed to a node that spent nothing.
    assert "cost_usd" not in printed and "window" not in printed


def test_a_compactions_popover_cites_the_query_it_was_fetched_by(client: TestClient) -> None:
    """The fragment carries its own citation line, keys and all.

    A popover arrives on a page already served, so it cannot ride the footer the pages share.
    Pinned here rather than in the sweeps: `tests/view/test_query.py` reads pages and skips
    every `/fragment/` route, and `test_app.py`'s fragment sweep covers the whole-value
    fetches. A numbers fragment is cited nowhere else.
    """
    assert values(popped(client, PATH), "data-query") == [
        f"-- queries/view_numbers_compaction.sql session_id={COMPACTED} source={MAIN}"
        f" compaction_id={COMPACTED_BOUNDARY} chip_chars=60"
    ]


def test_a_compaction_row_fetches_the_route_the_app_actually_mounts(client: TestClient) -> None:
    """The ⊟ row's URL resolves to the compaction handler and not the generic one.

    A compaction's path is shaped like every other node's — `.../thread/{source}/{kind}/{id}` —
    so the route serving turns, calls and tool calls matches it too, and that one 404s on a
    kind it has no query for. Which of the two answers is decided by the order `build_app`
    registers them, and nothing but this reads that order back. Matched against what the app
    mounted rather than against a path written out here, so a route that moved is a failure
    rather than a test quietly checking a string against itself.
    """
    page = client.get(f"/session/{COMPACTED}").text
    fetched = {
        row: at for row, at in wired(page, "data-nav-tree") if at["hx-get"].startswith(NUMBERS_URL)
    }
    assert KEY in fetched, "the compaction's row mints no popover URL"
    url = fetched[KEY]["hx-get"]
    answering = [
        route.name
        for route in client.app.routes  # pyrefly: ignore
        if isinstance(route, APIRoute) and route.path_regex.fullmatch(url)
    ]
    assert answering, f"no mounted route answers {url}"
    assert answering[0] == "compaction_numbers", answering
    assert client.get(url).status_code == 200
