"""What one row of the NavTree shows, and how the page lays the rows out.

A row says three things at once: how deep it stands, what node it is, and what to fetch when a
reader clicks it. These leaves read all three back off the rendered page — the indent, the
links, and the totals a bucket row carries for the rows it gathers, which are its own because a
bucket is not a row of the store.

What column each kind is named from is `test_nav_tree__names.py`.
"""

import re

import duckdb
from fastapi.testclient import TestClient

from hyphae.model import MAIN_SOURCE
from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.nodes import Kind, meter
from tests.conftest import COMPACTED, COMPACTED_RUN, MAIN, SPINE, SPINE_RUN
from tests.view.conftest import (
    Planter,
    badges,
    fields,
    inside,
    marked,
    one,
    reads,
    rows,
    values,
    wired,
)
from tests.view.nav_trees import (
    STANDING,
    THREAD,
    edges,
    node_link,
    open_turn,
    url,
    weighed,
)


def test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The whole of what a click does, on both the mounts that mount a node link.

    A NavTree row, a children-log row and the two walk controls are how a reader moves without
    leaving the page, and all of them do the same thing: fetch the node's URL, take `#reading-pane`
    out of the response, put it where the pane already is, and swap the rows out of band.
    Read as htmx composes it, inheritance and all, because that is what the browser acts on.

    `hx-target` is the half that has no default worth having: htmx aims at the clicked
    element, so a page missing it swaps the whole pane inside the `<a>` the reader clicked
    and leaves the pane itself showing the node they came from. `hx-swap` is `outerHTML`
    because `hx-select` hands back the `#reading-pane` element itself, not its contents.
    """
    html = client.get(url(open_turn(store))).text
    swap = {
        "hx-target": "#reading-pane",
        "hx-swap": "outerHTML",
        "hx-select": "#reading-pane",
        "hx-select-oob": "#nav-tree-rows",
        "hx-push-url": "true",
    }
    for mount in ("data-nav-tree", "data-child", "data-walk"):
        # A row's other fetch is its body toggle, which opens in place and has nowhere to go:
        # the ones that move the reader are the ones fetching a node's own URL.
        moving = [(key, w) for key, w in wired(html, mount) if node_link(w["hx-get"])]
        assert len(moving) > 1, mount
        for key, wiring in moving:
            # A link fetches what it points at: one URL, however the reader gets there. A walk
            # control has no `href` to agree with — it is a button, because what it offers is
            # a move through the pane and not a place of its own to paste.
            assert wiring.get("href", wiring["hx-get"]) == wiring["hx-get"], (mount, key)
            assert {name: wiring.get(name) for name in swap} == swap, (mount, key)
    # The two ids the swap aims at, each written exactly once.
    assert html.count('id="reading-pane"') == 1
    assert html.count('id="nav-tree-rows"') == 1


def test_every_level_a_nav_tree_opens_is_indented_one_step_further_than_the_one_above(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A row sits one step further in than its parent, however deep the session nests.

    A subagent's own turns render four levels down and its api calls deeper still, so a
    stylesheet with a rung for the first three levels laid them flush against the session and
    the hierarchy vanished exactly where a reader most needs it. CSS cannot read `data-depth`
    as a number portably and `app.CSP` forbids the inline style that would carry one, so every
    level a chain can open is written out — and this is what keeps that ladder as long as
    `bounds.DEPTH` says a chain can be.
    """
    # A turn of a subagent's own thread opens the session, the turn that spawned the run, the
    # run, the turn itself and its api calls — five levels, past the three the ladder had...
    turn_id, source = one(
        store,
        'SELECT id, source FROM live_turns WHERE session_id = ? AND source <> ? ORDER BY "index"'
        " LIMIT 1",
        [SPINE, MAIN],
    )
    page = client.get(f"/session/{SPINE}/thread/{source}/turn/{turn_id}").text
    rendered = {depth for depth, _ in rows(page)}
    assert max(rendered) > 3, "the recorded subagent no longer nests past three levels"
    # ...and the stylesheet indents each of them by its own depth, in one step a level.
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    ladder = {
        int(depth): int(steps)
        for depth, steps in re.findall(
            r'li\.row\[data-depth="(\d+)"\][^{]*\{[^}]*calc\((\d+) \* var\(--nav-tree-step\)\)',
            style,
        )
    }
    # Every level a chain can open has a rung, and no rung stands for a level nothing reaches.
    assert ladder == {depth: depth for depth in range(1, bounds.DEPTH + 1)}
    assert rendered <= set(ladder) | {0}


