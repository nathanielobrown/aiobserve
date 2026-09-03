"""What a node page and an expansion cost, with every value on them as fat as the caps allow.

The ceiling leaves in `test_bounds.py` say the page fits; these say what it spends to fit. The
page is matched into the rows the arithmetic prices — a crumb, a NavTree row, a log row, a
previewed value — and each is weighed against what `tests/view/budgets.py` measured it at, so
a page that grows a field pays for it here before it reaches a ceiling.
"""

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.extract.pricing import MODELS
from hyphae.view import bounds, nodes
from hyphae.view.app import build_app
from hyphae.view.text.format import ELLIPSIS
from tests.view.budgets import (
    DEAR_PANE_DETAILS,
    DESCRIBED_AT_EVERY_CAP,
    MEASURED_EXPANSION_CHROME,
    MEASURED_NODE_CHROME,
    MEASURED_PAGER_BYTES,
    PANE_DETAILS,
    exact_pins,
    fits,
    worst_crumb_bytes,
    worst_expansion_bytes,
    worst_log_row_bytes,
    worst_rendered_detail_bytes,
    worst_stored_detail_bytes,
)
from tests.view.conftest import (
    Planter,
    Statement,
    fields,
    one,
    pages,
    planter,
    values,
)

# What a node page's arithmetic prices row by row, which chrome is the page without: a crumb of
# the chain down to the selection, a row of the NavTree, a row of the pane's children log, and one
# previewed value. Each is matched rather than differenced, so what the leaf below weighs is the
# row itself and not a difference between two pages that could differ in something else.
PRICED_ROWS = {
    "crumb": r"<a data-crumb=.*?</a>",
    "nav_tree": r'<li class="row.*?</li>',
    "log": r"<tr data-child=.*?</tr>",
    # The control under the log, which is once a page rather than once a row — priced apart
    # from the chrome because it renders only where the level runs past one page, so a page
    # that happens to hold every child of its node would otherwise weigh it at nothing.
    "pager": r'<nav class="pager".*?</nav>',
    # The class carries the wall a quoted value wears as well as the name of the part, so the
    # match reads the whole attribute: a pattern pinned to `detail"` would stop pricing a prose
    # preview the moment one was walled.
    "detail": r'<section class="detail[^"]*".*?</section>',
}


# The sizes that make every link on a node page longest, which is what `worst_knob_bytes`
# prices. Written as a request rather than derived from `knobs`, so the leaf below fails if the
# app stops accepting one of them rather than quietly measuring a page with no knobs at all.
WORST_KNOBS = {
    "nav": max(nodes.Preset, key=len).value,
    "kin": bounds.KIN.ceiling - 1,
    "log": bounds.LOG.ceiling - 1,
    "detail": bounds.DETAIL.ceiling - 1,
}


def priced(html: str) -> tuple[str, dict[str, list[str]]]:
    """A node page split into the rows the arithmetic prices and the chrome it does not."""
    rows: dict[str, list[str]] = {}
    for name, pattern in PRICED_ROWS.items():
        rows[name] = re.findall(pattern, html, flags=re.DOTALL)
        html = re.sub(pattern, "", html, flags=re.DOTALL)
    # The split is the instrument, so it is checked both ways: a row left in is a cost counted
    # twice, and a wrapper taken out hides part of the page this measures.
    assert not values(html, "data-crumb") and not values(html, "data-nav-tree")
    assert not values(html, "data-child") and not values(html, "data-detail")
    assert 'id="nav-tree-rows"' in html and 'id="reading-pane"' in html
    return html, rows


