"""`?nav=` picks which children a level shows, with the path down to the reader left open.

The design's kind x preset table is `trees.py:cell`, and these leaves are what spend it: every
kind under every preset, the levels an open path keeps whatever the preset filters, the control
that offers the presets, and the preset riding every link the page mints so a reader who picked
one keeps it.
"""

from collections.abc import Sequence
from html import unescape
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view.nodes import (
    Kind,
    Preset,
)
from tests.conftest import MAIN, SPINE
from tests.view.conftest import (
    inside,
    kin,
    one,
    rows,
    values,
)
from tests.view.nav_trees import (
    candidates,
    cell,
    mounts,
    node_link,
    node_url,
    open_turn,
    spilled,
    url,
)


def richest(
    store: duckdb.DuckDBPyConnection, preset: Preset, kind: Kind, count: int
) -> list[tuple[str, str, str]]:
    """The nodes of one kind whose cell holds the most, fullest first.

    No recorded session holds every kind, and a cell read on an empty node passes by agreeing
    that nothing is there — so a cell is checked wherever the corpus fills it best.
    """
    ordered = sorted(
        candidates(store, kind), key=lambda at: (-len(cell(store, preset, kind, *at)), at)
    )
    return ordered[:count]


def sited(session_id: str, chain: Sequence[str]) -> list[tuple[Kind, str, str, str]]:
    """Each step of an open path as the arguments its own cell is read with.

    A key carries a kind and an id but not a thread, and the path is what supplies it: a node
    sits on `main` until the path passes through a run, and on that run's thread after it.
    """
    placed: list[tuple[Kind, str, str, str]] = []
    source = MAIN
    for key in chain:
        kind, _, node_id = key.partition(":")
        placed.append((Kind(kind), session_id, source, node_id))
        if Kind(kind) is Kind.RUN:
            source = node_id
    return placed


# An api call that made a `Task` call: the one call whose tool log mounts a body with a run
# link in it, read from the tool's side of the join `view_runs` makes.
SPAWNING_CALL = (
    "SELECT c.session_id, c.source, c.id FROM live_api_calls c"
    " JOIN live_tool_calls tc ON tc.session_id = c.session_id AND tc.source = c.source"
    "  AND tc.api_call_id = c.id"
    " JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id"
    "  AND tc.source <> a.id"
    " ORDER BY c.id LIMIT 1"
)


def mounting(store: duckdb.DuckDBPyConnection) -> list[str]:
    """One page per kind of body a children log can mount.

    A session's log mounts turns, a turn's mounts api calls, a call's mounts tool calls, and
    the unattached bucket's mounts runs — the only page whose rows reach `run_body`, the
    second of the two fragment routes. The call is one that spawned a run, because a tool body
    leads with the run it started and that link is the only node link a pane inside a fragment
    mints.
    """
    session_id, source, call_id = one(store, SPAWNING_CALL)
    loose, _, _ = candidates(store, Kind.UNATTACHED)[0]
    return [
        node_url(Kind.SESSION, str(session_id), MAIN, str(session_id)),
        url(open_turn(store)),
        node_url(Kind.CALL, str(session_id), str(source), str(call_id)),
        node_url(Kind.UNATTACHED, loose, MAIN, loose),
    ]


def mounted_kind(mount: str) -> str:
    """The kind of node a body mount opens, which its URL says just before the id."""
    return mount.partition("?")[0].rsplit("/", 2)[1]


# The cells no recorded session fills, which is not the same claim as an empty cell. A tool
# call and a compaction are leaves by the table; the bucket's `agents` cell is one the corpus
# happens not to reach, and the planted leaf below is what reaches it.
UNFILLED = {
    (Kind.TOOL, Preset.FULL),
    (Kind.TOOL, Preset.NO_API),
    *((Kind.COMPACTION, preset) for preset in Preset),
    (Kind.UNATTRIBUTED, Preset.AGENTS),
}


@pytest.mark.parametrize("preset", list(Preset))
@pytest.mark.parametrize("kind", list(Kind))
def test_every_kind_under_every_preset_opens_the_children_its_cell_defines(
    client: TestClient, store: duckdb.DuckDBPyConnection, kind: Kind, preset: Preset
) -> None:
    """The 24 cells of the design's table, each read off the page that renders it.

    One case per cell, checked on the node the corpus fills that cell fullest at, so a wrong
    cell reddens under its own name. `UNFILLED` is the other half: a cell that renders nothing
    has to be one the design or the corpus says is empty.
    """
    (picked,) = richest(store, preset, kind, 1)
    expected = cell(store, preset, kind, *picked)
    html = client.get(node_url(kind, *picked), params={"nav": preset}).text
    assert kin(html) == expected, picked
    assert bool(expected) == ((kind, preset) not in UNFILLED), picked
    # The fullest node cannot see a level that leaks sideways: children matched on the thread
    # alone would land under every node of the kind on it, and under the fullest one that is
    # the answer the cell wanted anyway. So the same cell is read again at a sibling on the
    # same thread that the corpus leaves empty, where a leak has nothing to hide behind.
    beside = [
        at
        for at in candidates(store, kind)
        if at[:2] == picked[:2] and at != picked and not cell(store, preset, kind, *at)
    ]
    if beside:
        empty = client.get(node_url(kind, *beside[0]), params={"nav": preset}).text
        assert kin(empty) == [], beside[0]


