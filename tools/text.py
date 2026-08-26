"""Shared shapes for the generators: a markdown table, a number, and a lifted gloss.

Nothing here decides what a table says — only how the generated text is written, so three
generators splice tables a reader cannot tell apart. Every helper crashes on a shape it did
not expect rather than letting a document carry it silently.
"""

import re
from collections.abc import Iterable

# Where a sentence does not end, however much it looks like it does. Small and closed on
# purpose: a gloss cut short is cheaper to spot than a list nobody prunes.
ABBREVIATIONS = ("e.g", "i.e", "etc", "vs", "cf")


def table(headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> str:
    """A markdown table, header and all, with no trailing newline for the cog splice to double.

    Cells are written as given: a generator that wants backticks writes them. A cell holding a
    pipe or a line break would break the table, so it crashes here instead.
    """
    lines = [_row(headers), _row(("---",) * len(headers))]
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"row has {len(row)} cells, not {len(headers)}: {row}")
        lines.append(_row(row))
    if len(lines) == 2:
        raise ValueError("a generated table with no rows is a generator that read nothing")
    return "\n".join(lines)


def _row(cells: tuple[str, ...]) -> str:
    for cell in cells:
        if "|" in cell or "\n" in cell:
            raise ValueError(f"cell would break the table: {cell!r}")
    return "| " + " | ".join(cells) + " |"


def count(value: int) -> str:
    """A number as the docs print it, thousands separated."""
    return f"{value:,}"


def gloss(text: str) -> str:
    """The first sentence of `text`'s first paragraph, as one line, its final period dropped.

    What a lifted gloss is: prose living beside the thing it describes, cut to the one sentence
    a table cell or a tree line can carry. Both are fragments, so a terminal period comes off.
    Empty prose crashes — the caller names the source it came from.
    """
    paragraph = " ".join(text.strip().split("\n\n")[0].split())
    if not paragraph:
        raise ValueError("no prose to lift")
    for match in re.finditer(r"[.!?](?=\s|$)", paragraph):
        head = paragraph[: match.start()]
        if head.rsplit(" ", 1)[-1] in ABBREVIATIONS:
            continue
        mark = paragraph[match.start()]
        return head if mark == "." else head + mark
    # A paragraph that ends without punctuation is already the sentence.
    return paragraph
