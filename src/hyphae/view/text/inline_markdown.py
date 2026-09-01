"""One line of what a session wrote, rendered as a title: bold, italic, code, a link.

The second of the viewer's two escape paths, beside `view/text/render.py` — and the one that
reaches furthest. A title is printed in a NavTree row, a crumb, a walk control, the pane's own
heading and the browser tab, so a mistake here lands on every page at once. Which is why the
renderer below is an allowlist rather than a configuration: markdown-it parses the line, and
this module decides what each token it hands back may become. A token type it does not know is
a crash rather than a silent drop.

Four rules, three of them `view/text/render.py`'s own:

- **No block element.** Only the inline parser runs, so a heading, a list and a fence are the
  characters they were typed as — a `<p>` inside a NavTree row is not a row any more
- **HTML passthrough is off**, and an **image renders as a placeholder** rather than a fetch
- A URL is **a link only when its scheme is `http` or `https`**, and only where the caller
  says the surface can carry one. Every surface but the pane's heading prints its title inside
  a link already, and an `<a>` inside an `<a>` is markup a browser takes apart
- A **width is spent on what a reader sees**. `cut` counts visible characters and closes what
  it cut inside, so `**` never eats a row's budget and a stopped title never bolds the page.
  Which is why it is also told the width the *query* cut at: a line whose syntax the store cut
  through renders short, and only that cap says the reader is looking at part of it

Escaping is `markupsafe`'s and not markdown-it's, so a line with no markdown in it serves the
bytes the page served before this module existed: markdown-it spells a quote `&quot;` where
every other value on a page spells it `&#34;`, and a NavTree row is measured in bytes
(`view/bounds.py`). `tests/view/test_render.py` pins all of it, beside the block renderer's.
"""

from typing import NamedTuple

from markdown_it import MarkdownIt
from markupsafe import Markup, escape

from hyphae.view.text.format import ELLIPSIS
from hyphae.view.text.render import IMAGE_CLASS, LINK_SCHEMES, image_text

# The same reader `view/text/render.py` builds, and for the same two reasons: `html=False` because
# the preset's default is True, and linkify off because a bare URL in a transcript is a string
# someone typed. Only its inline parser is ever run.
_INLINE = MarkdownIt("commonmark", {"html": False, "linkify": False})


class _Line(NamedTuple):
    """One rendered line, both ways: what the page prints and what a width is measured on."""

    markup: Markup
    shown: str


def render(text: str | None, *, links: bool) -> Markup:
    """One line as the markup a surface prints, whole.

    `links` is the surface's answer to whether it may carry an `<a>`: True in the reading
    pane's heading, False everywhere a title is already inside a link. No default — a caller
    that does not know which surface it is cannot answer it.
    """
    return _line(text, links=links, size=None).markup


def cut(text: str | None, size: int, *, links: bool, source_cap: int) -> Markup:
    """One line as markup, at `size` visible characters, marked where the rest was left.

    Two cuts can stop a line and the mark stands for either. `size` is the surface's, spent on
    what a reader sees: the syntax a line is written in costs the reader nothing, so it costs
    the width nothing, and a cut landing inside a `<strong>` closes it before the mark.
    `source_cap` is the *query's* — every one of them ships a character past the width it cut
    at (`view/text/format.py:cut`), so a raw string longer than the cap is one the store stopped,
    however short it renders. The run that cut broke goes with it: half a `**` is delimiters
    the page would print as typing, and nothing here can recover the half the query kept.
    """
    return _line(text, links=links, size=size, source_cap=source_cap).markup


def strip(text: str | None) -> str:
    """One line as plain text — what `cut` measures, and what an attribute may carry.

    The browser tab and every `title=` attribute take this: markup in either is either
    printed as characters or acted on, and neither is what the line says.
    """
    return _line(text, links=False, size=None).shown


def _anchor(url: str, *, links: bool) -> str:
    """A link's opening tag, or nothing where this surface or this URL may not carry one.

    Nothing rather than a rendered `href` the browser refuses: escaping does not settle a
    `javascript:` URL, so the scheme decides, and the words the transcript wrote still print.
    """
    if not links or not url or not url.lower().startswith(LINK_SCHEMES):
        return ""
    return f'<a href="{escape(url)}">'


