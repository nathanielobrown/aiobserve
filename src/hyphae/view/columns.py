"""What a children log heads and fills: one table of columns per shape of log.

A pane lists one kind of child at a time, and each kind is read by different columns — what
tells two turns apart is not what tells two tool calls apart. This module is that table, plus
the marks a column head and a node's own kind both carry (`view/nodes.py:GLYPHS`).

The log components read `COLUMNS` and `nodes.spanned` counts it; nothing here knows what a node
is, so the table can be read by anything that renders a row.
"""

from enum import StrEnum
from typing import NamedTuple


class Shape(StrEnum):
    """What the pane's children log lists, which decides the macro a row renders through.

    A function of the selection's kind rather than a choice: a turn's children are api calls
    however the reader arrived at the turn, and a node with nothing under it has no log.
    """

    TURNS = "turns"
    CALLS = "calls"
    TOOLS = "tools"
    RUNS = "runs"
    NONE = "none"


class Column(NamedTuple):
    """One column of the pane's children log: what it prints, and how it heads itself.

    The word above the column is not here — it comes from `labels.label(field)`, the registry
    every header on every page reads, so a column and a pane's fact call the same store column
    the same thing. What is here is the icon beside that word and the class the cell carries:
    a number is read down a column, a time is read across a row, and neither survives being
    printed as prose.
    """

    field: str
    icon: str
    # `number` right-aligns and figures the digits, `when` keeps a time on one line, `what`
    # is the one wide column a row is identified by and links from. Plain text otherwise.
    css: str = ""


# The marks a column head and a node's own kind both carry. Written once, so the `⇄` over a
# turn's api-call count and the `⇄` on an api call's row in the NavTree cannot drift apart: they
# are one reader meeting one thing twice. The rest of each vocabulary is its own — a column
# counts things a kind is not, and two kinds no column counts are marked in `view/nodes.py`.
CALL_ICON = "⇄"
TOOL_ICON = "⚒"
RUN_ICON = "◎"
ERROR_ICON = "⚠"

# What each shape of children log shows, column by column, in the order it shows them. Per
# shape because the columns are the shape's own: what tells two turns apart is not what tells
# two tool calls apart. Every row fills every column of its shape — a log that skipped a cell
# where the store held nothing would slide every later value under the wrong heading — and
# `tests/view/test_node__logs.py` reads a served head against this table, and
# `tests/view/test_node__rows.py` the cells under it.
#
# One column of each shape is `what`: the wide one carrying the node's own words and the link
# to its page. The last is the control that opens the child's body in place.
COLUMNS: dict[Shape, tuple[Column, ...]] = {
    Shape.TURNS: (
        Column("turn_index", "#", css="number"),
        Column("title", "☰", css="what"),
        Column("api_calls", CALL_ICON, css="number"),
        Column("tool_calls", TOOL_ICON, css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.CALLS: (
        Column("call_index", "#", css="number"),
        # The row is named by the model that answered, with what it answered beside it: two
        # lines of the call's own words, which is what tells two calls of one model apart.
        Column("model", "◈", css="what"),
        Column("text", "☰", css="said"),
        Column("tool_calls", TOOL_ICON, css="number"),
        # What those tool calls were, named the way the log inside the call names them.
        Column("tool_titles", "⌨", css="called"),
        Column("text_chars", "¶", css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.TOOLS: (
        Column("tool_index", "#", css="number"),
        Column("name", TOOL_ICON),
        Column("title", "⌨", css="what"),
        Column("is_error", ERROR_ICON),
        Column("result_chars", "¶", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
    Shape.RUNS: (
        Column("agent_type", RUN_ICON),
        Column("title", "☰", css="what"),
        Column("tool_errors", ERROR_ICON, css="number"),
        Column("cost_usd", "$", css="number"),
        Column("started_at", "◷", css="when"),
        Column("body", "⌄"),
    ),
}
