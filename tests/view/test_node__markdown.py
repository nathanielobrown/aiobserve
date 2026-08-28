"""Markdown in a title, from what a pass wrote to what a row serves.

A description is one line the model wrote in markdown, and every surface that prints a title
prints it rendered rather than typed (`view/inline_markdown.py`). No fixture holds one:
redaction flattened every string the corpus records, so these leaves plant the markdown and
read it back off a served page. The renderer's own readings are `tests/view/test_render.py`.
"""

import re
from html import unescape
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
from markupsafe import escape

from hyphae.view.app import build_app
from tests.conftest import MAIN
from tests.view.conftest import Planter, marked_up, one, plain

# Every place a page labels a title, and what a flat one holds: one run of text with no
# element in it. Both built from the same opening tag, so a count and a capture of the same
# page cannot drift apart.
TITLE_OPENS = '<span data-field="title">'
TITLE_SPAN = re.compile(TITLE_OPENS + r"([^<]*)</span>")


# What a pass would write in one line about a run: the three things a title may say and the
# one it may only say in the pane. Planted, because redaction flattened every string the
# corpus records — no fixture holds a title with markdown in it.
WRITTEN = "**bold** `code` [PR #18](https://github.test/pr/18)"


def test_the_markdown_a_pass_wrote_renders_in_a_title_and_links_only_in_the_pane(
    enriched_plant: Planter, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A description is written in markdown, so a title is rendered rather than printed.

    A pass writes one line about a turn and writes it the way it writes everything else: bold
    for the thing that matters, backticks around a path, a link to the PR it opened. Printed
    as typed, that line spends a NavTree row's width on asterisks.

    The link is the half only one surface can carry. A NavTree row, a crumb and a walk control
    are each already a link to the node they name, and an `<a>` inside an `<a>` is markup a
    browser takes apart into something neither element meant — so those three print the link's
    words and the pane's heading, which nothing wraps, gets the anchor.
    """
    # A turn with a sibling on either side, so the walk has two controls naming the same
    # description, and on the main thread, where a pass describes turns one apiece.
    session_id, turn_id = one(
        enriched_store,
        'SELECT session_id, id FROM live_turns WHERE source = ? AND "index" = 1'
        " AND session_id IN (SELECT session_id FROM live_turns WHERE source = ?"
        " GROUP BY 1 HAVING count(*) > 2) ORDER BY session_id LIMIT 1",
        [MAIN, MAIN],
    )
    path: Path = enriched_plant(("UPDATE turn_enrichments SET description = ?", [WRITTEN]))
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{MAIN}/turn/{turn_id}").text
    # The pane heads the node it is about, and nothing wraps that heading: the link is a link.
    assert marked_up(page, "data-body", "turn", "title") == (
        '<strong>bold</strong> <code>code</code> <a href="https://github.test/pr/18">PR #18</a>'
    )
    # The three surfaces that are links already render the same line without the anchor, so
    # the reader still sees the words the pass linked and nothing nests.
    inside_a_link = "<strong>bold</strong> <code>code</code> PR #18"
    assert marked_up(page, "data-nav-tree", f"turn:{turn_id}", "title") == inside_a_link
    assert marked_up(page, "data-crumb", f"turn:{turn_id}", "turn") == inside_a_link
    for stepped in ("previous", "next"):
        assert marked_up(page, "data-walk", stepped, "title") == inside_a_link, stepped
    # One `<a>` on the page holds the URL, and it is the one in the heading: the NavTree draws
    # this description on every turn row of the session, so a nested anchor would be everywhere.
    assert page.count('href="https://github.test/pr/18"') == 1


def test_the_browser_tab_and_every_attribute_carry_the_text_under_a_title(
    enriched_plant: Planter, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A `<title>` and an attribute have nowhere to put markup, so they take the text.

    Both print an element as characters or act on it, and neither is what the line says: a tab
    reading `**bold**` shows the asterisks, and markup in an attribute is the escape the whole
    of `view/inline_markdown.py` exists to close. So the tab takes the same cut, stripped.
    """
    session_id, turn_id = one(
        enriched_store,
        "SELECT session_id, id FROM live_turns WHERE source = ? ORDER BY session_id LIMIT 1",
        [MAIN],
    )
    path: Path = enriched_plant(("UPDATE turn_enrichments SET description = ?", [WRITTEN]))
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{MAIN}/turn/{turn_id}").text
    # The tab says what the line says, in none of the characters it was written in.
    tab = re.search(r"<title>(.*?)</title>", page)
    assert tab is not None and tab.group(1) == "❯ bold code PR #18 · hyphae"
    # And no attribute on the page carries a tag: an escaped value cannot hold a bare `<`, so
    # one here is markup that reached an attribute rather than the text a reader sees.
    for attribute, held in re.findall(r'\s(data-[a-z-]+|title)="([^"]*)"', page):
        assert "<" not in held and "**" not in held, attribute


def test_no_block_element_a_pass_wrote_escapes_into_a_navtree_row(
    enriched_plant: Planter, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A description written in paragraphs is still one line in a row.

    A pass is asked for a sentence and a model sometimes answers with a document. Only the
    inline parser runs, so there is no rule that could open a `<p>` or a `<pre>` inside a row
    — a row that held a block element would not be a row any more, and the NavTree draws
    thousands of them.
    """
    session_id, turn_id = one(
        enriched_store,
        "SELECT session_id, id FROM live_turns WHERE source = ? ORDER BY session_id LIMIT 1",
        [MAIN],
    )
    document = "# Heading\n- one\n- two\n\n```py\nx = 1\n```"
    path: Path = enriched_plant(("UPDATE turn_enrichments SET description = ?", [document]))
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{session_id}/thread/{MAIN}/turn/{turn_id}").text
    row = marked_up(page, "data-nav-tree", f"turn:{turn_id}", "title")
    for element in ("<h", "<ul>", "<li>", "<p>", "<pre>", "<ol>", "<blockquote>"):
        assert element not in row, element
    # The heading's own `#` and the list's dashes survive as the typing they are.
    assert "# Heading" in plain(row) and "- one" in plain(row)


def test_a_title_the_corpus_records_flat_is_served_as_the_bytes_it_always_was(
    client: TestClient, store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """Rendering markdown changed nothing about a title that has none in it.

    Every string the fixture corpus records is flat — redaction saw to that — which makes the
    whole corpus the control for the renderer standing between a value and the page. Read as
    bytes rather than as text, because a NavTree row's width is budgeted in bytes
    (`view/bounds.py`): a renderer that spelled one escape differently — markdown-it writes a
    quote `&quot;` where autoescape writes it `&#34;` — would move the ceiling without
    changing a word anyone reads.
    """
    sessions = [row[0] for row in store.execute("SELECT id FROM sessions ORDER BY id").fetchall()]
    assert sessions, "the fixture corpus records no session"
    read = 0
    for session_id in sessions:
        page = client.get(f"/session/{session_id}").text
        # `[^<]*` is the assertion as much as the capture: a title that rendered an element
        # would not match, so the count says every title span on the page is one run of text.
        flat = TITLE_SPAN.findall(page)
        assert len(flat) == page.count(TITLE_OPENS), session_id
        for held in flat:
            # Escaped the way the template escaped it before a renderer stood in the way:
            # unescaping and escaping again is a no-op only on autoescape's own spelling.
            assert str(escape(unescape(held))) == held, held
        read += len(flat)
    # A sweep that read nothing would pass on a viewer that served no rows at all.
    assert read > len(sessions), "the sweep found no title to read"

    # None of those 95 titles holds a character worth escaping, so the spelling is planted
    # rather than swept: five characters, on the surface the byte budget is measured on.
    marks = """a & b < c > d "e" 'f'"""
    spent = one(
        store,
        "SELECT session_id FROM live_turns WHERE source = ? GROUP BY 1"
        " ORDER BY count(*) DESC, 1 LIMIT 1",
        [MAIN],
    )[0]
    path: Path = plant(
        (
            "UPDATE turns SET prompt = ?, command_name = NULL, command_args = NULL"
            " WHERE session_id = ?",
            [marks, spent],
        )
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{spent}").text
    assert TITLE_OPENS + str(escape(marks)) + "</span>" in page
