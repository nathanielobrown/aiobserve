"""What a transcript's own text is allowed to do once it reaches a page: nothing.

Every input here is invented markup, and it has to be — redaction flattens every string in
the fixture corpus, so no recorded session can carry a payload. These are the inner of the
two escaping layers the design names; the outer one is the planted-sentinel route test in
`tests/view/test_app.py`, which sees what a template does after `render.py` is done.

Two renderers are pinned here, because they answer the same escaping questions: `render.py`,
which turns a fat value into a block of prose, and `inline_markdown.py`, which turns one line
into a title. The second one reaches further — a NavTree row, a crumb, the browser tab — so
every pin below is taken twice.
"""

from markupsafe import escape

from hyphae.analyze import queries
from hyphae.view.text import inline_markdown, render
from hyphae.view.text.format import ELLIPSIS
from tests.view.conftest import plain


def test_html_in_markdown_source_arrives_as_text() -> None:
    """A `<script>` a transcript wrote renders as the characters, never as an element.

    markdown-it-py's `commonmark` preset turns HTML passthrough *on*, so this pin is one
    constructor argument away from being undone and nothing else in the suite would notice.
    """
    rendered = render.markdown("before\n\n<script>alert(1)</script>\n\nafter")
    # The markup is text on the page...
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    # ...and no part of it is an element the browser would run.
    assert "<script" not in rendered
    # The markdown around it still renders, so the pin costs the reader nothing.
    assert "<p>before</p>" in rendered


def test_an_inline_html_attribute_cannot_ride_in_either() -> None:
    """Passthrough is off inline as well as in a block: an `onerror` handler is text."""
    rendered = render.markdown("a paragraph with <img src=x onerror=alert(1)> in it")
    assert "<img" not in rendered
    assert "onerror=alert(1)&gt;" in rendered


def test_markdown_image_syntax_fetches_nothing() -> None:
    """`![](host)` renders a placeholder, so a transcript cannot make the browser call out.

    This is a second, independent hole: an `<img src>` the page emits is a request the
    browser makes on load, with no click and no HTML passthrough involved. The CSP header is
    the other wall behind it. Note the `href` assertion — simply *disabling* the image rule
    hands the syntax to the link rule, which puts the same host back in an attribute.
    """
    rendered = render.markdown("![pixel](https://evil.test/px?d=1)")
    # No image element, and the host reaches no attribute of anything else either...
    assert "<img" not in rendered
    assert 'src="' not in rendered
    assert 'href="' not in rendered
    # ...while alt text and URL stay visible as text, so a reader sees what was written.
    assert "[image: pixel — https://evil.test/px?d=1]" in rendered


def test_a_bare_url_does_not_become_a_link() -> None:
    """Linkify is off: text that looks like a URL stays text."""
    rendered = render.markdown("see https://evil.test/path for details")
    assert 'href="http' not in rendered
    assert "https://evil.test/path" in rendered


def test_only_an_http_url_becomes_a_link() -> None:
    """A URL a browser should follow is a link; every other scheme is shown as text."""
    # An http or https URL reaches the `href`, upper-case scheme and all...
    assert 'href="https://example.test/pr/1"' in render.link("https://example.test/pr/1")
    assert 'href="HTTP://example.test/"' in render.link("HTTP://example.test/")
    # ...while a scheme that runs or reads something local never does, though the reader still
    # sees what the transcript wrote.
    for url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc"):
        shown = render.link(url)
        assert "href" not in shown
        assert "<script>" not in shown
        assert url.split(":")[0] in shown
    # What the reader sees is the URL itself: a link whose text says something else is one
    # they cannot check before following it.
    assert plain(render.link("https://example.test/pr/1")) == "https://example.test/pr/1"
    # A quote in a URL cannot close the attribute it lands in.
    assert 'href="https://example.test/?q=&#34;' in render.link('https://example.test/?q="')


