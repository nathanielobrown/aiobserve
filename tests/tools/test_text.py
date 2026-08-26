"""What the shared table writer refuses: a cell that would not survive being spliced into a doc.

The generators feed it prose lifted from docstrings and descriptions, so the two characters
that end a markdown cell early — a pipe, and a newline — reach it whenever someone writes one
into a docstring. A cell carrying either does not fail loudly at splice time: it silently
shifts every column after it, or cuts the table in half. So the guard is the contract, and
these are the two shapes it exists for.
"""

import pytest

from tools import text


@pytest.mark.parametrize(
    ("cell", "why"),
    [
        ("holds a | pipe", "a pipe opens a column the header has no name for"),
        ("holds a\nnewline", "a newline ends the row, and the rest becomes prose"),
        ("holds\ta\ttab\nand a newline", "a newline anywhere in the cell, not only alone"),
    ],
    ids=["pipe", "newline", "newline-among-other-whitespace"],
)
def test_a_cell_that_would_break_the_table_crashes_the_writer(cell: str, why: str) -> None:
    with pytest.raises(ValueError, match="break the table"):
        text.table(("Header",), [(cell,)])


def test_a_cell_of_ordinary_prose_is_written_as_given() -> None:
    # The other side of the guard: nothing else is rejected, and nothing is escaped or
    # rewritten. A generator that wants backticks writes them, and they arrive intact.
    assert text.table(("Header",), [("`code`, a comma and a — dash",)]) == (
        "| Header |\n| --- |\n| `code`, a comma and a — dash |"
    )
