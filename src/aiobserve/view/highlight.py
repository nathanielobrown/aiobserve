"""Code as a page shows it: a value's own syntax, marked by class rather than by color.

A syntax is here because a session writes it: the JSON a tool was passed and returned, the SQL
behind a page, the shell a `Bash` call ran, the markdown a `Read` returned, and the languages a
model fences a block of code in. Everything else a transcript wrote is prose, and
`view/render.py` renders it — marking up a file the viewer shows is a reading aid over the
source, never a rendering of it: a tool result is evidence, and it prints as it was stored —
character for character, which is what `_EXACT` and `_run` are for.

The markup is Pygments' with `nowrap`, so what comes back is a run of classed spans and the
template owns the `<pre>` around them. Classes rather than inline colors because the policy in
`app.CSP` allows no `style` attribute; `static/pygments.css` is where they are painted.

Escaping is Pygments' own — it escapes `&`, `<`, `>`, `"` and `'` before it writes a token —
which is what lets `lit` hand back `Markup`. A value that is not highlighted is escaped here
instead, so both arms leave by the same door.
"""

import json
import re
from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import NamedTuple, override

from markupsafe import Markup, escape
from pygments import highlight
from pygments.filter import Filter
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import BashLexer, JsonLexer, MarkdownLexer, PythonLexer, SqlLexer
from pygments.token import STANDARD_TYPES, Text, Whitespace, _TokenType

from aiobserve.view import bounds


class _PlainWhitespace(Filter):
    """Whitespace as itself, not as a token — a third of the markup is the space between.

    Pygments wraps every run of whitespace in a span of its own, and this viewer paints none
    of them: on the widest query the repo ships that is 10 KB of `<span class="w">` in 35 KB
    of output. A token the formatter has no class for is written out bare, so re-typing
    whitespace as text is what drops the span without dropping a character.
    """

    @override
    def filter(
        self, lexer: Lexer | None, stream: Iterable[tuple[_TokenType, str]]
    ) -> Iterator[tuple[_TokenType, str]]:
        for token, value in stream:
            yield (Text if token is Whitespace else token), value


class Syntax(StrEnum):
    """The syntaxes the viewer marks up, which is also what a template may ask for."""

    JSON = "json"
    SQL = "sql"
    BASH = "bash"
    MARKDOWN = "markdown"
    PYTHON = "python"


# What every lexer here is built with, so that marking a value up adds to it and edits none of
# it: Pygments strips the newlines at either end of what it lexes, and a result whose first
# line went missing is not the evidence it was stored as. The newline at the end is the
# formatter's rather than the lexer's, and `_lexed` is where that one goes; the two characters
# Pygments rewrites whatever the options say are `_run`'s.
_EXACT = {"stripnl": False}


def _lexer(built: Lexer) -> Lexer:
    """One lexer as this viewer reads with it: its own tokens, whitespace left plain."""
    built.add_filter(_PlainWhitespace())
    return built


_LEXERS: dict[Syntax, Lexer] = {
    Syntax.JSON: _lexer(JsonLexer(**_EXACT)),
    Syntax.SQL: _lexer(SqlLexer(**_EXACT)),
    Syntax.BASH: _lexer(BashLexer(**_EXACT)),
    Syntax.MARKDOWN: _lexer(MarkdownLexer(**_EXACT)),
    Syntax.PYTHON: _lexer(PythonLexer(**_EXACT)),
}

# What a file's name says its contents are. Only the suffixes this viewer has a lexer for: a
# `Read` result carries no type of its own, so the path the call asked for is the only evidence
# of what came back, and anything this map misses is shown as it was stored.
_SUFFIXES: dict[str, Syntax] = {
    ".md": Syntax.MARKDOWN,
    ".markdown": Syntax.MARKDOWN,
    ".py": Syntax.PYTHON,
    ".sql": Syntax.SQL,
    ".json": Syntax.JSON,
    ".sh": Syntax.BASH,
    ".bash": Syntax.BASH,
    ".zsh": Syntax.BASH,
}

# What a model writes after the three backticks of a fence, for the syntaxes this viewer
# reads. The enum's own names, plus the short spellings a model actually types.
_FENCED: dict[str, Syntax] = {syntax.value: syntax for syntax in Syntax} | {
    "py": Syntax.PYTHON,
    "sh": Syntax.BASH,
    "shell": Syntax.BASH,
    "zsh": Syntax.BASH,
    "md": Syntax.MARKDOWN,
}

# The line-number gutter Claude Code writes down the left of a file it read — `12\t`, one per
# line. It is not part of the file: a lexer that meets it reads a different language, where a
# heading whose `#` follows a number is no longer a heading.
_GUTTER = re.compile(r"^\s*\d+\t")

# The characters Pygments rewrites before any lexer reads them, kept out of its way.
_REWRITTEN = re.compile("([\r\ufeff])")