def test_a_fenced_block_is_marked_up_as_the_language_it_names() -> None:
    """A model's fenced code renders as code, in the syntax the fence claims.

    Most of what a model writes is prose with code in it, so the fence is where the two meet:
    the prose renders and the block inside it is lexed. The info string is what a model wrote,
    so a fence naming a language this viewer has no lexer for prints as it was written.
    """
    rendered = render.markdown("Run it:\n\n```bash\ncd /tmp && ls\n```\n")
    # The prose around the block still renders...
    assert "<p>Run it:</p>" in rendered
    # ...and the block is a `<pre>` classed like every other marked-up value on the page.
    assert '<pre class="code bash">' in rendered
    assert '<span class="nb">cd</span>' in rendered
    # The info string is typing rather than a token, so the language is read out of it: the
    # word a model capitalized, and the first word of a fence that goes on to name a file.
    for info in ("Bash", "BASH", "bash title=run.sh"):
        typed = render.markdown(f"```{info}\ncd /tmp && ls\n```\n")
        assert '<pre class="code bash">' in typed, info
        assert '<span class="nb">cd</span>' in typed, info
    # A fence naming nothing, or naming a language with no lexer here, is still a block.
    for info in ("", "html", "rust"):
        plain_block = render.markdown(f"```{info}\nx = 1\n```")
        assert '<pre class="code">x = 1' in plain_block, info


def test_the_markup_inside_a_fence_arrives_as_text() -> None:
    """A fence is not a way around the escaping: what a transcript wrote is inert either way.

    Both arms are checked, because they escape in different places — a marked-up block is
    escaped by the lexer and an unlexed one here.
    """
    for info in ("json", "html"):
        rendered = render.markdown(f'```{info}\n{{"a": "<img src=x onerror=y>"}}\n```')
        assert "<img" not in rendered, info
        assert "&lt;img src=x onerror=y&gt;" in rendered, info


def test_an_absent_value_renders_to_nothing() -> None:
    """A NULL column reaches the template as None, and an empty block beats a crash."""
    assert render.markdown(None) == ""
    assert render.link(None) == ""


def test_html_in_a_title_arrives_as_text_too() -> None:
    """`render.py`'s first pin, on the renderer that reaches a NavTree row.

    A title is composed into a link, a crumb and a browser tab, so an element escaping here
    lands in markup the block renderer never touches. Passthrough is off in the inline parser
    for the same one constructor argument, and nothing else in the suite would notice.
    """
    rendered = inline_markdown.render("<script>alert(1)</script> ran", links=True)
    # The markup is text in the title...
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    # ...and no part of it is an element the browser would run.
    assert "<script" not in rendered


def test_an_inline_attribute_cannot_ride_into_a_title_either() -> None:
    """An `onerror` handler in a description is characters, wherever the description prints."""
    rendered = inline_markdown.render("a run of <img src=x onerror=alert(1)> work", links=True)
    assert "<img" not in rendered
    assert "onerror=alert(1)&gt;" in rendered


def test_image_syntax_in_a_title_fetches_nothing() -> None:
    """`![](host)` in a title renders the placeholder the pane's prose renders.

    The second, independent hole: an `<img src>` is a request the browser makes on load, and a
    NavTree draws thousands of rows. One placeholder wording for both renderers, so a reader
    meets the same thing in a row and in the paragraph the row opens.
    """
    rendered = inline_markdown.render("![pixel](https://evil.test/px?d=1)", links=True)
    assert "<img" not in rendered
    assert 'src="' not in rendered
    assert 'href="' not in rendered
    assert "[image: pixel — https://evil.test/px?d=1]" in plain(rendered)