def escaped_at_every_cap() -> tuple[Statement, ...]:
    """Every cap a title, a heading or a preview reads, planted full of `&`.

    `&` is the character that escapes to five bytes, and no recorded node is adversarial: what a
    pass wrote, and the prompt, command, agent type, model, tool name and tool payload a page
    falls back to.
    """
    head = "&" * queries.HEADER_CHARS
    # Longer than the widest cut any query makes, so every cut bites and every preview offers
    # the rest of itself: what this weighs is the page at its caps, not at the corpus's sizes.
    fat = "&" * (queries.DETAIL_CHARS + 1)
    item = "&" * queries.HEADER_ITEM_CHARS
    # And the same width of the pair every lexer here makes two tokens of, for the two previews
    # a row can name the syntax of.
    tokens = "&;" * ((queries.DETAIL_CHARS + 2) // 2)
    over = queries.HEADER_ITEMS + 2
    return (
        (
            "UPDATE sessions SET title = ?, agent_name = ?, project_dir = ?, git_branch = ?,"
            " version = ?, entrypoint = ?",
            [head] * 6,
        ),
        # A skill rides an api call, so the plant clones a live one per session rather than
        # inventing a row: `live_api_calls` is the population the header's list counts.
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            " ? || i AS attribution_skill)"
            " FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c,"
            " range(1, ?) t(i))",
            [item, over + 1],
        ),
        (
            "INSERT INTO pr_links (SELECT s.id, 900000 + i, i, ? || i, 'planted/repo',"
            " '2026-01-01T00:00:00Z' FROM sessions s, range(1, ?) t(i))",
            [item, over + 1],
        ),
        # What a turn's NavTree row, log row and pane read. All three go in past every cut that
        # touches them: the timeline cuts each to a log line's width, and the prompt is the
        # pane's one preview as well as the row's title, which is the wider of the two.
        ("UPDATE turns SET prompt = ?, command_name = ?, command_args = ?", [fat] * 3),
        ("UPDATE agent_runs SET agent_type = ?, model = ?, brief = ?", [fat, fat, fat]),
        ("UPDATE api_calls SET model = ?, text = ?, thinking = ?", [fat, fat, fat]),
        # The input parses, and says all three of the things read out of one: the two a tool row
        # reads — a log row
        # that could not find a description would print the raw input in its place and leave
        # the line under it empty, which is a row two columns short of the widest one there is.
        # Every call failed, too, which is the dearest a tool row gets: the mark the NavTree puts
        # on a failure is markup no other kind of row carries. It does not make a tool the
        # widest row — a turn's row measures 914 B against a tool's 830 — but it is what puts
        # the stepper on every tool page, and that is the dearest the chrome under a pane gets.
        (
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            [fat, json.dumps({"description": fat, "command": fat, "prompt": fat}), fat],
        ),
        # One call a turn answered in a model the window table prices, at tokens a window over
        # the turn before it, so every row that draws a context bar draws one at its widest
        # spelling: three edges of two digits each. Cloned rather than flipped, because the
        # model column above is what makes an api call's the widest row of the children log, and
        # a thread that answered in a real model would print a real model there. Which model is
        # arbitrary — the bar reads the window off the table, and every window in it is spent
        # past here — but the tokens climb with the call's index, because a turn's tip is what
        # it added over the turn before: a thread of turns all left at the same fill draws a
        # full bar with no tip in it. It answers last in its turn — a thread's calls are
        # ordered by index and a turn's fill is its last call's — so the index is planted
        # past every recorded one rather than tied with the call it was cloned from.
        (
            "INSERT INTO api_calls (SELECT * EXCLUDE (rank)"
            " REPLACE (id || '-filled' AS id, ? AS model, false AS synthetic,"
            ' 1000000 + "index" AS "index",'
            " 300000 * rank AS input_tokens, 0 AS output_tokens, 0 AS cache_read_tokens,"
            " 0 AS cache_creation_tokens)"
            " FROM (SELECT DISTINCT ON (l.session_id, l.source, l.turn_id) l.*,"
            ' l."index" + 1 AS rank FROM live_api_calls l))',
            [next(model for model, spec in MODELS.items() if spec.context_window)],
        ),
        # And the third edge, which is read off the session's opening call rather than off the
        # turn: every thread's turns stand on what `main` sent first, so the earliest call of
        # every main thread is filled to the window too. Planted after the clone above, which
        # copies live rows and would otherwise carry this width into a second call.
        (
            'UPDATE api_calls SET input_tokens = 300000 WHERE (session_id, source, "index") IN'
            ' (SELECT session_id, source, min("index") FROM api_calls'
            "  WHERE source = 'main' AND NOT synthetic GROUP BY session_id, source)",
            [],
        ),
        # And the two calls whose panes show a value in its own syntax, planted after the rest
        # so they keep the widths above and take the tool names that reach the lexers. `&;` is
        # the pair the shipped lexers make the most tokens of, which is what a preview budgeted
        # at a span a character has to hold: 26 B a character through the SQL lexer today.
        (
            "UPDATE tool_calls SET name = 'Bash', input = ?"
            " WHERE id = (SELECT min(id) FROM tool_calls)",
            [json.dumps({"description": fat, "command": tokens})],
        ),
        (
            "UPDATE tool_calls SET name = 'Read', input = ?, result = ?"
            " WHERE id = (SELECT max(id) FROM tool_calls)",
            # The path is planted past the cut like every other input here, and its suffix is
            # what the page reads the result's syntax off — a name, not a length.
            [json.dumps({"file_path": f"/{fat}/planted.sql"}), tokens],
        ),
        # And one turn asked in the dearest markdown there is: a fenced block, the one
        # construct markdown hands to a lexer. The pane cuts the head inside the fence, which
        # commonmark closes at the end of what it was given — so what it renders is `&;` at an
        # element a token, which is what a preview budgeted at `MARKED_CHAR_BYTES` has to hold.
        (
            "UPDATE turns SET prompt = ? WHERE id = (SELECT min(id) FROM turns)",
            [f"```sql\n{tokens}"],
        ),
        *DESCRIBED_AT_EVERY_CAP,
    )


