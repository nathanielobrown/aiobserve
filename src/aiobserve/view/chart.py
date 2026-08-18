"""The session page's context panel: bucketed rows in, SVG geometry out.

Pure functions over the rows `view_context_timeline.sql` returned, mirroring `threads.py`:
nothing here reads a store or a request. Two shapes come out of one `build` — a line of what
the thread's context had grown to by the end of each turn, and a stacked band per token type
of what each turn spent — because the two are the same rows read twice.

Everything that leaves this module is a number this code computed: a coordinate, a token
count, a turn index. No string a transcript wrote reaches the panel, which is what keeps its
byte cost arithmetic instead of subject to the five-byte escaping factor every other surface
of this viewer budgets for (`tests/view/test_bounds.py`).

The drawing area is a `viewBox`, not a pixel size: the stylesheet scales it to whatever width
the page gives it, and the coordinates below stay three digits a side whatever the session
holds.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, NamedTuple

from aiobserve.view.store import Row
from aiobserve.view.threads import lands

# The `viewBox` both charts are drawn in. Chosen so every coordinate is at most three digits:
# the payload bound counts eight bytes a point (`"NNN,NNN "`), and a taller or wider box would
# spend a fourth digit `CONTEXT_POINTS` times over for no more shape.
WIDTH = 600
HEIGHT = 140

# Room around the plot area for the x-axis labels: the strip under it they sit in, and the
# margin either side that keeps the first and last from clipping at the box's edge.
LABEL_GAP = 16
LABEL_MARGIN = 12
VIEW_BOX = f"{-LABEL_MARGIN} 0 {WIDTH + 2 * LABEL_MARGIN} {HEIGHT + LABEL_GAP}"
LABEL_Y = HEIGHT + LABEL_GAP - 4

# The fewest points that make a shape. One point draws nothing a reader can read — a polyline
# of one vertex and a band of zero width — so a thread with one answering turn gets no panel
# rather than an empty one, the same silence `build` keeps for a thread with none.
MIN_POINTS = 2

# The x-axis labels. Sparse by design: a label per point would be `CONTEXT_POINTS` strings of
# chrome, and the question this panel answers is the shape rather than any one turn's number.
TICKS = 5


class TokenType(StrEnum):
    """The four token counts a call reports, stacked bottom-up in this order.

    The values are the columns `view_context_timeline.sql` sums, so a band reads its own row
    field by name. `output_tokens` is here even though it is not part of context size — the
    two charts deliberately total different numbers, and the legend says so.
    """

    INPUT = "input_tokens"
    CACHE_READ = "cache_read_tokens"
    CACHE_CREATION = "cache_creation_tokens"
    OUTPUT = "output_tokens"

    @property
    def label(self) -> str:
        """What the legend calls this band — the column name without its unit."""
        return self.value.removesuffix("_tokens").replace("_", " ")


# What context size is: the three token counts a request carried, output excluded. Named here
# because the line chart and the legend's caption have to mean the same thing by it.
CONTEXT_TYPES = (TokenType.INPUT, TokenType.CACHE_READ, TokenType.CACHE_CREATION)


class Band(NamedTuple):
    """One token type's stacked area: what to fill, and what to call it."""

    type: TokenType
    # The `d` attribute of a `<path>`: the band's lower edge left to right, then its upper
    # edge back again, closed.
    outline: str


class Mark(NamedTuple):
    """Where a compaction fell, and the drop it made — drawn as a rule across both charts."""

    x: int
    pre_tokens: int
    post_tokens: int


class Tick(NamedTuple):
    """One x-axis label: where it sits, and the turn index it names."""

    x: int
    turn_index: int


@dataclass(frozen=True)
class Chart:
    """Both charts of one thread, as coordinates a template writes into two `<svg>` blocks."""

    # The `points` attribute of the context line's `<polyline>`.
    context_line: str
    # The composition bands, bottom-up in `TokenType` order.
    bands: tuple[Band, ...]
    compaction_marks: tuple[Mark, ...]
    x_ticks: tuple[Tick, ...]
    # The scale each chart is drawn against, which is also the top axis label it prints. They
    # are different numbers: one is a snapshot of context, the other a turn's whole spend.
    y_max: int
    spend_max: int
    # Whether `$max_points` grouped real turns, so a point is several turns rather than one.
    bucketed: bool

    # The geometry the template writes into the `<svg>` wrapper. Read off the instance so the
    # box, the depth a compaction's rule spans and the axis baseline are defined once, here,
    # rather than repeated as literals in markup that has to agree with the coordinates above.
    view_box: ClassVar[str] = VIEW_BOX
    plot_height: ClassVar[int] = HEIGHT
    label_y: ClassVar[int] = LABEL_Y