@pytest.mark.parametrize("preset", list(Preset))
def test_every_open_level_is_its_own_cell_or_the_full_one_that_holds_the_path(
    client: TestClient, store: duckdb.DuckDBPyConnection, preset: Preset
) -> None:
    """Every visible node has a visible parent, level by level and not at the selection alone.

    A preset filters children and never the expanded chain, so a level whose cell would hide
    the step the path goes through renders in full instead — which is what lets a reader stand
    on a kind the preset folds away and still see where it sits. Swept over the nodes of every
    kind the corpus fills best, because a level built under the wrong parent is a shape one
    selection can hide.
    """
    for kind in Kind:
        for at in richest(store, preset, kind, 3):
            html = client.get(node_url(kind, *at), params={"nav": preset}).text
            chain, drawn = values(html, "data-crumb"), rows(html)
            assert drawn[0] == (0, chain[0]), at
            for depth, (crumb_kind, *arguments) in enumerate(sited(at[0], chain)):
                below = [key for at_depth, key in drawn if at_depth == depth + 1]
                expected = cell(store, preset, crumb_kind, *arguments)
                if depth + 1 < len(chain) and chain[depth + 1] not in expected:
                    expected = cell(store, Preset.FULL, crumb_kind, *arguments)
                assert below == expected, f"{at}: under {chain[depth]}"


@pytest.mark.parametrize("preset", list(Preset))
def test_the_nav_tree_offers_every_preset_at_the_node_the_reader_stands_on(
    client: TestClient, store: duckdb.DuckDBPyConnection, preset: Preset
) -> None:
    """A preset is a control above the NavTree, not a query string a reader has to know to type.

    One link per preset, each pointing at the *same* node under a different preset, and the preset
    in force marked. Read on a node of every kind because every kind's page carries the NavTree,
    and read with a knob turned down because a link that dropped `?kin=` would quietly serve a
    wider page than the one the reader is standing on.
    """
    for kind in Kind:
        (picked,) = richest(store, preset, kind, 1)
        at = node_url(kind, *picked)
        html = client.get(at, params={"nav": preset, "kin": 2}).text
        # Every preset is offered, in the order the enum declares them, and the control rides the
        # rows: it sits inside the element a tree click swaps out of band, so the links follow
        # the reader to the node they land on instead of pointing back at the one they left.
        assert inside(html, "id", "nav-tree-rows", "data-nav") == [
            choice.value for choice in Preset
        ]
        for choice in Preset:
            (href,) = inside(html, "data-nav", choice, "href")
            went = urlsplit(href)
            # The same node under a different preset, carrying the knobs the reader arrived with.
            assert went.path == at, (kind, choice)
            carried = {name: value for name, (value,) in parse_qs(went.query).items()}
            wanted = {"kin": "2"} | ({} if choice is Preset.FULL else {"nav": str(choice)})
            assert carried == wanted, (kind, choice)
            # And the preset in force is the marked one, so the control says where the reader is.
            marked = ["true"] if choice is preset else []
            assert inside(html, "data-nav", choice, "aria-current") == marked, (kind, choice)


