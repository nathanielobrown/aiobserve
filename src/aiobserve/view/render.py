"""Turning what a transcript wrote into HTML that cannot act.

Both helpers hand back `Markup`, which Jinja prints without escaping — so this module owns
the escaping for every value it renders, and a mistake here is a live one. Two rules make
the difference and each is one argument from being lost:

- HTML passthrough is **off**. markdown-it-py's `commonmark` preset turns it on, so a
  `<script>` a transcript wrote would otherwise reach the page as an element
- An image **renders as a placeholder**. `![](https://host/px?d=1)` needs no passthrough
  and no click: the browser fetches the URL on load, which is egress a transcript controls.
  The CSP header in `view/app.py` is the second wall behind the same hole

`tests/view/test_render.py` pins both. It cannot see a template that pipes a value through
`|safe`, which is why the route-level sentinel test exists as well.
"""

import json
from collections.abc import MutableMapping, Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markupsafe import Markup, escape

# Explicit `html=False`, because the preset's default is True. Linkify off as well: a bare
# URL in a transcript is a string someone typed, not an invitation to make it clickable.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})

# How far JSON is indented before it stops being readable and starts being a scroll.
_INDENT = 2


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


def pretty(value: str | None) -> Markup:
    """A stored JSON value indented for reading, escaped — or the value itself if it is not.

    Tool arguments and raw records are JSON *most* of the time. A value that does not parse
    is shown as it was stored rather than hidden: what it holds is the reason someone opened
    the fragment.
    """
    if not value:
        return Markup()
    try:
        shown = json.dumps(json.loads(value), indent=_INDENT, ensure_ascii=False)
    except ValueError:
        shown = value
    return escape(shown)
