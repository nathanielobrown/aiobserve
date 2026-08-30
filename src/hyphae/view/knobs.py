"""What a URL may ask a node page for, and what a page hands back in every link it mints.

Four knobs: the view, and the three sizes (`docs/viewer-bounds.md`). A request's knobs are
checked here or answered with a 400, and minted back into the suffix every link on the page
carries — so a reader who narrowed the NavTree keeps it as they walk. The paging controls live
here too: a page number is the one knob a children log adds to that suffix.
"""

from collections.abc import Sequence
from urllib.parse import urlencode

from fastapi import HTTPException

from hyphae.view import bounds, nodes
from hyphae.view.components.logs import Pager
from hyphae.view.components.nav_tree import PresetChoice
from hyphae.view.store import Listed, Row

# What a node URL can name, at the value a link that names none is served at: the view, and
# the three sizes. Every href a node page mints carries whatever is *not* one of these
# (`knobs`), so a reader who picked a view or narrowed the NavTree keeps it as they walk, and an
# ordinary link stays short.
KNOB_DEFAULTS: dict[str, int | str] = {
    "nav": nodes.Preset.FULL,
    "kin": bounds.KIN.default,
    "log": bounds.LOG.default,
    "detail": bounds.DETAIL.default,
}


def knobs(nav: nodes.Preset, kin: int, log: int, detail: int) -> str:
    """The query string every link on a node page carries: whatever is not a default."""
    given = {
        name: value
        for name, value in (("nav", nav), ("kin", kin), ("log", log), ("detail", detail))
        if value != KNOB_DEFAULTS[name]
    }
    return f"?{urlencode(given)}" if given else ""


def checked(size: int, ceiling: int) -> int:
    """A page size from a query string, or a 400 — every route's sizes go through here."""
    if not 1 <= size <= ceiling:
        raise HTTPException(400, f"Ask for a page size between 1 and {ceiling}.")
    return size


def viewed(nav: str) -> nodes.Preset:
    """The filter preset from a query string, or a 400 — every node route's `?nav=` comes here.

    A 400 rather than a fallback to the full NavTree: a reader who typed a view the viewer does
    not have should be told, not served a different one under the URL they asked for.
    """
    if nav not in set(nodes.Preset):
        raise HTTPException(400, f"Filter the NavTree by one of: {', '.join(nodes.Preset)}.")
    return nodes.Preset(nav)


def carried(nav: str, kin: int, log: int, detail: int) -> str:
    """The knobs a request asked for, checked and minted back into the suffix its links carry."""
    return knobs(
        viewed(nav),
        checked(kin, bounds.KIN.ceiling),
        checked(log, bounds.LOG.ceiling),
        checked(detail, bounds.DETAIL.ceiling),
    )


def preset_choices(
    node: nodes.Node, nav: nodes.Preset, kin: int, log: int, detail: int
) -> list[PresetChoice]:
    """The node the reader is on under each preset, so switching never costs them their place."""
    return [
        PresetChoice(choice, f"{node.url}{knobs(choice, kin, log, detail)}", choice is nav)
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


def pager(url: str, marks: str, page: int, pages: int) -> Pager | None:
    """The control under a children log, or None where the level is one page long."""
    if pages < 2:
        return None
    return Pager(
        place=f"Page {page} of {pages}",
        previous=numbered(url, marks, page - 1) if page > 1 else None,
        next=numbered(url, marks, page + 1) if page < pages else None,
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
