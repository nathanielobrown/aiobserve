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
            [
                htpy.summary["what produced this page"],
                htpy.ul[
                    [
                        htpy.li[htpy.a(data_field=name, href=ran.url)[htpy.code[ran.line]]]
                        for name, ran in citations.items()
                    ]
                ],
            ]
        ]
    ]
