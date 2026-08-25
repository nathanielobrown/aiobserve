"""Reading a session in order: the prev/next controls beside the pane.

Neither control descends. A click on a tree row is how a reader goes down, so these two go
along the level the reader is standing on — the next row, then the next — and at the end of it
out to whatever follows the thing that level sits in. Prev is the same level backwards, and
from its first row the node that holds it. A step that changes level is marked, because a
reader who did not ask to leave the branch should see it coming.

These leaves follow the controls themselves rather than calling `walk.py`: what a reader gets
is the chain of pages, and only fetching them proves the chain closes. The expectation is read
off the tree each page was served with — the rows at the selection's own depth are its level,
in the order the tree drew it — so the reading order is checked against what the reader sees
rather than derived from the store a second time.
"""

import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from tests.conftest import FORK_ORIGIN, MAIN, SPINE
from tests.view.conftest import fields, inside, kin, one, pages, plain, rows, values


class Page:
    """One page the walk stepped on: where it sits, and the tree it was served with."""

    def __init__(self, url: str, html: str) -> None:
        self.url = url
        # The crumbs are the open path, outermost first and ending at the selection, so the
        # last is this page's own node and the rest is where it hangs.
        self.chain = tuple(values(html, "data-crumb"))
        self.key = self.chain[-1]
        self.html = html

    @property
    def levels(self) -> list[list[str]]:
        """Each open level of the tree, outermost first — the level each crumb stands in.

        The tree opens one path, so the rows at depth `d` are the whole of the level the
        `d`-th crumb sits in and nothing else. A cap would cut one, which is why the sweep
        checks no level was cut before reading a level off a page.
        """
        found: dict[int, list[str]] = {}
        for depth, key in rows(self.html):
            found.setdefault(depth, []).append(key)
        return [found[depth] for depth in range(len(self.chain))]

    @property
    def expected(self) -> dict[str, tuple[str, bool] | None]:
        """Where each control should go and whether it climbs, read off this page's own tree."""
        levels, chain = self.levels, self.chain
        after_it: tuple[str, bool] | None = None
        for depth in range(len(chain) - 1, 0, -1):
            level = levels[depth]
            after = level.index(chain[depth]) + 1
            if after < len(level):
                after_it = (level[after], depth != len(chain) - 1)
                break
        previous: tuple[str, bool] | None = None
        if len(chain) > 1:
            place = levels[-1].index(chain[-1])
            previous = (levels[-1][place - 1], False) if place else (chain[-2], True)
        return {"previous": previous, "next": after_it}


# The arrow each control shows, by direction and by whether the step leaves the level: along
# the level it points the way the reader is going, and out of it both point up. This is the
# half a reader sees — `data-climb` is a hook for these leaves, and the stylesheet reads
# neither — so the two are checked as one claim below.
ARROW = {("previous", False): "\u2190", ("next", False): "\u2192"}
CLIMB = "\u2191"


def shown(html: str, named: str) -> str:
    """The arrow one control shows: leading on prev, trailing on next, as each points away."""
    found = re.search(rf'<button[^>]*data-walk="{named}"[^>]*>(.*?)</button>', html, re.DOTALL)
    assert found is not None, f"no {named} control on the page"
    text = plain(found.group(1)).strip()
    return text[0] if named == "previous" else text[-1]


def control(html: str, named: str) -> tuple[str, bool] | None:
    """What one control on a served page points at, and whether it is marked as a climb."""
    found = inside(html, "data-walk", named, "data-node")
    if not found:
        return None
    climbed = bool(inside(html, "data-walk", named, "data-climb"))
    assert shown(html, named) == (CLIMB if climbed else ARROW[(named, climbed)]), named
    return found[0], climbed


def follow(client: TestClient, start: str, named: str) -> list[Page]:
    """Every page one control reaches from `start` without leaving the level, `start` first.

    Stops at the row whose control climbs, or where there is no control at all: what this
    returns is one level, walked. The cap is the corpus's own size with room to spare — a walk
    that did not close would loop here rather than hang.
    """
    walked: list[Page] = []
    url: str | None = start
    while url is not None:
        served = client.get(url)
        assert served.status_code == 200, f"{url}: {served.status_code}"
        walked.append(Page(url, served.text))
        step = control(served.text, named)
        url = (
            None
            if step is None or step[1]
            else inside(served.text, "data-walk", named, "hx-get")[0]
        )
        assert len(walked) < 500, f"{start}: the walk did not end"
    return walked


def first_child(client: TestClient, at: str) -> str:
    """The URL of the first row of the level under one page's selection.

    Where a walk of that level starts: the controls never descend, so a leaf that wants to
    read a level has to arrive on it the way a reader does, by clicking a tree row.
    """
    html = client.get(at).text
    (href,) = inside(html, "data-tree", kin(html)[0], "href")
    return href


