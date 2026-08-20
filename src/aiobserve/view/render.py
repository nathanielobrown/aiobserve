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
through `|safe`, which is why the route-level sentinel test exists as well. Code a transcript
wrote is not prose and does not come through here: `view/highlight.py` marks it up.
"""

from collections.abc import MutableMapping, Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markupsafe import Markup, escape

# Explicit `html=False`, because the preset's default is True. Linkify off as well: a bare
# URL in a transcript is a string someone typed, not an invitation to make it clickable.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})

# The schemes a rendered URL may carry into an `href`. Everything else a transcript can write
# there — `javascript:`, `data:`, `file:` — is shown as text instead.
_LINK_SCHEMES = ("http://", "https://")


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
    return (
        f'<span class="image">[image: {escape(token.content or "untitled")}'
        f" — {escape(token.attrGet('src') or '')}]</span>"
    )


_MARKDOWN.add_render_rule("image", _image)


def markdown(text: str | None) -> Markup:
    """One value's markdown as HTML, with its markup rendered inert."""
    return Markup(_MARKDOWN.render(text)) if text else Markup()


def link(url: str | None) -> Markup:
    """A URL as a link when a browser should follow it, and as text when it should not.

    The one value the viewer puts in an `href` is a PR URL, and a transcript wrote it. Escaping
    does not settle that: an escaped `javascript:` URL is still a `javascript:` URL in an
    attribute the browser acts on. Only `http` and `https` become links; the CSP header in
    `view/app.py` is the second wall behind this one.
    """
    if not url:
        return Markup()
    if not url.lower().startswith(_LINK_SCHEMES):
        return escape(url)
    return Markup(f'<a href="{escape(url)}">{escape(url)}</a>')