@pytest.fixture(scope="module")
def escaped_client(
    enriched_db: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    """The viewer over a described store planted full of `&` at every cap it reads.

    Module-scoped and built off `tmp_path_factory`, because the three sweeps below are three
    leaves over one plant: `enriched_plant` is function-scoped over `tmp_path` and would rebuild
    it per leaf. Nothing here writes to the store, so the three share it safely.
    """
    path = planter(enriched_db, tmp_path_factory.mktemp("escaped"))(*escaped_at_every_cap())
    with TestClient(build_app(path)) as planted:
        yield planted


# One node page, split into the rows the arithmetic prices and the chrome it does not.
Split = tuple[str, dict[str, list[str]]]

# The second page of each level, one child to a page: no recorded node has children enough to
# page at a size a reader would type, and the control under the log is what a level running past
# its page costs.
PAGED_KNOBS = {**WORST_KNOBS, "log": 1, "page": 2}


def swept(
    planted: TestClient, urls: list[str], marks: Mapping[str, str | int], *, paged: bool
) -> list[Split]:
    """Every node page the store serves at these knobs, priced row by row.

    The list and the two pages that are not nodes come back too; only a node page splits. Pass
    `paged` for the sweep that asks for a second page, where a level of fewer than three has
    neither a second page nor a middle one and answers 404 by design — every other sweep holds
    every URL to 200.
    """
    served = []
    for url in urls:
        response = planted.get(url, params=marks)
        if paged and response.status_code != 200:
            continue
        assert response.status_code == 200, (url, response.text[:200])
        served.append(response.text)
    return [priced(page) for page in served if 'id="nav-tree-rows"' in page]


def found_rows(split: list[Split], name: str) -> list[str]:
    """Every priced row of one kind the sweep rendered, over all of its pages."""
    return [row for _, rows in split for row in rows[name]]


def weighed(split: list[Split], *, widest_of: frozenset[str]) -> None:
    """A crumb, a NavTree row and a log row of this sweep each weigh what the arithmetic budgets.

    `widest_of` names the kinds this sweep renders the corpus's widest row of, which is a fact
    about the fixtures recorded by weighing the three sweeps apart rather than reasoned from the
    knobs. There a budget is held from below as well; everywhere else it is only a ceiling, and a
    template that grows a row still reds in the one sweep that prices that kind exactly.
    """
    for name, budget, pinned in (
        ("crumb", worst_crumb_bytes(), exact_pins()),
        ("nav_tree", bounds.NAV_TREE_ROW_BYTES, True),
        ("log", worst_log_row_bytes(), False),
    ):
        found = found_rows(split, name)
        assert found, name
        widest_row = max(len(row.encode()) for row in found)
        # A log row is arithmetic over a cap with a rounding fudge inside it, so a row that comes
        # in under is a cap with room left and the budget is only ever a ceiling. The other two
        # are measurements of the row itself, or arithmetic with nothing rounded in it: the
        # NavTree's is held from below always — the NavTree is most of the page, so a byte of
        # slack there is 3,217 bytes the ceiling keeps for nothing, and `NODE_BYTES` now has room
        # to hide one — and the crumb's under the exact-pin mode, which is what keeps a
        # hand-written pin from outliving the measurement it stood for.
        exact = pinned and name in widest_of
        assert widest_row == budget if exact else widest_row <= budget, (name, widest_row)
        if name == "nav_tree":
            # And the row it priced drew a context bar at its widest spelling: three edges of
            # two digits each, which is the most a turn's row carries. A corpus that answered
            # in models the window table holds none of would price a row that draws no bar, and
            # every barred row would be twelve bytes over it.
            widest = max(found, key=lambda row: len(row.encode()))
            top = nodes.BAR_STEPS
            assert re.search(rf'class="[^"]* f{top} p{top} b{top}"', widest), widest[:200]
            # And it drew both halves of its cost badge, which is the widest thing the row has
            # grown: a corpus whose dearest row spawned no agent run would measure under this.
            assert widest.count('class="badge ') == 2, widest[:200]


def marked_up(split: list[Split]) -> tuple[list[str], list[str], list[str]]:
    """The sweep's previews split by whether the page marked one up, each inside its own budget.

    A preview is priced by whether the page marked it up, which is the whole of the difference
    between the two budgets: an element a token against an escape a character. Marked up two
    ways — the syntax a record named, and the markdown a session wrote — and both are read off
    the markup rather than off the route, because what the ceiling pays for is what came back.
    """
    previews = found_rows(split, "detail")
    dear = [row for row in previews if 'class="code ' in row or 'class="prose"' in row]
    assert dear
    assert max(len(row.encode()) for row in dear) <= worst_rendered_detail_bytes()
    # And the plant reached a lexer, so that budget is being held rather than merely not
    # approached: the dearest preview costs more than escaping every character of it would,
    # which is the whole of the difference between the two.
    assert max(len(row.encode()) for row in dear) > worst_stored_detail_bytes()
    return previews, dear, [row for row in previews if row not in dear]


def reached_the_caps(split: list[Split]) -> None:
    """The plant reached every cap this sweep's pages read, which is what makes them a worst case.

    Each header string cut to its head, each list cut to its first members and saying how many it
    left, every tree title cut to a nav width, and every preview offering the rest of itself.
    """
    session = next(chrome for chrome, _ in split if 'data-body="session"' in chrome)
    facts = fields(session, "data-body", "session")
    assert len(facts["git_branch"]) == len(facts["version"]) == queries.HEADER_CHARS
    escaped = {
        found.count("&amp;")
        for row in found_rows(split, "nav_tree")
        for found in re.findall(r'<span data-field="title">(.*?)</span>', row, flags=re.DOTALL)
    }
    # No title got past the cut, and one reached it. Not every row's title is planted — a
    # bucket is named by the viewer and a compaction by its trigger — so the widest is what
    # says the cut bit rather than every row being the same width. Every sweep reaches the cut,
    # so every one of them holds it from below.
    assert max(escaped) == queries.NAV_CHARS
    assert {row.count("more character(s)") for row in found_rows(split, "detail")} == {1}
    # And the mark a failed call carries reached the rows the NavTree priced, so
    # `NAV_TREE_ROW_BYTES` is a price for the dearest tool row rather than for one that happened
    # to succeed.
    assert any('data-field="is_error"' in row for row in found_rows(split, "nav_tree"))
    # The enrichment sits in the chrome, stale tag and all, so it is planted with the rest.
    described = fields(session, "data-enrichment", values(session, "data-enrichment")[0])
    marked = "&" * queries.ENRICHMENT_CHARS + ELLIPSIS
    assert described["description"] == described["friction"] == marked
    assert described["stale"] == "stale"


# The node page is the one page `worst_node_bytes` multiplies four ways — a crumb per level open,
# a NavTree row per child of each, a log row per child of the selection, and the values the pane
# previews — so a template that grows any of them puts the ceiling out by whatever size it is
# multiplied by. Three sweeps of every node of every session weigh them, one to a leaf: a page is
# not what any of them measures, because the widest chrome belongs to whichever pane is dearest
# and that is a question about the corpus. Which sweep reaches which budget is recorded, not
# assumed — run the three under `HYPHAE_PIN_EXACT=1` after changing a knob or the corpus, because
# a pin moving to another sweep shows up as one leaf red and another passing loosely.


def test_a_node_page_at_the_sizes_a_reader_gets_costs_what_the_ceiling_budgets(
    escaped_client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every part of a node page at the default sizes weighs no more than the arithmetic gives it.

    The defaults are where the NavTree holds a row of every kind there is, and where the chrome —
    the page apart from the rows the arithmetic prices — is widest of the three sweeps.
    """
    split = swept(escaped_client, pages(store), {}, paged=False)
    weighed(split, widest_of=frozenset())
    # No level of the corpus runs past its page at a size a reader would type, so nothing here
    # draws the control under the log; the paged sweep below is the one that prices it.
    assert not found_rows(split, "pager")
    previews, dear, stored = marked_up(split)
    # The plant reached the store's own route as well as the lexer's, so the cheaper budget is
    # weighed against a preview that took it rather than against none.
    assert len(dear) < len(previews)
    assert max(len(row.encode()) for row in stored) <= worst_stored_detail_bytes()
    # And no pane shows more previews than the arithmetic gives it, or more marked-up ones: a
    # kind that grew a third value would otherwise spend the ceiling unpriced.
    assert max(len(rows["detail"]) for _, rows in split) == PANE_DETAILS
    assert max(sum(row in dear for row in rows["detail"]) for _, rows in split) == (
        DEAR_PANE_DETAILS
    )
    # ...and what the page carries whatever it holds fits the allowance the ceiling gives it,
    # which this sweep is the one to spend in full.
    widest = max((chrome for chrome, _ in split), key=lambda page: len(page.encode()))
    assert fits(measured=len(widest.encode()), budget=MEASURED_NODE_CHROME), len(widest.encode())
    reached_the_caps(split)


def test_a_node_page_at_the_widest_knobs_costs_what_the_ceiling_budgets(
    escaped_client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every part of a node page at the sizes that lengthen every link on it fits its budget.

    A reader who narrows a page pays for the query string on every row of it, so this is the
    sweep that holds the widest crumb and the widest NavTree row.
    """
    split = swept(escaped_client, pages(store), WORST_KNOBS, paged=False)
    weighed(split, widest_of=frozenset({"crumb", "nav_tree"}))
    # These knobs ask for a page of every level, so no level here runs past one either.
    assert not found_rows(split, "pager")
    previews, dear, stored = marked_up(split)
    assert len(dear) < len(previews)
    assert max(len(row.encode()) for row in stored) <= worst_stored_detail_bytes()
    assert max(len(rows["detail"]) for _, rows in split) == PANE_DETAILS
    assert max(sum(row in dear for row in rows["detail"]) for _, rows in split) == (
        DEAR_PANE_DETAILS
    )
    widest = max((chrome for chrome, _ in split), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_NODE_CHROME, len(widest.encode())
    reached_the_caps(split)


def test_a_second_page_of_a_level_costs_what_the_ceiling_budgets(
    escaped_client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A node page showing a level's second page weighs the control the first two sweeps never draw.

    One child to a page, because no recorded node has children enough to page at a size a reader
    would type — and the control under the log is what a level running past its page costs.
    """
    split = swept(escaped_client, pages(store), PAGED_KNOBS, paged=True)
    weighed(split, widest_of=frozenset())
    # The pager is this sweep's alone: it is arithmetic with nothing rounded in it, so the
    # exact-pin mode holds it from below as well, and a sweep that drew none would crash on the
    # `max()` below rather than pass.
    pagers = found_rows(split, "pager")
    assert pagers
    widest_pager = max(len(row.encode()) for row in pagers)
    if exact_pins():
        assert widest_pager == MEASURED_PAGER_BYTES, widest_pager
    else:
        assert widest_pager <= MEASURED_PAGER_BYTES, widest_pager
    _, dear, stored = marked_up(split)
    # Every preview a second page shows is one a model or a person wrote, so this sweep has no
    # escaped-only preview to weigh: the cheaper budget is held in the two sweeps that render one.
    assert not stored
    assert max(len(rows["detail"]) for _, rows in split) <= PANE_DETAILS
    assert max(sum(row in dear for row in rows["detail"]) for _, rows in split) <= (
        DEAR_PANE_DETAILS
    )
    widest = max((chrome for chrome, _ in split), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_NODE_CHROME, len(widest.encode())
    reached_the_caps(split)


def test_an_expansion_weighs_a_body_and_the_one_page_of_rows_it_lists(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """An expansion is bounded by the same cap its node's own page is, and by nothing else.

    An api call's expansion lists the tools it called, so a call that called two hundred is
    where the bound has to hold: the fragment reads one page of the level at the reader's
    `?log=`, and the way past that page is the link to the call's own page rather than more
    rows. Planted, because the densest call the corpus recorded made four tool calls — and
    planted at every cap, with `&` in each string a row prints, so what this weighs is the
    fragment at its ceiling rather than at the fixture's sizes.

    The body above those rows is weighed over all three kinds a log can open, not just the
    call's: a turn's is the dearest of them, because a turn's body is the one that carries
    what an enrichment pass wrote. So the described store, planted at the enrichment's caps
    as well.
    """
    fat = "&" * (queries.LOG_CHARS + 1)
    # The body's own strings are cut at the width a title is, not at the reader's `?detail=`.
    head = "&" * (queries.HEADER_CHARS + 1)
    session_id, source, api_call_id, recorded = one(
        store,
        "SELECT session_id, source, api_call_id, count(*) FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3 LIMIT 1",
    )
    turn_id, tool_id = one(
        store,
        "SELECT c.turn_id, t.id FROM live_api_calls c JOIN live_tool_calls t"
        "  ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id"
        " WHERE c.session_id = ? AND c.source = ? AND c.id = ?",
        [session_id, source, api_call_id],
    )
    clones = bounds.LOG.ceiling * 2
    path = enriched_plant(
        # One recorded tool call, cloned past the cap: the clone keeps every column the row
        # reads except the two that have to differ, so the rows are the store's own shape.
        (
            "INSERT INTO tool_calls (SELECT t.* REPLACE (t.id || '-planted-' || i AS id,"
            ' 90000 + i AS "index") FROM (SELECT * FROM tool_calls WHERE session_id = ?'
            " AND source = ? AND api_call_id = ? LIMIT 1) t, range(1, ?) g(i))",
            [session_id, source, api_call_id, clones + 1],
        ),
        # Then every string a tool row prints, planted past its cut: the name, the title the
        # input is read for, the command under it, and the failure that marks the row.
        (
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            [fat, json.dumps({"description": fat, "command": fat}), fat],
        ),
        # And the call's own facts, which are the body above those rows: every string the
        # header cuts, planted past its cut, so the chrome is weighed at the width the body
        # reads rather than at the fixture's.
        # And the facts the bodies themselves print, planted past the cut each is read at: a
        # call's model and what it fell back from, a turn's ask and the command it was typed
        # as. A body reads them at `HEADER_CHARS`, not at the reader's `?detail=`.
        # What it said and what it thought go in too: a body previews neither, but the head of
        # what a call said is what its title falls back to.
        (
            "UPDATE api_calls SET model = ?, fallback_from = ?, text = ?, thinking = ?",
            [head] * 4,
        ),
        ("UPDATE turns SET prompt = ?, command_name = ?", [head, head]),
        *DESCRIBED_AT_EVERY_CAP,
    )
    at = f"/session/{session_id}/thread/{source}"
    mount = f"{nodes.BODY_URL}{at}/call/{api_call_id}"
    knobs = {**WORST_KNOBS, "log": bounds.LOG.ceiling}
    with TestClient(build_app(path)) as planted:
        served = planted.get(mount, params=knobs)
        # Every other kind a log opens a body for, for the widest chrome of the three.
        others = [
            planted.get(f"{nodes.BODY_URL}{at}/{kind}/{node_id}", params=knobs)
            for kind, node_id in (("turn", turn_id), ("tool", tool_id))
        ]
    assert served.status_code == 200, mount
    rows = re.findall(PRICED_ROWS["log"], served.text, flags=re.DOTALL)
    # The cap bit: the level holds twice what came back, and what came back is one page of it.
    assert len(rows) == bounds.LOG.ceiling
    assert fields(served.text, "data-log", "tools")["children"] == str(recorded + clones)
    # The fragment weighs its rows and a body, and neither part is over what it is budgeted...
    assert len(served.content) <= worst_expansion_bytes()
    assert max(len(row.encode()) for row in rows) <= worst_log_row_bytes()
    bodies = [re.sub(PRICED_ROWS["log"], "", served.text, flags=re.DOTALL)]
    for other in others:
        assert other.status_code == 200
        assert not re.findall(PRICED_ROWS["log"], other.text, flags=re.DOTALL), "it listed a level"
        bodies.append(other.text)
    assert fits(
        measured=max(len(body.encode()) for body in bodies), budget=MEASURED_EXPANSION_CHROME
    ), [len(body.encode()) for body in bodies]
    # A turn's body is the one whose title a pass can have written, so the described store is
    # what makes that title the widest it gets rather than the prompt's own head.
    assert fields(bodies[-2], "data-body", "turn")["title"].startswith("&" * queries.TAG_CHARS)
    # ...and an expansion opens no expansion: not one of those rows carries a button that
    # would fetch another body under it.
    assert "data-view" not in served.text