class _ShortClasses(HtmlFormatter[str]):
    """Pygments' own short class for a token, and never the chain of names above it.

    A lexer may hand back token types Pygments has no name for — the markdown lexer delegates a
    fenced block to whatever lexer the fence names, which is any lexer the library ships — and
    the formatter classes one of those with a name per step up to a type it does know
    (`l l-Scalar l-Scalar-Plain`). Two reasons not to: `static/pygments.css` paints the short
    names and nothing else, and how wide a class can be is a term in the page's byte budget
    (`tests/view/budgets.py:MARKED_CHAR_BYTES`). So an unnamed token is classed as the
    nearest named one above it, which is a class this viewer chose rather than one a library
    can widen under it.

    The hook is Pygments' own private one, so a release that renames it would leave this class
    doing nothing. What holds it is a leaf over a delegated block in `tests/view/test_highlight.py`.
    """

    def _get_css_classes(self, ttype: _TokenType) -> str:
        # Every token type descends from `Token`, which Pygments names itself, so the walk up
        # ends at a name — and a type from outside that tree is written out with no class.
        named: _TokenType | None = ttype
        while named is not None and named not in STANDARD_TYPES:
            named = named.parent
        return self.classprefix + STANDARD_TYPES[named] if named is not None else ""


# No wrapper: the `<pre>` and its `data-field` belong to the template, and a formatter that
# brought its own `<div class="highlight">` would put a second box around every value.
_FORMATTER = _ShortClasses(nowrap=True)

# How far JSON is indented before it stops being readable and starts being a scroll.
_INDENT = 2


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
    """Whether indenting a parsed value would add less than `bounds.INDENT_CHARS`.

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
        if added >= bounds.INDENT_CHARS:
            return False
        stack.extend((child, depth + 1) for child in children)
    return True


def by_suffix(suffix: str | None) -> Syntax | None:
    """The syntax a read file's name implies, or None where the viewer shows it as stored.

    Takes the suffix rather than the path because the queries that ask this extract one: a
    header query cuts every column it returns, and a path cut to a pane's width would lose the
    end that names it. Case is folded here so the store keeps what the session wrote.
    """
    return _SUFFIXES.get(suffix.lower()) if suffix else None


def by_fence(info: str | None) -> Syntax | None:
    """The syntax a fenced block claims, or None where this viewer has no lexer for it.

    The info string is what a model typed above its code, so it is a claim rather than a fact:
    an unknown one prints the block as it was written. Only the first word is read — a fence
    can carry a filename or attributes after the language.
    """
    named = (info or "").strip().split(" ")[0].lower()
    return _FENCED.get(named)


def _run(text: str, lexer: Lexer) -> str:
    """One stretch of text marked up, character for character.

    Two characters a lexer never sees as themselves: Pygments rewrites every carriage return
    as a newline and drops a byte-order mark before the options in `_EXACT` apply
    (`Lexer._preprocess_lexer_input`). Neither can be turned off, so the text is cut at them
    and they are written back as they were stored — the stretches between are lexed. A lexer
    reading a stretch forgets what the stretch before it opened, which is the price, and it is
    rarely paid: of the 134,738 values the canonical store holds that this viewer marks up,
    none carries a carriage return and six carry a mark (read 2026-08-20).
    """
    # `re.split` on a capturing pattern alternates: lexed, rewritten, lexed. Neither character
    # it splits on is one HTML escapes, so each goes back into the markup as itself.
    return "".join(
        piece if at % 2 else _lexed(piece, lexer) for at, piece in enumerate(_REWRITTEN.split(text))
    )


def _lexed(text: str, lexer: Lexer) -> str:
    """One stretch through the lexer, ending where the stretch ended.

    The formatter closes its last line with a newline of its own. Inside a `<pre>` that is a
    blank line the value does not have, so it goes wherever the text did not end with one.
    """
    marked = highlight(text, lexer, _FORMATTER)
    return marked if text.endswith("\n") else marked.removesuffix("\n")


def _marked(text: str, lexer: Lexer) -> str:
    """A value marked up: in one pass, or a line at a time behind a `Read` result's gutter.

    Lexing line by line is what peeling the gutter costs — a lexer reading one line forgets
    what the line before it opened — so it is done only for a value whose first line is
    numbered, and the numbers are classed as the gutter they are. They hold digits and a tab
    by construction, so there is nothing in them to escape.
    """
    lines = text.splitlines(keepends=True)
    if not lines or not _GUTTER.match(lines[0]):
        return _run(text, lexer)
    pieces = []
    for line in lines:
        gutter = _GUTTER.match(line)
        # What is left once the gutter is its own span: the whole line when there is none.
        rest = line
        if gutter:
            pieces.append(f'<span class="lineno">{gutter.group()}</span>')
            rest = line[gutter.end() :]
        pieces.append(_run(rest, lexer))
    return "".join(pieces)


def _readable(value: str) -> tuple[str, bool]:
    """A stored JSON value indented for reading, and whether it was JSON at all.

    Tool arguments and raw records are JSON *most* of the time. A value that does not parse is
    shown as it was stored rather than hidden: what it holds is the reason someone opened the
    fragment, and a JSON lexer over prose marks every other word as an error.

    A value nested deeply enough that indenting it would explode — or that the parser's own
    stack cannot hold — is shown as stored too, so what a fragment serves stays proportional
    to what the store holds. `bounds.INDENT_CHARS` sets the line.
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
    # `_marked` escapes every token it wraps — the spans around them are this module's.
    return Lit(Markup(_marked(text, _LEXERS[syntax])), syntax, 0)  # noqa: S704
