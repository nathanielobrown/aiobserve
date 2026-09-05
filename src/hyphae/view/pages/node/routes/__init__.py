"""Every URL the node page answers, gathered into one router.

Four modules carry routes, by what the reader asked for: a whole page, a child opened in
place, the numbers behind a NavTree row, and the rest of a cut value — an enrichment line
being one of those, declared in the registry like every other Detail. Two more carry none.
`browse` is the one response those routes share, and lives here rather than beside
the presenters because it decides a status and builds a response. `knobs` is the dependency
they take it through, which parses the four knobs a node URL may name and refuses the rest.
"""

from fastapi import APIRouter

from hyphae.view.pages.node.routes import details, expansions, pages, popovers

# Extended rather than `include_router`, for the reason `view/app.py` extends this one.
router = APIRouter()
for part in (pages, expansions, popovers, details):
    router.routes.extend(part.router.routes)