def test_only_an_http_url_becomes_a_link_and_only_where_the_surface_carries_one() -> None:
    """A link is an `<a>` in the pane's heading and text everywhere else.

    Every other surface that names a node prints the title inside a link already — a NavTree
    row, a crumb, the walk, the error stepper — and an `<a>` inside an `<a>` is markup the
    browser takes apart into something neither element meant. So the caller says whether this
    surface may carry one, and the scheme says whether this URL may be followed.
    """
    written = "see [PR #18](https://github.test/pr/18) for it"
    linked = inline_markdown.render(written, links=True)
    assert 'href="https://github.test/pr/18"' in linked
    assert "PR #18</a>" in linked
    # The same words, on a surface that is already a link: the text stands and the anchor does
    # not, so the row still reads and nothing nests.
    flat = inline_markdown.render(written, links=False)
    assert "href" not in flat and "<a" not in flat
    assert plain(flat) == "see PR #18 for it"
    # A scheme that runs or reads something local never reaches an `href`, on either surface,
    # while the words the transcript wrote still print.
    for url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc"):
        shown = inline_markdown.render(f"[go]({url})", links=True)
        assert "href" not in shown, url
        assert "<script>" not in shown, url
        assert "go" in plain(shown), url


def test_no_block_element_escapes_into_a_title() -> None:
    """A heading, a list and a fence are the characters they were typed as.

    Only the inline parser runs, so there is no rule that could open a `<p>` or a `<pre>`
    inside a line — which is what keeps a description a pass wrote in paragraphs from
    breaking the one line a NavTree row is.
    """
    written = "# Heading\n- one\n- two\n\n```py\nx = 1\n```"
    rendered = inline_markdown.render(written, links=True)
    for element in ("<h", "<ul>", "<li>", "<p>", "<pre>", "<ol>", "<blockquote>"):
        assert element not in rendered, element
    # The heading's own `#` survives as typing, and the fence's contents as a code run.
    assert "# Heading" in plain(rendered)
    assert "x = 1" in plain(rendered)


def test_a_title_renders_the_four_things_a_line_may_say() -> None:
    """Bold, italic, code and a link — and nothing else the vocabulary could grow."""
    rendered = inline_markdown.render(
        "**bold** *italic* `code` [link](https://x.test/)", links=True
    )
    assert "<strong>bold</strong>" in rendered
    assert "<em>italic</em>" in rendered
    assert "<code>code</code>" in rendered
    assert '<a href="https://x.test/">link</a>' in rendered


def test_a_title_with_no_markdown_in_it_is_what_it_was() -> None:
    """The renderer is a no-op on flat text, escaping included.

    Every title the fixture corpus records is flat — redaction saw to that — so this is the
    ordinary case, and the bytes it serves are what the page served before markdown was
    rendered at all. `markupsafe` does the escaping for that reason: markdown-it spells a
    quote `&quot;` where every other value on the page spells it `&#34;`, and a NavTree row
    is measured in bytes (`view/bounds.py`).
    """
    for flat in ('{"name": "deep-research"}', "⚡ rm -rf .mutants", "a & b < c > d 'e'"):
        assert inline_markdown.render(flat, links=False) == str(escape(flat)), flat
    # noqa: the `strip` under test is this module's own, not `str.strip`.
    assert inline_markdown.strip("⚡ rm -rf .mutants") == "⚡ rm -rf .mutants"  # noqa: B005


def test_a_title_is_cut_by_what_a_reader_sees_not_by_what_it_is_written_in() -> None:
    """A width is spent on visible characters, so markdown syntax never eats the budget.

    The cut lands inside the markup and closes it, because a surface's width is a promise
    about the line it draws and an unclosed `<strong>` would bold the rest of the page.
    """
    written = f"**{'x' * 20}**"
    # A cap the raw string is inside, so the width is the only thing cutting here.
    whole = len(written)
    assert (
        inline_markdown.cut(written, 8, links=False, source_cap=whole)
        == f"<strong>{'x' * 8}</strong>{ELLIPSIS}"
    )
    # The same string without its syntax, cut to the same width, shows the same characters.
    assert (
        plain(inline_markdown.cut(written, 8, links=False, source_cap=whole)) == "x" * 8 + ELLIPSIS
    )
    assert plain(inline_markdown.cut("x" * 20, 8, links=False, source_cap=20)) == "x" * 8 + ELLIPSIS
    # A line that fits carries no mark, and the four syntax characters are not counted.
    assert (
        inline_markdown.cut(written, 20, links=False, source_cap=whole)
        == f"<strong>{'x' * 20}</strong>"
    )
    # `strip` measures the same thing the cut spends, which is what lets the browser tab and
    # the row it names stop at the same word.
    assert len(inline_markdown.strip(written)) == 20