def test_the_open_path_clamps_at_the_top_while_the_rows_under_it_scroll(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The steps down to the selection stay on screen, stacked under the preset control.

    A working session's NavTree is longer than the column holding it, and the rows a reader
    scrolls past are the ones saying where they are — the session, the turn that spawned the
    run, the run. So the open path clamps: each ancestor stands where its own depth puts it,
    one row below the step above it and the first of them below the presets, and only the
    siblings and children scroll past them.

    Pure CSS, so the markup's whole part is one class on the rows of the open path — which is
    what this reads — and an offset per depth written out beside the indent ladder above,
    because `app.CSP` forbids the inline style a computed one would ride on.
    """
    # The same deep selection the ladder above opens: a subagent's own turn, whose path runs
    # session → turn → run → the turn itself.
    turn_id, source = one(
        store,
        'SELECT id, source FROM live_turns WHERE session_id = ? AND source <> ? ORDER BY "index"'
        " LIMIT 1",
        [SPINE, MAIN],
    )
    page = client.get(f"/session/{SPINE}/thread/{source}/turn/{turn_id}").text
    # The chain the crumbs print is the open path, and everything but its last step is an
    # ancestor — the selection is what the reader is already reading.
    chain = values(page, "data-crumb")
    assert len(chain) > 2, "the recorded subagent no longer opens a path worth clamping"
    clamped = set(chain[:-1])
    # Every step of the path wears the class, the selection does not, and no other row does:
    # a NavTree that clamped a sibling would stack rows the reader never opened.
    assert {key for _, key in rows(page) if marked(page, key, "ancestor")} == clamped
    # And each depth clamps one row further down than the one above it, under the control the
    # presets are pinned in. Written out per level, as long as a chain can be.
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    stack = {
        int(depth): int(rung)
        for depth, rung in re.findall(
            r'li\.row\.ancestor\[data-depth="(\d+)"\][^{]*\{[^}]*'
            r"calc\(var\(--nav-tree-head\) \+ (\d+) \* var\(--nav-tree-row\)\)",
            style,
        )
    }
    assert stack == {depth: depth for depth in range(bounds.DEPTH + 1)}
    assert re.search(r"li\.row\.ancestor\s*\{[^}]*position: sticky", style)


def test_a_row_reads_from_the_left_and_only_its_cost_sits_at_the_right(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The parts of a row are pushed together at the left, and the spare width goes to the title.

    A row is a flex line of four parts — the kind mark, the enrichment glyph, the title and the
    cost — and free space in a flex line goes wherever the line says to put it. Spread between
    the parts, a short title floats out in the middle of the column with the glyph adrift ahead
    of it, and a column of them reads as centred text; the indent that says how deep a row sits
    then measures from a mark nothing follows. So the free width belongs to the title: it is
    the one part that can use it, and giving it there is what keeps every other part where the
    reader's eye already is.

    Read off the stylesheet because that is where it is decided — the served markup is the same
    either way, and nothing else in the tier can see a laid-out box.
    """
    page = client.get(url(open_turn(store))).text
    # The row is the flex line the rule below is about, with its parts in reading order.
    parts = r'class="icon".*data-field="title".*class="secondary"'
    assert [
        row
        for row in re.findall(r'<li class="row node.*?</li>', page, flags=re.DOTALL)
        if re.search(parts, row, flags=re.DOTALL)
    ]
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    row_rules = re.findall(r"li\.node > a \{([^}]*)\}", style)
    assert any("display: flex" in rule for rule in row_rules)
    # Nothing distributes the spare width between the parts — that is the centring itself.
    assert not [rule for rule in row_rules if "justify-content" in rule]
    # The title takes it instead, so the cost is what ends up against the right edge.
    (title,) = re.findall(r'li\.node \[data-field="title"\] \{([^}]*)\}', style)
    assert "flex: 1" in title


def test_the_nav_tree_keeps_its_place_because_the_scroller_is_not_what_swaps(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """What holds a reader's place in a long tree when a click replaces its rows.

    Nothing in the markup says "keep the scroll offset" — the NavTree keeps it because the
    element carrying the scrollbar is `#nav-tree`, and the swap replaces `#nav-tree-rows` inside it.
    An untouched scroller keeps its `scrollTop`, which is why the design could drop
    `hx-preserve`. Move `overflow` down onto the rows and every click sends the reader back to
    the top of the session, and no assertion on served HTML would notice.

    So the structure is what gets pinned: the rows the swap replaces are nested inside the
    element the stylesheet scrolls, and nothing scrolls below it.
    """
    page = client.get(url(open_turn(store))).text
    # The element the swap replaces sits inside the one the NavTree is scrolled by.
    assert "nav-tree-rows" in inside(page, "id", "nav-tree", "id")
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    scrolls = {
        selector.strip()
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style)
        if "overflow:" in body
    }
    # One of them scrolls, and it is the one the swap leaves alone. The two selectors that
    # could take the scrollbar off it are the rows themselves, under either name.
    assert "#nav-tree" in scrolls
    assert not [rule for rule in scrolls if "#nav-tree-rows" in rule or "#nav-tree .rows" in rule]


def test_the_nav_tree_is_widened_by_a_handle_and_the_width_outlives_the_page(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A handle beside the NavTree drags it wider, and the browser remembers how wide.

    Every other thing a reader sets rides the URL. A width cannot: it belongs to the screen
    they are reading on and not to the node they linked to, so a pasted link would carry
    someone else's column. What this pins is the chain that lets a script set it instead —
    a handle in the markup, a grid whose NavTree column is one custom property, and a script
    served from this app, because `app.CSP` forbids an inline one and a page load would
    forget a width that CSS alone had kept.
    """
    page = client.get(url(open_turn(store))).text
    # The handle sits between the two columns it divides, and says what it is to a reader who
    # cannot see it.
    assert [
        at for at in values(page, "id") if at in {"nav-tree", "nav-tree-grip", "reading-pane"}
    ] == [
        "nav-tree",
        "nav-tree-grip",
        "reading-pane",
    ]
    grip = re.findall(r"<div id=\"nav-tree-grip\"[^>]*>", page)
    assert len(grip) == 1 and 'role="separator"' in grip[0] and 'tabindex="0"' in grip[0]
    # The NavTree's column is one custom property, which is the whole of what the script writes:
    # a width the stylesheet fixed some other way is a handle that drags nothing.
    style = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.DOTALL)
    (columns,) = re.findall(r"#browser\s*\{[^}]*grid-template-columns:([^;]*);", style)
    assert "var(--nav-tree-width" in columns
    # And the script that writes it is a file this app serves, keeping the width where a page
    # load cannot reach it.
    (src,) = [asset for asset in values(page, "src") if "tree-width" in asset]
    served = client.get(src)
    assert served.status_code == 200
    assert "--nav-tree-width" in served.text and "localStorage" in served.text
    # And where the width starts when this browser remembers none: the column the stylesheet
    # lays out, read off the grid's own first track. Not the NavTree's laid-out box — under the
    # narrow layout below, `#browser` is a block and the NavTree is the whole page, so a width
    # seeded from it survives into the wide layout as a column twice the one above. Witnessed
    # in Chromium on 2026-08-25: loaded at 800 px and widened to 1600, the NavTree held 768 px
    # against the stylesheet's 384 and left the pane narrower than the NavTree.
    # And it reads the *first* track of that grid, which is the NavTree's: `parseFloat` takes the
    # leading number of `"384px 8px 1fr"` and stops there. A read that walked to another track
    # would seed the gap or the pane — and where the walk misses, `apply()` clamps the `NaN` it
    # yields to `NaN` and the column comes out broken. Pinned as one expression, which is as
    # far as a server-side test can follow a script this app only serves.
    assert re.search(r"parseFloat\(getComputedStyle\(\w+\)\.gridTemplateColumns\)", served.text)
    assert "getBoundingClientRect" not in served.text


def test_a_row_pairs_its_depth_with_the_key_in_the_same_tag() -> None:
    """`rows()` reads the pair whatever the tag's layout, and never reaches across a tag.

    Every leaf here reads the NavTree through that pair, and the tag boundary is the whole of what
    it rests on: a tail row carries a depth and no key — the leaf above builds one — so a pair
    that could span `>` would hand it the next row's key and every level would read one long.
    How a tag is laid out belongs to the component that writes it, which today names these
    attributes in this order and puts nothing between two of them; the first case is invented
    for exactly that reason, standing for a layout a row is free to grow into.
    """
    apart = '<li class="row node" data-depth="2" data-selected="turn:a" data-nav-tree="turn:a">'
    assert rows(apart) == [(2, "turn:a")]
    # A tail row's depth, and the next tag's key: two tags, so nothing to pair. On one line,
    # so the `>` is the only thing that can separate them — a newline between the tags would
    # part them on its own, whatever the pattern says about tag boundaries.
    tail = '<li class="row more" data-depth="1" data-more="session:s"><a data-nav-tree="turn:b">'
    assert rows(tail) == []


def test_a_run_row_says_how_often_its_own_thread_compacted(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run that ran its window out says so on its row; one that never did says nothing.

    The badge is the only thing on a run's row that comes off another table entirely — every
    other value a row carries is the run's own column — so it is read back against
    `live_compactions` in the test's own SQL rather than against what `view_runs` computed.

    One run in the corpus is in this shape and one is the whole of the reading: `compaction/`'s
    `general-purpose` run is the only recorded `compact_boundary` outside a `main` thread
    (`tests/fixtures/compaction/README.md`). The absent half is what makes it a badge and not a
    field — a row with nothing to say draws no pill at all — and `spine/`'s run is read for it.
    """
    compacted = f"{Kind.RUN}:{COMPACTED_RUN}"
    (count,) = one(
        store,
        "SELECT count(*) FROM live_compactions WHERE session_id = ? AND source = ?",
        [COMPACTED, COMPACTED_RUN],
    )
    assert count, "the fixture run's own thread compacted"
    page = client.get(f"/session/{COMPACTED}/run/{COMPACTED_RUN}").text
    assert compacted in values(page, "data-nav-tree")
    # The count alone in the labelled span, the way every other number on a row is carried:
    # the word beside it is prose the markup around the value owns.
    assert fields(page, "data-nav-tree", compacted)["compactions"] == str(count)
    assert f"{count} compaction" in reads(page, "data-nav-tree", compacted)
    # And the run whose thread never compacted carries no such field — not a zero, which
    # would draw a pill saying nothing happened.
    spine = f"{Kind.RUN}:{SPINE_RUN}"
    (none,) = one(
        store,
        "SELECT count(*) FROM live_compactions WHERE session_id = ? AND source = ?",
        [SPINE, SPINE_RUN],
    )
    assert none == 0
    quiet = client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    assert spine in values(quiet, "data-nav-tree")
    assert "compactions" not in fields(quiet, "data-nav-tree", spine)


def test_a_bucket_row_carries_the_totals_of_what_it_gathers(
    client: TestClient, store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Neither bucket is a row of the store: its numbers are sums over what it holds.

    The rest of the NavTree hands a row the store's own numbers, so a bucket is the one place the
    viewer adds up. What it adds up is read back here — the spend, the bar that spend takes
    against the session, and the mark saying some of the calls under it went unpriced — for
    every bucket the corpus records, on the page the bucket hangs on.
    """
    # The session's threads whose calls answer no turn of them, and the runs nothing placed.
    standing = store.execute(
        "SELECT DISTINCT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL ORDER BY 1, 2"
    ).fetchall()
    gathered: tuple[str, list[str]] | None = None
    for session_id, source in standing:
        at = (
            f"/session/{session_id}"
            if source == MAIN_SOURCE
            else f"/session/{session_id}/run/{source}"
        )
        cost, unpriced = one(store, STANDING, [str(session_id), str(source)])
        weighed(client.get(at).text, f"unattributed:{source}", store, session_id, cost, unpriced)
    for (session_id,) in store.execute("SELECT id FROM sessions ORDER BY 1").fetchall():
        loose = [edge.run_id for edge in edges(store, str(session_id)) if edge.spawn_source is None]
        if not loose:
            continue
        # The bucket's own row is every loose run's thread at once, which is the sum the
        # session's page shows against a row that has no children of the store's to point at.
        totals = [one(store, THREAD, [str(session_id), run_id]) for run_id in loose]
        cost, unpriced = sum(row[0] for row in totals), sum(row[1] for row in totals)
        page = client.get(f"/session/{session_id}").text
        weighed(page, f"unattached:{session_id}", store, str(session_id), cost, unpriced)
        # Opening it hands its children the same basis: a run under the bucket draws its share
        # of the session, like every other run, and not a share of the bucket that gathered it.
        (whole,) = one(
            store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [str(session_id)]
        )
        opened = client.get(f"/session/{session_id}/unattached").text
        for run_id, (spent, _) in zip(loose, totals, strict=True):
            drawn = badges(opened, f"run:{run_id}")["cost_usd"]
            assert meter(spent / whole if whole else None) in drawn.step.split(), run_id
        gathered = gathered or (str(session_id), loose)
    # Both buckets are read above rather than one of them: they are built by different code
    # over different rows, and only one of them can span threads.
    assert standing and gathered is not None
    # No recorded bucket holds a call our price table could not price, so the mark that says
    # one does is planted: a thread under each bucket loses its costs, and the bucket has to
    # both count what went unpriced and total what is left. The expectations read the planted
    # store through the same sums, so the plant moves the page and the oracle together.
    thread, source = str(standing[0][0]), str(standing[0][1])
    loose_at, loose_runs = gathered
    path = plant(
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ? AND source = ?",
            [thread, source],
        ),
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ? AND source = ?",
            [loose_at, loose_runs[0]],
        ),
    )
    planted = duckdb.connect(str(path), read_only=True)
    with TestClient(build_app(path)) as marked:
        cost, unpriced = one(planted, STANDING, [thread, source])
        assert unpriced, "the plant leaves the unattributed bucket calls to mark"
        at = f"/session/{thread}" if source == MAIN_SOURCE else f"/session/{thread}/run/{source}"
        weighed(marked.get(at).text, f"unattributed:{source}", planted, thread, cost, unpriced)
        totals = [one(planted, THREAD, [loose_at, run_id]) for run_id in loose_runs]
        cost, unpriced = sum(row[0] for row in totals), sum(row[1] for row in totals)
        assert unpriced, "and leaves the unattached bucket calls to mark"
        page = marked.get(f"/session/{loose_at}").text
        weighed(page, f"unattached:{loose_at}", planted, loose_at, cost, unpriced)
        # A child of the bucket says the same thing for itself: the run whose calls the plant
        # left unpriced carries its own count, and the runs beside it carry no mark at all.
        opened = marked.get(f"/session/{loose_at}/unattached").text
        for run_id, (_, missing) in zip(loose_runs, totals, strict=True):
            marks = inside(opened, "data-nav-tree", f"run:{run_id}", "title")
            assert bool(marks) == bool(missing), run_id
            assert not missing or str(missing) in marks[0], run_id
    planted.close()
