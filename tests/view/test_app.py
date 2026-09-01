"""The app around the node browser: what every response owes a reader, whatever it serves.

A page cites the query behind it, names each id in its URL by the word in front of it, asks for
no asset the viewer does not ship, and is painted by a stylesheet that knows only fields a page
carries. The store it reads is opened read-only. Every expectation is derived from the store
the app is serving rather than written down, so a fixture added to the corpus does not silently
stop being covered.

The session list is `test_app__list.py` and its filter form `test_app__filters.py`; the header
above a node is `test_app__headers.py`, and what a page does with untrusted text is
`test_app__safety.py`. The node pages themselves live in `test_node.py` and the NavTree beside
them in `test_nav_tree.py`, each with its neighbours.
"""

import html
import json
import re
from collections import defaultdict
from pathlib import Path

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.view import nodes
from tests.conftest import (
    BASH_TOOL,
    DENSE_CALL,
    DENSE_CALL_TURN,
    DENSE_TOOL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    SLASH_TURN,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import (
    MISSING,
    fields,
    one,
    values,
    viewer_css,
)
from tests.view.scenarios import SCENARIOS


def test_a_node_page_cites_every_query_it_ran(client: TestClient) -> None:
    """A node page's footer holds one re-runnable line per query behind it.

    The session node is the case with the most reads behind one page: its own header, the
    level of the NavTree under it, and the runs and compactions every level needs to place. Each
    line carries the bindings this request made rather than the query file's defaults, which
    is what makes it a citation and not a filename.
    """
    page = client.get(f"/session/{SPINE}", params={"log": 3}).text
    assert fields(page, "id", "citation") == {
        "view_session_header": (
            f"-- queries/view_session_header.sql session_id={SPINE}"
            " head_chars=100 item_chars=60 head_items=5"
        ),
        "view_nav_tree_turns": (
            f"-- queries/view_nav_tree_turns.sql session_id={SPINE} source={MAIN}"
            f" nav_chars={queries.NAV_CHARS}"
        ),
        # A run is printed twice on this page — as a NavTree row and as a children log row — so
        # the citation says which of the two widths this request read them at: the wider.
        "view_runs": f"-- queries/view_runs.sql session_id={SPINE} chip_chars={queries.LOG_CHARS}",
        "view_compactions": (
            f"-- queries/view_compactions.sql session_id={SPINE} source={MAIN}"
            f" chip_chars={queries.NAV_CHARS}"
        ),
        # The whole thread in outline, which is what places the runs: no window, so no paging.
        "session_timeline": (
            f"-- queries/session_timeline.sql session_id={SPINE} log_chars={queries.LOG_CHARS}"
        ),
    }


def test_every_id_a_url_carries_is_named_by_the_word_in_front_of_it(client: TestClient) -> None:
    """Every id in a path has a word in front of it saying what kind of id it is.

    The one rule the URL scheme is built on (`docs/viewer.md`), and it has two halves. No two
    ids sit side by side: read a path that breaks that and the eye pairs the segments the wrong
    way — a turn and something under it, where the second id is really the thread the turn is
    on. And the word in front *names* the id, which is what the first half alone does not say:
    `/session/{session_id}/unattributed/{source}` puts no two ids together and still calls a
    thread by the name of the bucket hanging off it.

    Naming is checked across the table rather than against a list of words, which would be the
    rule written twice: an id kind that follows two different words is one of the two lying.
    That catches a word changed at one route and misses a parameter used at exactly one — for
    those, the closed registry in `test_bounds.py` is what holds the shape.

    `{kind}` is the one parameter that counts as a word rather than an id: it carries a member
    of `nodes.Kind`, and every one of those is a bare literal segment.
    """
    assert all(str(kind).isalpha() for kind in nodes.Kind)
    routes = [route for route in client.app.routes if isinstance(route, APIRoute)]  # pyrefly: ignore
    assert routes, "the app exposes no routes"
    naming: dict[str, set[str]] = defaultdict(set)
    for path in sorted(route.path for route in routes):
        segments = ["kind" if part == "{kind}" else part for part in path.split("/") if part]
        for at, part in enumerate(segments):
            if not part.startswith("{"):
                continue
            assert at, f"{path} opens on an id nothing names"
            assert not segments[at - 1].startswith("{"), f"{path} puts two ids side by side"
            # The parameter's own name, past the converter an offloaded file path carries.
            naming[part.strip("{}").partition(":")[0]].add(segments[at - 1])
    assert naming, "no route carries an id"
    for parameter, words in sorted(naming.items()):
        assert len(words) == 1, f"{parameter} is called {sorted(words)} at different routes"


# The ratio WCAG 2.2 asks of body text against what it is printed on. Both schemes are held
# to it: a dark page is a page someone reads, not a courtesy.
READABLE = 4.5


# How much of the accent the one wash a page composes carries — `:target` on a record, and a
# hovered node — over whatever surface it lands on.
WASH = 0.12


# The deepest step of the cost badge, which is the most of `--hot` a row's wash ever carries.
# A shallower step sits between it and the page it is painted on, so holding the deepest one
# readable holds every step above it: each is a step back toward the paper.
BADGE = 0.60


def _channel(value: int) -> float:
    """One sRGB channel, linearised — the relative-luminance formula's own step."""
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _contrast(ink: str, surface: str) -> float:
    lit, dark = sorted((_luminance(ink), _luminance(surface)), reverse=True)
    return (lit + 0.05) / (dark + 0.05)


def _over(ink: str, surface: str, part: float) -> str:
    """`color-mix(in srgb, ink part%, transparent)` painted over an opaque surface."""
    mixed = (
        round(
            int(ink[index : index + 2], 16) * part
            + int(surface[index : index + 2], 16) * (1 - part)
        )
        for index in (1, 3, 5)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def test_both_schemes_print_every_color_of_text_readably(client: TestClient) -> None:
    """Every color the stylesheet sets text in clears 4.5:1 over every surface it lands on.

    Read off the served stylesheet rather than written down, so a token retuned for one scheme
    cannot quietly darken the other. The surfaces are the page itself and the one wash the
    sheet composes rather than names — 12% of the accent, which a targeted record and a
    hovered node are both painted with, and which is where `--dim` comes closest to failing.
    A chip's outline is its own text color (`currentColor`), so it clears whatever this does.

    The cost badge is read apart from the rest: it is a surface only the dollar value is
    printed on, so it is held to `--ink` alone rather than to every role.
    """
    sheet = viewer_css(client)
    # Tokens are declared in exactly two places, and dark restates only what it changes.
    head, _, tail = sheet.partition("prefers-color-scheme: dark")

    def read(block: str) -> dict[str, str]:
        return dict(re.findall(r"--([a-z]+):\s*(#[0-9a-f]{6})", block))

    light = read(head)
    schemes = {"light": light, "dark": light | read(tail)}
    # Two rosters, closed: the colours text is printed in, and the surfaces under it — the
    # badge's warm ground and the three bands the context bar draws (`view/static/nav-tree.css`).
    # A surface carries no text of its own, so what holds it is the eye on the gallery
    # (`.claude/rules/viewer-ui.md`) and the ramp below, not a contrast ratio.
    assert set(light) == {"ink", "dim", "line", "paper", "mark", "bad"} | {
        "hot",
        "faint",
        "agent",
        "free",
    }
    for scheme, tokens in schemes.items():
        surfaces = {
            "the page": tokens["paper"],
            "the wash": _over(tokens["mark"], tokens["paper"], WASH),
        }
        for role in ("ink", "dim", "mark", "bad"):
            for where, surface in surfaces.items():
                ratio = _contrast(tokens[role], surface)
                assert ratio >= READABLE, f"{scheme} --{role} on {where}: {ratio:.2f}:1"
        # The badge composes over both of them, because the row under it may be the hovered one.
        for where, under in surfaces.items():
            ratio = _contrast(tokens["ink"], _over(tokens["hot"], under, BADGE))
            assert ratio >= READABLE, f"{scheme} --ink on the badge over {where}: {ratio:.2f}:1"
        # And the context bar's three grounds are a ramp: the track palest, the base band a
        # step in from it, the conversation over that. Each scheme runs the ramp its own way —
        # a light page darkens toward the reader, a dark one lightens — so what is held is the
        # order and not the direction. Two bands a reader cannot tell apart is one band.
        ramp = [_luminance(tokens[role]) for role in ("line", "faint", "dim")]
        assert ramp == sorted(ramp, reverse=scheme == "light"), (scheme, ramp)
        assert len(set(ramp)) == len(ramp), (scheme, ramp)


def test_the_stylesheet_a_browser_reads_carries_no_prose_outside_a_comment(
    client: TestClient,
) -> None:
    """Nothing in the served stylesheet sits between a comment's end and the rule below it.

    A comment closed twice is the one CSS mistake nothing else here can see: the browser reads
    the stray prose as the start of a selector, swallows the rule under it, and paints one
    fewer thing than the file says — silently, because a stylesheet has no syntax error a
    server or a test suite reports. Every comment this sheet opens is closed once, so a `*/`
    left over after the comments come out is prose a browser is about to read as a selector.
    """
    sheet = viewer_css(client)
    assert "*/" not in re.sub(r"/\*.*?\*/", "", sheet, flags=re.DOTALL)


def test_the_stylesheet_paints_only_fields_a_page_carries(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every `data-field` the stylesheet selects is a field a page writes, read off both.

    A `data-field` is what a test reads a page through, and the stylesheet reads pages through
    the same names — but nothing renders CSS, so a field renamed in a template leaves the rule
    behind, valid and matching nothing. One page carries all of them: a failed tool call's,
    whose tree names each node and marks the failure, whose walk names the kind either side,
    and which counts the session's failures under the pane.

    The `data-field` rules only. The depth ladder beside them runs to the NavTree's hard limit of
    16 levels and the deepest chain the corpus records is 14, so no page can show that the top
    of that ladder is live.
    """
    # The one failure this session recorded, which is the node whose page carries all four.
    source, tool_id = store.execute(
        "SELECT source, id FROM live_tool_calls WHERE session_id = ? AND is_error",
        [FORK_ORIGIN],
    ).fetchall()[0]
    page = client.get(f"/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}").text
    painted = set(re.findall(r'data-field="([a-z_]+)"', viewer_css(client)))
    assert painted, "the stylesheet no longer paints any field by name"
    assert painted <= set(re.findall(r'data-field="([a-z_]+)"', page))


def test_a_per_value_fragment_returns_the_one_value_it_names(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Opening one tool call's result fetches that call's and nothing else from the same call.

    The per-value routes are the exception to the payload bound — they ship a fat column
    whole — so what keeps the bound is that the unit really is one value. A fragment that
    quietly carried its siblings would be a page of them under another name.
    """
    siblings = [
        row[0]
        for row in store.execute(
            "SELECT id FROM live_tool_calls"
            " WHERE session_id = ? AND source = ? AND api_call_id = ?",
            [FORK_ORIGIN, FORK_ORIGIN_RUN, DENSE_CALL],
        ).fetchall()
    ]
    assert DENSE_TOOL in siblings and len(siblings) > 1
    served = client.get(
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
    ).text
    # The value it was asked for arrives, and it is not empty...
    whole = one(
        store,
        "SELECT length(result) FROM live_tool_calls WHERE id = ? AND session_id = ?",
        [DENSE_TOOL, FORK_ORIGIN],
    )[0]
    assert [int(size) for size in values(served, "data-value")] == [whole]
    # ...and no sibling of the same call rode along with it.
    for other in siblings:
        assert other == DENSE_TOOL or other not in served


def test_a_fragment_cites_the_query_that_fetched_it(client: TestClient) -> None:
    """Every whole-value fragment carries the query and the keys it was fetched by.

    A fragment arrives on a page that has already been served, so it cannot ride the footer
    the pages share: each one carries the line itself. All nine routes hand one shared seam
    their own keys, so each is here — a seam pinned through one route alone would still let
    another cite a key it was not fetched by.
    """
    keyed = f"session_id={FORK_ORIGIN} source={FORK_ORIGIN_RUN}"
    for url, expected in (
        (
            f"/fragment/text/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
            f"-- queries/view_call_text.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/thinking/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
            f"-- queries/view_call_thinking.sql {keyed} api_call_id={DENSE_CALL}",
        ),
        (
            f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
            f"-- queries/view_tool_input.sql {keyed} tool_call_id={DENSE_TOOL}",
        ),
        (
            f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
            f"-- queries/view_tool_result.sql {keyed} tool_call_id={DENSE_TOOL}"
            f" head_chars={queries.HEADER_CHARS}",
        ),
        # The command a `Bash` call ran, which only a `Bash` call has — so this one is keyed
        # off the thread that holds one rather than off the dense call above.
        (
            f"/fragment/command/session/{SPINE}/thread/{MAIN}/tool/{BASH_TOOL}",
            f"-- queries/view_tool_command.sql session_id={SPINE} source={MAIN}"
            f" tool_call_id={BASH_TOOL}",
        ),
        (
            f"/fragment/prompt/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/turn/{DENSE_CALL_TURN}",
            f"-- queries/view_turn_prompt.sql {keyed} turn_id={DENSE_CALL_TURN}",
        ),
        # The arguments of a slash turn, which only the one recorded slash turn has.
        (
            f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}",
            f"-- queries/view_turn_command_args.sql session_id={SPINE} source={MAIN}"
            f" turn_id={SLASH_TURN}",
        ),
        # A run is keyed by the session and its own id: a run has one home, so no thread
        # names it.
        (
            f"/fragment/brief/session/{FORK_ORIGIN}/run/{FORK_ORIGIN_RUN}",
            f"-- queries/view_run_brief.sql session_id={FORK_ORIGIN} run_id={FORK_ORIGIN_RUN}",
        ),
        # The record route keys on a line number rather than an id. Fetched off a subagent
        # thread at a line past the first, so neither key can be a constant the fixture hides.
        (
            f"/fragment/record/session/{SPINE}/thread/{SPINE_RUN}/line/2",
            f"-- queries/view_record.sql session_id={SPINE} source={SPINE_RUN} line_no=2",
        ),
    ):
        assert values(client.get(url).text, "data-query") == [expected], url


def test_a_fragment_naming_nothing_is_a_404(client: TestClient) -> None:
    """A per-value fragment for an id the store lacks is a 404, not an empty box."""
    response = client.get(
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{MISSING}"
    )
    assert response.status_code == 404
    assert MISSING not in response.text


@pytest.mark.parametrize("path", sorted(scenario.url for scenario in SCENARIOS.values()))
def test_every_asset_a_page_asks_for_is_one_the_viewer_ships(
    path: str, enriched_client: TestClient
) -> None:
    """No page reaches off the machine for an asset, and none writes an inline style.

    Both are things the policy in `app.CSP` forbids, and both fail the same way: loudly in a
    browser and silently in this tier, because a blocked asset and a dropped attribute leave
    a 200 behind. Read off what each route served — the fragments included, which no other
    page-level sweep renders — rather than off the code that builds it: a component composes
    another component, so what a source scan reads is never the page a reader gets.
    """
    served = enriched_client.get(path)
    assert served.status_code == 200, path
    # Every `src` and `href` the page writes is a path on this server...
    assert re.findall(r'(?:src|href)="(\w+:)?//[^"]*"', served.text) == [], path
    # ...and nothing carries a style attribute. This is the trap the cost badge's decile
    # classes exist to dodge: a wash written inline is a badge no reader ever sees.
    assert ' style="' not in served.text, path
    # ...and nothing wears the class htmx paints, which the config below stops it painting.
    assert "htmx-indicator" not in served.text, path


def test_the_frame_every_page_arrives_in_asks_only_for_assets_the_viewer_serves(
    client: TestClient,
) -> None:
    """Each asset the base page names is served from this app, htmx included."""
    page = client.get("/").text
    assets = re.findall(r'(?:src|href)="(/static/[^"]*)"', page)
    assert any("htmx" in asset for asset in assets), page
    for asset in assets:
        assert client.get(asset).status_code == 200, asset
    # A clean page is not enough: htmx writes a `<style>` block of its own for the indicator
    # class as it loads, which the policy blocks and the browser reports on every page. This
    # meta is what stops it writing one — htmx merges the config before it paints. Read back
    # through htpy's escaping: it quotes every attribute with `"` and escapes the JSON's own
    # quotes to `&#34;`, so what the browser parses is the config and what the source holds
    # is not.
    (config,) = re.findall(r'<meta name="htmx-config" content="([^"]*)">', page)
    assert json.loads(html.unescape(config))["includeIndicatorStyles"] is False


def test_serving_the_store_leaves_it_read_only(corpus_db: Path, client: TestClient) -> None:
    """Nothing the viewer serves writes to the store it is pointed at."""
    before = corpus_db.stat().st_mtime_ns
    client.get("/")
    client.get(f"/session/{SPINE}")
    client.get(f"/session/{MISSING}")
    assert corpus_db.stat().st_mtime_ns == before
