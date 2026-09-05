"""Each cut a surface makes: the width a value is printed at, and the mark where it was cut.

A cut here is the render-time half of a query's one-extra-character protocol — the query
returns a string one character past the width, and the cut marks it (`docs/viewer.md`). Which
width applies is a fact about the surface: a session-list row takes a head where a pane takes a
paragraph, so there is one function per surface rather than one with a size argument.

The two that read the world — the clock and the reader's home — read it per call, not per
process. A viewer left open is long-lived, and the gallery freezes `fmt.utcnow` after import
(`tests/gallery/serve.py`); either would be lost by a value captured once.
"""

import datetime as dt

from hyphae.view import bounds
from hyphae.view.text import format as fmt


def ago(value: dt.datetime | None) -> str:
    """How long ago, against the clock at render rather than one captured at startup."""
    return fmt.ago(value, fmt.utcnow())


def project_path(value: str | None) -> str:
    """A project directory, with the home of whoever is reading the page folded to `~`."""
    return fmt.path(value, fmt.home())


def line(value: str | None) -> str:
    """A row's string at the width a children log prints it, marked where it was cut.

    Every string a log row prints comes back from its query one character past this width, so a
    value that arrives longer than the cut is a value with more behind it. What
    `nodes.Node.log_title` does for a node's title, for the columns a row prints straight off
    the row.
    """
    return fmt.ABSENT if value is None else fmt.cut(value, bounds.LOG_WIDTHS.log_chars)


def head(value: object) -> object:
    """A header's value as a pane prints it: a string cut and marked, anything else as is.

    Applied by `components.parts.fact` to every value that reaches it rather than at the rows
    that need it, so a fact added beside them inherits the bound instead of printing a value
    whole. A header's other facts are flags and already-formatted numbers, and only a string
    the store holds can be longer than the pane: those go through as `fmt.text` leaves them.
    """
    if value is None:
        return fmt.ABSENT
    return fmt.cut(value, bounds.HEADER_WIDTHS.head_chars) if isinstance(value, str) else value


def short(value: str | None) -> str:
    """A string at the width a row of the session list prints it, marked where it was cut.

    The narrowest of the four: a row is multiplied by the page. Every string a transcript or a
    pass wrote in a row goes through this or `item` — the session's title, its project path,
    and the line a pass wrote about it — and the mark is what the link beside it makes good on:
    the whole value is on the session's page, a click away.

    Takes None like the other cuts do: it stands ahead of `project_path` on the project column,
    which is where a row's one nullable string is printed.
    """
    return fmt.ABSENT if value is None else fmt.cut(value, bounds.LIST_WIDTHS.head_chars)


def item(value: str) -> str:
    """One member of a list on a row of the session list, marked where the query cut it.

    What `member` does for a header's lists, at the width a row shows a skill or an agent type.
    The kinds of work beside them do not come through here: their vocabulary is closed
    (`enrich/taxonomy.py`), so the list's `kind_chars` is a bound the page's arithmetic needs
    rather than one a value reaches, and a mark there could never be true.
    """
    return fmt.cut(value, bounds.LIST_WIDTHS.item_chars)


def member(value: str) -> str:
    """One member of a header's list, marked where the query cut it.

    The list half of what `head` does for a header's own strings: a list is cut twice — to its
    first `head_items` members, which the pane counts out loud, and each member to the
    surface's `item_chars`, which nothing said until here.
    """
    return fmt.cut(value, bounds.HEADER_WIDTHS.item_chars)
