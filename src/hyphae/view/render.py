"""Turning what a transcript wrote into HTML that cannot act.

Every helper hands back `Markup`, which Jinja prints without escaping — so this module owns
the escaping for every value it renders, and a mistake here is a live one. Three rules make
the difference, and the first two are each one argument from being lost:

- HTML passthrough is **off**. markdown-it-py's `commonmark` preset turns it on, so a
  `<script>` a transcript wrote would otherwise reach the page as an element
- An image **renders as a placeholder**. `![](https://host/px?d=1)` needs no passthrough
  and no click: the browser fetches the URL on load, which is egress a transcript controls.
  The CSP header in `view/app.py` is the second wall behind the same hole
- A URL is **a link only when its scheme is `http` or `https`**. Escaping leaves a
  `javascript:` URL intact, and an `href` is the one place a transcript's text is acted on

`tests/view/test_render.py` pins all three. It cannot see a template that pipes a value
through `|safe`, which is why the route-level sentinel test exists as well. The one place code
meets prose is a fenced block, and `view/highlight.py` marks that up; a value that is code
whole never comes through here at all.
"""

from collections.abc import MutableMapping, Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markupsafe import Markup, escape

from hyphae.view import highlight
from hyphae.view.format import ELLIPSIS

# Explicit `html=False`, because the preset's default is True. Linkify off as well: a bare
# URL in a transcript is a string someone typed, not an invitation to make it clickable.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})

# The schemes a rendered URL may carry into an `href`. Everything else a transcript can write
# there — `javascript:`, `data:`, `file:` — is shown as text instead. `view/inline_markdown.py`
# reads it too: one answer to where a browser may be pointed.
LINK_SCHEMES = ("http://", "https://")

# The class an image placeholder wears, so the stylesheet paints one thing wherever it lands.
IMAGE_CLASS = "image"


def image_text(alt: str, src: str) -> str:
    """What an image shows here instead of fetching: its alt text and its URL, in words.

    Written once because both renderers print it — a title and the paragraph that title opens —
    and a reader meeting two wordings would read them as two different things.
    """
    return f"[image: {alt or 'untitled'} — {src}]"


def _image(
    renderer: RendererHTML,
    tokens: Sequence[Token],
    index: int,
    options: Any,
    env: MutableMapping[str, Any],
) -> str:
    """What an image renders as: the alt text and the URL, as text.

    The renderer is replaced rather than the rule disabled. Disabling it hands `![x](url)`
    to the link rule instead, which puts the transcript's host straight back in an `href`.
    """
    token = tokens[index]
    shown = image_text(token.content, str(token.attrGet("src") or ""))
    return f'<span class="{IMAGE_CLASS}">{escape(shown)}</span>'


def _fence(
    renderer: RendererHTML,
    tokens: Sequence[Token],
    index: int,
    options: Any,
    env: MutableMapping[str, Any],
) -> str:
    """A fenced block as the code the fence says it is, in the same `<pre>` the rest of the
    viewer prints code in.

    A block whose language this viewer has no lexer for is escaped here instead, and so is one
    past the highlighter's ceiling — the class says what was marked up rather than what the
    fence claimed. JSON is re-laid-out for reading, the way every other JSON on a page is.
    """
    token = tokens[index]
    syntax = highlight.by_fence(token.info)
    shown = highlight.lit(token.content, syntax) if syntax else None
    if shown is None or shown.syntax is None:
        return f'<pre class="code">{escape(token.content)}</pre>\n'
    return f'<pre class="code {shown.syntax}">{shown.html}</pre>\n'


_MARKDOWN.add_render_rule("image", _image)
_MARKDOWN.add_render_rule("fence", _fence)


def markdown(text: str | None) -> Markup:
    """One value's markdown as HTML, with its markup rendered inert."""
    # The reader is configured above to render a transcript's own markup inert.
    return Markup(_MARKDOWN.render(text)) if text else Markup()  # noqa: S704


def link(url: str | None) -> Markup:
    """A URL as a link when a browser should follow it, and as text when it should not.

    The one value the viewer puts in an `href` is a PR URL, and a transcript wrote it. Escaping
    does not settle that: an escaped `javascript:` URL is still a `javascript:` URL in an
    attribute the browser acts on. Only `http` and `https` become links; the CSP header in
    `view/app.py` is the second wall behind this one.

    Nor does a value the page cut: half a URL is a URL somewhere else, so a value carrying the
    mark a cut leaves is text like anything else the browser should not follow.
    """
    if not url:
        return Markup()
    if not url.lower().startswith(LINK_SCHEMES) or url.endswith(ELLIPSIS):
        return escape(url)
    return Markup(f'<a href="{escape(url)}">{escape(url)}</a>')  # noqa: S704 — escaped above
