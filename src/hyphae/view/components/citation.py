"""What produced a page, at the end of it: the query it ran, and the link to read the SQL.

Its own module rather than a function in `parts.py` because two mounts stand it in different
places — a list page's footer ends the document, and a node page's ends the reading pane, which
is the scroller that page has. `view/citation.py` composes what goes in it.
"""

from collections.abc import Mapping

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html


def footer(*, citations: Mapping[str, Cited]) -> Html | None:
    """Every query a page ran, folded shut — and nothing where a page ran none.

    Folded because it is provenance rather than content. Each line links to the query page for
    the statement it names, so a reader who wants to know what a column means reads the SQL.
    """
    if not citations:
        return None
    return htpy.footer(id="citation")[
        htpy.details(data_citations=len(citations))[
            [htpy.summary["what produced this page"], htpy.ul[_lines(citations)]]
        ]
    ]


def listed(*, citations: Mapping[str, Cited]) -> Html:
    """The same lines on a fragment, unfolded: what one swapped-in element ran.

    A fragment has no footer to end and nothing to fold away from — it is a handful of lines
    inside somebody else's page — so the provenance stands open. The lines are `footer`'s, so
    the two mounts cannot cite one query two ways.
    """
    return htpy.ul(".citations", data_citations=len(citations))[_lines(citations)]


def _lines(citations: Mapping[str, Cited]) -> list[Html]:
    """One line per query: the statement, linking to the page that prints its SQL."""
    return [
        htpy.li[htpy.a(data_field=name, href=ran.url)[htpy.code[ran.line]]]
        for name, ran in citations.items()
    ]
