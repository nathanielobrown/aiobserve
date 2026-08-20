"""Code as a page shows it: a value's own syntax, marked by class rather than by color.

Two syntaxes and no more — the JSON a tool was passed and returned, and the SQL behind a page.
Everything else a transcript wrote is prose, and `view/render.py` renders it.

The markup is Pygments' with `nowrap`, so what comes back is a run of classed spans and the
template owns the `<pre>` around them. Classes rather than inline colors because the policy in
`app.CSP` allows no `style` attribute; `static/pygments.css` is where they are painted.

Escaping is Pygments' own — it escapes `&`, `<`, `>`, `"` and `'` before it writes a token —
which is what lets `lit` hand back `Markup`. A value that is not highlighted is escaped here
instead, so both arms leave by the same door.
"""

import json
from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import NamedTuple

from markupsafe import Markup, escape
from pygments import highlight
from pygments.filter import Filter
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import JsonLexer, SqlLexer
from pygments.token import Text, Whitespace, _TokenType

from aiobserve.view import bounds


class _PlainWhitespace(Filter):
    """Whitespace as itself, not as a token — a third of the markup is the space between.

    Pygments wraps every run of whitespace in a span of its own, and this viewer paints none
    of them: on the widest query the repo ships that is 10 KB of `<span class="w">` in 35 KB
    of output. A token the formatter has no class for is written out bare, so re-typing
    whitespace as text is what drops the span without dropping a character.
    """

    def filter(
        self, lexer: Lexer | None, stream: Iterable[tuple[_TokenType, str]]
    ) -> Iterator[tuple[_TokenType, str]]:
        for token, value in stream:
            yield (Text if token is Whitespace else token), value


class Syntax(StrEnum):
    """The syntaxes the viewer marks up, which is also what a template may ask for."""

    JSON = "json"
    SQL = "sql"


def _lexer(built: Lexer) -> Lexer:
    """One lexer as this viewer reads with it: its own tokens, whitespace left plain."""
    built.add_filter(_PlainWhitespace())
    return built


_LEXERS: dict[Syntax, Lexer] = {
    Syntax.JSON: _lexer(JsonLexer()),
    Syntax.SQL: _lexer(SqlLexer()),
}

# No wrapper: the `<pre>` and its `data-field` belong to the template, and a formatter that
# brought its own `<div class="highlight">` would put a second box around every value.
_FORMATTER = HtmlFormatter(nowrap=True)

# How far JSON is indented before it stops being readable and starts being a scroll.
_INDENT = 2

# How much indentation a value may gain before it is served as stored instead. Indenting is
# quadratic in nesting — 10 KB of nothing but `[` indents to 50 MB — while real values gain
# very little: across the canonical store on 2026-08-07, the worst of a 2,000-record sample
# gained 3,418 characters and the largest values in it gained 352.
_MAX_INDENT_CHARS = 20_000


class Lit(NamedTuple):
    """One value as a page prints it: the markup, and why it is plain where it is plain."""

    html: Markup
    # The syntax the spans are classed by, or None where the value is printed as it was
    # stored — because it did not parse, or because it is past the ceiling.
    syntax: Syntax | None
    # How long the value is where its length is the reason it was not marked up, else 0. What
    # the page says instead of highlighting it, so a reader knows the plainness is deliberate.
    over: int


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


def _readable(value: str) -> tuple[str, bool]:
    """A stored JSON value indented for reading, and whether it was JSON at all.

    Tool arguments and raw records are JSON *most* of the time. A value that does not parse is
    shown as it was stored rather than hidden: what it holds is the reason someone opened the
    fragment, and a JSON lexer over prose marks every other word as an error.

    A value nested deeply enough that indenting it would explode — or that the parser's own
    stack cannot hold — is shown as stored too, so what a fragment serves stays proportional
    to what the store holds. `_MAX_INDENT_CHARS` sets the line.
    """
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError):
        return value, False
    if not _indent_fits(parsed):
        return value, True
    return json.dumps(parsed, indent=_INDENT, ensure_ascii=False), True


def lit(value: str | None, syntax: Syntax) -> Lit:
    """One value ready for a `<pre>`: marked up in `syntax`, or printed as it was stored.

    Past `bounds.HIGHLIGHT_CHARS` the value comes back plain and says how long it is. The
    ceiling is characters rather than bytes on purpose: it guards the tokenizer's time and the
    markup's inflation — a span per token multiplies a value about fourfold — and neither of
    those is counted in bytes. A multibyte value under the ceiling is still marked up.
    """
    if not value:
        return Lit(Markup(), None, 0)
    text, known = _readable(value) if syntax is Syntax.JSON else (value, True)
    if not known:
        return Lit(escape(text), None, 0)
    if len(text) > bounds.HIGHLIGHT_CHARS:
        return Lit(escape(text), None, len(text))
    marked = highlight(text, _LEXERS[syntax], _FORMATTER)
    # Pygments ends every run with a newline. Inside a `<pre>` that is a blank line the value
    # does not have, so it goes wherever the value did not end with one itself.
    if not text.endswith("\n"):
        marked = marked.removesuffix("\n")
    return Lit(Markup(marked), syntax, 0)
