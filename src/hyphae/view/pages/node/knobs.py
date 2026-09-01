"""What a URL may ask a node page for, and what a page hands back in every link it mints.

Four knobs: the view, and the three sizes (`docs/viewer-bounds.md`). Every node route takes
them as one `Knobs`, built by the one dependency `routes/knobs.py` declares — which is where a
value outside its bounds becomes a 400. A checked `Knobs` mints its own `suffix`, which every
link on the page carries, so a reader who narrowed the NavTree keeps it as they walk. The paging
controls live here too: a page number is the one knob a children log adds to that suffix.
"""

from collections.abc import Sequence
from typing import NamedTuple
from urllib.parse import urlencode

from hyphae.view import bounds, nodes
from hyphae.view.components.parts import Pager, Step
from hyphae.view.pages.node.markup.nav_tree import PresetChoice
from hyphae.view.store import Listed, Row


class Knobs(NamedTuple):
    """The four things a node-page URL may name, checked, and the suffix its links carry.

    `deps.asked` is the only builder a route reaches: it parses the query string, refuses what
    is out of bounds, and is what makes the fields below already-checked values. Composing a
    variant off one — the preset control does, one link per preset — goes through `_replace`,
    which needs no second check because it starts from a value that passed.
    """

    nav: nodes.Preset
    kin: int
    log: int
    detail: int

    @property
    def suffix(self) -> str:
        """The query string every link on a node page carries: whatever is not a default."""
        default = KNOB_DEFAULTS._asdict()
        given = {name: value for name, value in self._asdict().items() if value != default[name]}
        return f"?{urlencode(given)}" if given else ""


# What a node URL naming no knob is served at, read off `bounds` like the defaults `deps.asked`
# declares. Every href a node page mints carries whatever is *not* one of these
# (`Knobs.suffix`), so a reader who picked a view or narrowed the NavTree keeps it as they walk,
# and an ordinary link stays short. `tools/gen_bounds.py` reads it for the knob table in
# `docs/viewer-bounds.md`.
KNOB_DEFAULTS = Knobs(
    nodes.Preset.FULL, bounds.KIN.default, bounds.LOG.default, bounds.DETAIL.default
)


def preset_choices(node: nodes.Node, knobs: Knobs) -> list[PresetChoice]:
    """The node the reader is on under each preset, so switching never costs them their place."""
    return [
        PresetChoice(choice, f"{node.url}{knobs._replace(nav=choice).suffix}", choice is knobs.nav)
        for choice in nodes.Preset
    ]


def numbered(url: str, marks: str, page: int) -> str:
    """One page of a node's children log as a URL: the node, its knobs, and the page number.

    Page one is the node's own URL. A reader who pages back to the start has to land on the
    document a link to the node serves, and it is the one the payload sweep prices.
    """
    if page == 1:
        return f"{url}{marks}"
    return f"{url}{marks}{'&' if marks else '?'}page={page}"


def pager(url: str, knobs: Knobs, page: int, pages: int) -> Pager | None:
    """The control under a children log, or None where the level is one page long."""
    if pages < 2:
        return None
    marks = knobs.suffix
    return Pager(
        field="place",
        words=f"Page {page} of {pages}",
        previous=Step(numbered(url, marks, page - 1), "← previous page") if page > 1 else None,
        next=Step(numbered(url, marks, page + 1), "next page →") if page < pages else None,
    )


def skipped(page: int, size: int) -> int:
    """How many children the pages before this one held — what a numbered page binds to skip."""
    return (page - 1) * size


def sliced(items: Sequence[Row], page: int, size: int) -> Listed:
    """One numbered page of rows already in memory, cut the way a query's OFFSET cuts one.

    The unattached runs are the case: they arrive with the session's runs, which every level of
    the NavTree needs anyway, so paging them is slicing rather than a second read.
    """
    start = skipped(page, size)
    return Listed(list(items[start : start + size]), len(items))
