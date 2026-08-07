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
through `|safe`, which is why the route-level sentinel test exists as well.
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

# The schemes a rendered URL may carry into an `href`. Everything else a transcript can write
# there — `javascript:`, `data:`, `file:` — is shown as text instead.
_LINK_SCHEMES = ("http://", "https://")

# How far JSON is indented before it stops being readable and starts being a scroll.
_INDENT = 2

# How much indentation a value may gain before it is served as stored instead. Indenting is
# quadratic in nesting — 10 KB of nothing but `[` indents to 50 MB — while real values gain
# very little: across the canonical store on 2026-08-07, the worst of a 2,000-record sample
# gained 3,418 characters and the largest values in it gained 352.
_MAX_INDENT_CHARS = 20_000


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


def _indent_fits(parsed: object) -> bool:
    """Whether indenting a parsed value would add less than `_MAX_INDENT_CHARS`.

    Counts what `json.dumps(indent=…)` adds — a newline and one level of padding per member,
    plus a line for each closing bracket — and stops at the budget, so measuring a hostile
    value costs no more than the budget. The walk carries its own stack because the nesting
    that makes indenting expensive is the nesting that would overflow a recursive one.
    """
    added = 0
    stack: list[tuple[object, int]] = [(parsed, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: list[object] = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if not children:
            continue
        added += len(children) * (1 + (depth + 1) * _INDENT) + 1 + depth * _INDENT
        if added >= _MAX_INDENT_CHARS:
            return False
        stack.extend((child, depth + 1) for child in children)
    return True


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


def pretty(value: str | None) -> Markup:
    """A stored JSON value indented for reading, escaped — or the value itself if it is not.

    Tool arguments and raw records are JSON *most* of the time. A value that does not parse
    is shown as it was stored rather than hidden: what it holds is the reason someone opened
    the fragment.

    A value nested deeply enough that indenting it would explode — or that the parser's own
    stack cannot hold — is shown as stored too, so what a fragment serves stays proportional
    to what the store holds. `_MAX_INDENT_CHARS` sets the line.
    """
    if not value:
        return Markup()
    try:
        parsed = json.loads(value)
        indent = _indent_fits(parsed)
        shown = json.dumps(parsed, indent=_INDENT, ensure_ascii=False) if indent else value
    except (ValueError, RecursionError):
        shown = value
    return escape(shown)