def deep_turn(store: duckdb.DuckDBPyConnection) -> str:
    """A turn with siblings on both sides and more than one call under it.

    The level below it is what the leaves walk, and the turns beside it are what its own level
    offers a climb out to — so this one turn exercises stepping along a level and both ways out.
    """
    (turn_id,) = one(
        store,
        'SELECT t.id FROM live_turns t WHERE t.session_id = ? AND t.source = ? AND t."index" > 0'
        " AND (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id"
        "   AND c.source = t.source AND c.turn_id = t.id) > 1"
        " AND EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id"
        '   AND o.source = t.source AND o."index" > t."index")'
        ' ORDER BY t."index" LIMIT 1',
        [SPINE, MAIN],
    )
    return str(turn_id)


def test_every_control_in_the_corpus_walks_its_own_level_or_climbs_out_of_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Neither control ever descends: every page in the corpus, both controls, against its tree.

    The claim is the whole rule at once — next is the following row of the reader's own level,
    or, at the end of it, what follows the branch they are in; prev is the row ahead of them,
    or the node that holds the level. A control that stepped into a node's children would land
    somewhere no level on the page holds, and fail here.
    """
    seen: set[str] = set()
    for url in pages(store):
        if not url.startswith("/session/"):
            continue
        html = client.get(url).text
        page = Page(url, html)
        # The expectation is a level read off the tree, so a level the cap cut would make it a
        # different claim. Nothing in this corpus comes near the window.
        assert values(html, "data-more") == [], url
        for named, expected in page.expected.items():
            assert control(html, named) == expected, (url, named)
            if expected is not None:
                seen.add(f"{named}:{expected[1]}")
    # And every arm of the rule was reached: both controls, each stepping along a level and
    # each climbing out of one. An arm the corpus never reaches is an arm nothing above pins.
    assert seen == {"previous:True", "previous:False", "next:True", "next:False"}


def test_the_two_controls_walk_one_level_and_mark_the_way_out_of_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A reader who keeps pressing next reads the level they are on, in the tree's order.

    Followed as a reader follows it — each page fetched, the next control read off what came
    back — so the leaf proves the chain closes rather than that one page's markup is right.
    Both ways out of the level are read at the ends: the row after the last is the turn's own
    next sibling, and the row before the first is the turn.
    """
    turn = deep_turn(store)
    at = f"/session/{SPINE}/thread/{MAIN}/turn/{turn}"
    level = kin(client.get(at).text)
    assert len(level) > 1, "a level with more than one row to walk"
    forward = follow(client, first_child(client, at), "next")
    assert [page.key for page in forward] == level
    # The end of the level climbs out of it, to the row after the turn the level hangs under.
    turns = Page(at, client.get(at).text).levels[1]
    assert control(forward[-1].html, "next") == (turns[turns.index(f"turn:{turn}") + 1], True)
    # And the same level backwards, out through the turn itself.
    back = follow(client, forward[-1].url, "previous")
    assert [page.key for page in back] == list(reversed(level))
    assert control(back[-1].html, "previous") == (f"turn:{turn}", True)


@pytest.mark.parametrize("session_id", [SPINE, FORK_ORIGIN])
def test_a_session_is_read_from_its_tree_and_not_from_the_controls(
    client: TestClient, session_id: str
) -> None:
    """A session page offers no step in either direction: it is the only node at its level.

    Which is the shape of the whole design — the controls read one level, and going down into
    the session is what the tree is for. `FORK_ORIGIN` is here for the nesting: a session that
    spawned runs that spawned runs still offers nothing, because depth is not what they walk.
    """
    html = client.get(f"/session/{session_id}").text
    assert values(html, "data-walk") == []
    # And the tree it was served with does hold the level a reader goes down into, so the
    # absence above is the controls' rule and not an empty page.
    assert kin(html)


def test_a_control_says_what_the_neighbour_is_and_what_it_was(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A control names the neighbour's kind and its label — the same label its tree row carries.

    A reader deciding whether to step has the node's own words, not the word "next". The kind
    is printed rather than left in an attribute: a step can climb out of the level, and a
    reader who cannot see that has no warning before it.
    """
    # The thread's own level, which is the longest the corpus offers: four turns in a row.
    walked = follow(client, first_child(client, f"/session/{SPINE}"), "next")
    step = walked[1]
    for named, neighbour in (("previous", walked[0]), ("next", walked[2])):
        assert control(step.html, named) == (neighbour.key, False)
        # Both halves are text on the page: what the neighbour is, and what it is called. The
        # label is the one the neighbour's own tree row carries — one node, one name, wherever
        # it is read.
        kind, _, _ = neighbour.key.partition(":")
        assert fields(step.html, "data-walk", named) == {
            "kind": kind,
            "label": fields(neighbour.html, "data-selected", neighbour.key)["label"],
        }


def test_the_walk_is_the_same_however_the_tree_is_capped(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?kin=` cuts the tree, never the reading order: the walk reads the store, not the rows.

    The cap is dropped to one child a level, which is the smallest the knob goes, so the tree
    beside the pane loses everything but the open path — and the controls do not move.
    """
    for page in follow(client, f"/session/{SPINE}/thread/{MAIN}/turn/{deep_turn(store)}", "next"):
        capped = client.get(f"{page.url}?kin=1")
        assert capped.status_code == 200, page.url
        for named in ("previous", "next"):
            assert control(capped.text, named) == control(page.html, named), (page.key, named)