def test_a_preset_hides_a_kind_without_hiding_the_path_down_to_one(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A node a preset filters out still renders when the reader is standing on it.

    `agents` hides turns and `noapi` hides api calls, and either one selected is a reading
    position rather than a contradiction: the node is on the NavTree, it is the row the pane is
    about, and its whole chain renders above it with nothing missing in between.
    """
    turn = open_turn(store)
    (call_id,) = one(
        store,
        "SELECT id FROM live_api_calls WHERE session_id = ? AND source = ? AND turn_id = ?"
        ' ORDER BY "index" LIMIT 1',
        [SPINE, MAIN, turn],
    )
    hidden = {
        "agents": (url(turn), f"turn:{turn}"),
        "noapi": (f"/session/{SPINE}/thread/{MAIN}/call/{call_id}", f"call:{call_id}"),
    }
    for preset, (at, key) in hidden.items():
        served = client.get(at, params={"nav": preset})
        assert served.status_code == 200, preset
        assert key in values(served.text, "data-nav-tree"), preset
        assert values(served.text, "data-selected") == [key], preset
        assert values(served.text, "data-crumb")[0] == f"session:{SPINE}", preset
        assert values(served.text, "data-crumb")[-1] == key, preset


def test_a_preset_rides_every_node_link_the_page_mints(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?nav=` travels with the reader: every node link on the page carries it.

    The NavTree's rows, the tail a cap left, the crumbs, the pane's children log and the two walk
    controls are all node URLs, and a reader who picked a view keeps it through any of them.
    Both the `href` a reader can paste and the `hx-get` a click follows, because the walk
    controls are buttons and mint only the second. The presets above the NavTree are the one
    exception, and the only one: their whole job is to change the preset, so their three links are
    excluded here and checked on their own leaf. Read with `?kin=1` so the tail row is on the
    page to check too, and with `?log=1` so the children log runs past one page: the corpus's
    widest level is five children, so at the production page size no pager is ever minted.

    The body a log row expands is the same node under the same view, so the preset rides the
    mount as well — and rides out again on the links the fragment itself mints, which are the
    reader's way on from inside a parent's page. Every kind of body is opened, because the two
    fragment routes mint their suffix apart and only a tool's body mints a link of its own.
    """
    html = client.get(url(open_turn(store)), params={"nav": "agents", "kin": 1, "log": 1}).text
    switching = set(inside(html, "class", "presets", "href"))
    assert len(switching) == len(Preset), "the control's own links, which change the preset"
    # `values` reads the markup and `inside` reads it parsed, so an href with two knobs on it
    # arrives `&amp;`-escaped from one and bare from the other.
    links = [
        href
        for attribute in ("href", "hx-get")
        for href in values(html, attribute)
        if node_link(href) and unescape(href) not in switching
    ]
    assert len(links) > 5, "the page mints node links to check"
    assert values(html, "data-walk"), "the walk controls are among them"
    for href in links:
        assert parse_qs(href.partition("?")[2]).get("nav") == ["agents"], href
    opened: set[str] = set()
    led = 0
    for at in mounting(store):
        page = client.get(at, params={"nav": "agents", "kin": 1}).text
        found = mounts(page)
        assert found, f"the log rows on {at} mount an expansion"
        for mount in found:
            # The mount a log row opens its child's body through carries the preset...
            assert parse_qs(mount.partition("?")[2]).get("nav") == ["agents"], mount
            served = client.get(mount)
            assert served.status_code == 200, mount
            # ...and the body it serves links on under that preset rather than dropping it.
            onward = [href for href in values(served.text, "href") if node_link(href)]
            assert onward, f"the fragment offers the way to its own node: {mount}"
            for href in onward:
                assert parse_qs(unescape(href).partition("?")[2]).get("nav") == ["agents"], href
            opened.add(mounted_kind(mount))
            led += len(values(served.text, "data-spawned"))
    # And the rows a tail row fetches are minted by the fragment and not by the page, so the
    # preset rides the fetch out and comes back on every row it answers with.
    spilling = spilled(html)
    assert spilling, "the window left a tail row on the page"
    for fetch in spilling:
        assert parse_qs(fetch.partition("?")[2]).get("nav") == ["agents"], fetch
        served = client.get(fetch)
        assert served.status_code == 200, fetch
        onward = [unescape(href) for href in values(served.text, "href")]
        assert onward, f"the level it left out has rows in it: {fetch}"
        for href in onward:
            assert parse_qs(href.partition("?")[2]).get("nav") == ["agents"], href
    # The three kinds of body one route serves, and the run the other one does.
    assert opened == {Kind.TURN, Kind.CALL, Kind.TOOL, Kind.RUN}, opened
    # One of those tool bodies led with the run it started. That link is the only one the pane
    # macro mints inside a fragment, so without it the suffix the pane is handed goes unread.
    assert led, "a tool body that leads with its run"


def test_a_preset_the_viewer_does_not_have_is_refused(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """`?nav=` names one of the three views or the request is a 400, not a quiet full tree.

    Asked of a fragment as well as a page: the preset rides the mount an expansion opens, so a
    preset the viewer does not have has to be refused there too rather than written into every
    link the fragment serves.
    """
    at = url(open_turn(store))
    for asked in (at, mounts(client.get(at).text)[0]):
        assert client.get(asked, params={"nav": "everything"}).status_code == 400, asked
        for preset in Preset:
            assert client.get(asked, params={"nav": preset}).status_code == 200, preset
