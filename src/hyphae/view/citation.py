"""How a page says what it ran: the line a reader re-runs, and the link to the query page.

Every footer in the viewer carries one. A page gathers what it ran as it runs it, `cited`
writes one of those both ways, and `view/components/citation.py` prints them — so what the
comment says was bound and what the link binds are one thing (`docs/viewer.md`).
"""

from collections.abc import Mapping
from typing import NamedTuple
from urllib.parse import urlencode

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.view.store import Library

# Where the SQL behind a page is read. Every citation in a footer links here, so the path is
# written once and the route below takes the query's name from it.
QUERY_URL = "/query"

# What a page ran and what it bound, in the order it ran them. A page accumulates one of these
# while it reads and hands it to its footer, so a query answered on the way to a page is cited
# whether or not the page kept anything from it.
Ran = list[tuple[Library, Mapping[str, ParamValue]]]


class Cited(NamedTuple):
    """One query a page ran, as the footer shows it: the line to re-run, and where to read it."""

    line: str
    url: str


def cited(name: str, bindings: Mapping[str, ParamValue]) -> Cited:
    """What produced a page, both ways a reader follows it.

    The line is what a report quotes and a shell re-runs; the URL is the same query as a page,
    bindings and all. Both spell a binding the one way `queries.shown` does, so the link a
    footer carries and the comment beside it cannot disagree about what was bound.
    """
    written = {key: queries.shown(value) for key, value in bindings.items()}
    return Cited(queries.citation(name, bindings), f"{QUERY_URL}/{name}?{urlencode(written)}")