def build(rows: Sequence[Row], compactions: Sequence[Row]) -> Chart | None:
    """The panel for one thread, or None when there is no shape to draw.

    `rows` are `view_context_timeline.sql`'s, in bucket order, and `compactions` are
    `view_compactions.sql`'s for the same thread — already capped by the page, because a mark
    is drawn on both charts and an uncapped list is a bound nothing holds.

    None for a thread whose turns made no api call at all, and for one that made calls in a
    single turn: neither has a shape, and both have their numbers on the timeline below.
    """
    if len(rows) < MIN_POINTS:
        return None
    xs = tuple(round(index * WIDTH / (len(rows) - 1)) for index in range(len(rows)))
    context = [int(row["context_tokens"]) for row in rows]
    spend = [[int(row[token]) for token in TokenType] for row in rows]
    # A thread whose calls reported nothing at all would divide by zero; a floor of 1 draws it
    # flat along the bottom, which is what a chart of zeroes should look like.
    y_max = max(max(context), 1)
    spend_max = max(max(sum(turn) for turn in spend), 1)
    return Chart(
        context_line=_polyline(xs, [_y(value, y_max) for value in context]),
        bands=_bands(xs, spend, spend_max),
        compaction_marks=tuple(
            Mark(_mark_x(xs, lands(mark, rows)), mark["pre_tokens"], mark["post_tokens"])
            for mark in compactions
        ),
        x_ticks=_ticks(xs, rows),
        y_max=y_max,
        spend_max=spend_max,
        bucketed=any(row["first_turn_index"] != row["last_turn_index"] for row in rows),
    )


def _y(value: int, scale: int) -> int:
    """One value as a y coordinate: the box is drawn from the top, so a larger value sits
    higher."""
    return HEIGHT - round(value * HEIGHT / scale)


def _polyline(xs: Sequence[int], ys: Sequence[int]) -> str:
    """A run of coordinates as SVG writes them — the eight bytes a point the bound counts."""
    return " ".join(f"{x},{y}" for x, y in zip(xs, ys, strict=True))


def _bands(xs: Sequence[int], spend: Sequence[Sequence[int]], scale: int) -> tuple[Band, ...]:
    """The four token types as stacked areas, each drawn on top of the ones before it.

    A band is one closed path rather than a filled polyline so the fill is the band itself and
    not everything under it: the lower edge left to right, the upper edge back, closed.
    """
    bands: list[Band] = []
    lower = [0] * len(xs)
    for index, token in enumerate(TokenType):
        upper = [under + turn[index] for under, turn in zip(lower, spend, strict=True)]
        edges = _polyline(xs, [_y(value, scale) for value in lower])
        back = _polyline(xs[::-1], [_y(value, scale) for value in upper[::-1]])
        bands.append(Band(token, f"M {edges} {back} Z"))
        lower = upper
    return tuple(bands)


def _mark_x(xs: Sequence[int], position: int) -> int:
    """Where a compaction's rule sits: between the points it fell between.

    `position` is `threads.lands`'s — the index of the point whose turn the compaction
    preceded, and `len(xs)` for the marks that trail the whole thread.
    """
    if position == 0:
        return xs[0]
    if position >= len(xs):
        return xs[-1]
    return (xs[position - 1] + xs[position]) // 2


def _ticks(xs: Sequence[int], rows: Sequence[Row]) -> tuple[Tick, ...]:
    """`TICKS` labels at most, evenly spaced, always including the first point and the last."""
    positions = sorted({round(step * (len(xs) - 1) / (TICKS - 1)) for step in range(TICKS)})
    return tuple(Tick(xs[at], int(rows[at]["first_turn_index"])) for at in positions)
