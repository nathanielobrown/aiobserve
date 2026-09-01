"""How one node-page URL becomes the four knobs its routes read, or a 400.

`KnobsDep` is what a node route declares instead of four query parameters of its own, so the
four defaults and the four refusals are written once. What a checked `Knobs` then does — the
suffix every link on the page carries, the paging it drives — is the presenter's
(`view/pages/node/knobs.py`).
"""

from typing import Annotated

from fastapi import Depends, HTTPException

from hyphae.view import bounds, nodes
from hyphae.view.deps import checked
from hyphae.view.pages.node.knobs import Knobs


def viewed(nav: str) -> nodes.Preset:
    """The filter preset from a query string, or a 400 — every node route's `?nav=` comes here.

    A 400 rather than a fallback to the full NavTree: a reader who typed a view the viewer does
    not have should be told, not served a different one under the URL they asked for.
    """
    if nav not in set(nodes.Preset):
        raise HTTPException(400, f"Filter the NavTree by one of: {', '.join(nodes.Preset)}.")
    return nodes.Preset(nav)


def asked(
    nav: str = nodes.Preset.FULL,
    kin: int = bounds.KIN.default,
    log: int = bounds.LOG.default,
    detail: int = bounds.DETAIL.default,
) -> Knobs:
    """The knobs one request asked for, or a 400 — declared here and in no handler.

    The one dependency behind `KnobsDep`, so the four defaults and the four refusals are
    written once rather than once per route. `knobs.KNOB_DEFAULTS` reads the same `bounds`.
    """
    return Knobs(
        viewed(nav),
        checked(kin, bounds.KIN.ceiling),
        checked(log, bounds.LOG.ceiling),
        checked(detail, bounds.DETAIL.ceiling),
    )


# What a node route declares instead of four query parameters of its own.
KnobsDep = Annotated[Knobs, Depends(asked)]