def _line(
    text: str | None, *, links: bool, size: int | None, source_cap: int | None = None
) -> _Line:
    """The walk every entry point above shares: one pass over the line's inline tokens.

    Both halves come out of the same walk so they cannot disagree — a width measured on one
    spelling of the line and spent on another is a row that stops in the wrong place.
    """
    if not text:
        return _Line(Markup(), "")
    tokens = list(_INLINE.parseInline(text, {})[0].children or ())
    # A raw string past the cap is one the query cut, and its cut landed wherever it landed:
    # the run it broke has no closing delimiter, so markdown-it hands it back as the characters
    # it was typed as. Only the last token can hold that run — everything before it closed.
    source_cut = source_cap is not None and len(text) > source_cap
    if source_cut and tokens and tokens[-1].type == "text":
        tokens[-1].content = _unbroken(tokens[-1].content)
    written: list[str] = []
    seen: list[str] = []
    # What is open at the cursor, innermost last: a cut has to close all of it.
    owed: list[str] = []
    room = size
    stopped = False
    for token in tokens:
        whole = True
        match token.type:
            case "text":
                whole = _write(token.content, written, seen, room)
            case "code_inline":
                whole = _write(token.content, written, seen, room, "<code>", "</code>")
            # A line break inside a title is whitespace to every surface that prints one, and
            # the character the store holds — so it is neither dropped nor turned into a `<br>`.
            case "softbreak" | "hardbreak":
                whole = _write("\n", written, seen, room)
            case "image":
                shown = image_text(token.content, str(token.attrGet("src") or ""))
                opening = f'<span class="{IMAGE_CLASS}">'
                whole = _write(shown, written, seen, room, opening, "</span>")
            case "strong_open":
                written.append("<strong>")
                owed.append("</strong>")
            case "em_open":
                written.append("<em>")
                owed.append("</em>")
            case "link_open":
                opened = _anchor(str(token.attrGet("href") or ""), links=links)
                written.append(opened)
                owed.append("</a>" if opened else "")
            case "strong_close" | "em_close" | "link_close":
                written.append(owed.pop())
            case unknown:
                raise ValueError(f"a title has no rule for a {unknown} token: {text!r}")
        if size is not None:
            room = size - sum(len(part) for part in seen)
        if not whole:
            stopped = True
            break
    # The mark goes outside what it cut, so a stopped title reads as the page stopping it
    # rather than as a word the session wrote. Either cut earns it.
    mark = ELLIPSIS if stopped or source_cut else ""
    tail = "".join(reversed(owed)) + mark
    # Every run in `written` is either one of this module's own literals or a value `_write`
    # escaped on the way in, which is what makes the join safe to declare as markup.
    return _Line(Markup("".join(written) + tail), "".join(seen) + mark)  # noqa: S704


# What a markdown run opens with. One of these surviving in a *text* token is one markdown-it
# could not pair, which after a query's cut is where that cut landed.
_OPENERS = "*_`["


def _unbroken(shown: str) -> str:
    """`shown` up to the run a cut left open, or the whole of it where none is open.

    Read from the right, because the broken run is the last one: everything before it paired.
    """
    at = len(shown) - 1
    while at >= 0:
        if (char := shown[at]) not in _OPENERS:
            at -= 1
            continue
        # The whole run, so `**` goes at once rather than one star at a time.
        start = at
        while start and shown[start - 1] == char:
            start -= 1
        if _opens(shown, start, at):
            return shown[:start].rstrip()
        at = start - 1
    return shown


def _opens(shown: str, start: int, end: int) -> bool:
    """Whether the run of one character between `start` and `end` is one a cut left open.

    Narrow on purpose. A title is mostly paths and commands, and dropping the tail of one to
    close a run nobody opened costs a reader more than a stray delimiter does — so an emphasis
    run counts at two characters and up, never the `*` of `*.tmp` or the `_` of `handoff_2`.
    """
    char = shown[start]
    after = shown[end + 1 : end + 2]
    if char in "*_":
        return end > start and bool(after) and not after.isspace()
    if char == "`":
        return "`" not in shown[end + 1 :]
    return _dangling(shown, start)


def _dangling(shown: str, at: int) -> bool:
    """Whether the bracket at `at` opens a link the cut took the end off.

    A bracket the line closes is typing — `[WIP] rewrite` is a title — unless the `](` after it
    opened a URL that never closes, which is a link the query stopped in the middle of.
    """
    closed = shown.find("]", at)
    if closed == -1:
        return True
    return shown[closed + 1 : closed + 2] == "(" and ")" not in shown[closed:]


def _write(
    shown: str,
    written: list[str],
    seen: list[str],
    room: int | None,
    before: str = "",
    after: str = "",
) -> bool:
    """One run of visible characters onto the line; False where the width ran out inside it.

    `before` and `after` are this module's own literals rather than anything a session wrote,
    which is what makes them safe to write beside an escaped value.
    """
    kept = shown if room is None else shown[:room]
    written.append(before + str(escape(kept)) + after)
    seen.append(kept)
    return len(kept) == len(shown)