def test_a_title_the_query_cut_is_marked_however_short_its_markup_renders() -> None:
    """A width the renderer did not spend is not a line with nothing behind it.

    Every query composing a title cuts it one character past the width it was read for, so a
    raw string longer than that cap is one the store stopped. The syntax it was written in is
    not counted on the way in — measured: 280 characters of `**ab** ` reach the NavTree as the
    111 the query ships, render to 47 visible characters, and used to print with nothing at all
    saying the other 169 were dropped.
    """
    written = "**ab** " * 40
    stored = written[: queries.NAV_CHARS + 1]
    shown = inline_markdown.cut(
        stored, queries.NAV_CHARS, links=False, source_cap=queries.NAV_CHARS
    )
    # The row is a third of its own width and still says the session wrote more.
    assert len(plain(shown)) < queries.NAV_CHARS
    assert plain(shown).endswith(ELLIPSIS)

    # And the other side of the rule: a line the query did not cut carries no mark, however
    # much of its raw length is syntax. A crumb is the narrowest surface there is and the cap
    # behind it is a NavTree row's, so marking on raw length alone would stop a title here
    # that nothing stopped.
    complete = "**bold** `code` *and* `more` and **more**"
    assert len(complete) > queries.CRUMB_CHARS
    crumb = inline_markdown.cut(
        complete, queries.CRUMB_CHARS, links=False, source_cap=queries.NAV_CHARS
    )
    assert plain(crumb) == "bold code and more and more"


def test_a_markdown_run_the_query_cut_in_half_is_dropped_rather_than_printed() -> None:
    """The store's cut lands mid-line, and half a `**` run is not something a session wrote.

    Markdown-it hands an unclosed run back as the characters it was typed as, so the asterisks
    print — and nothing at print time can recover the closing pair the query never shipped. The
    mark says the line stopped; the delimiters would say the session typed them.
    """
    cut = inline_markdown.cut("**ab** **cd", 40, links=False, source_cap=10)
    assert cut == f"<strong>ab</strong>{ELLIPSIS}"
    # The same for the two other runs a cut can break: a backtick and a link's bracket.
    assert (
        plain(inline_markdown.cut("read `sr", 40, links=False, source_cap=7)) == f"read{ELLIPSIS}"
    )
    opened = inline_markdown.cut("see [PR #18](https://x.te", 40, links=True, source_cap=24)
    assert plain(opened) == f"see{ELLIPSIS}"
    # A run the cut did not break keeps its characters, and the mark alone says the line
    # stopped. One `*` or `_` is never read as a broken run: the corpus is paths and commands,
    # and `handoff_2` losing its tail to close an emphasis nobody opened is the worse trade.
    for typed, cap in (("2 * 3 * 4", 8), ("ls handoffs/handoff_2", 20), ("rm -rf *.mutants", 15)):
        assert plain(inline_markdown.cut(typed, 40, links=False, source_cap=cap)) == (
            typed + ELLIPSIS
        ), typed


def test_an_absent_title_renders_to_nothing() -> None:
    """A NULL column reaches a title as None, and an empty line beats a crash."""
    assert inline_markdown.render(None, links=True) == ""
    assert inline_markdown.cut(None, 10, links=False, source_cap=10) == ""
    assert inline_markdown.strip(None) == ""
