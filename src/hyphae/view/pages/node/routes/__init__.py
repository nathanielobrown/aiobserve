"""Every URL the node page answers, gathered into one router.

Five modules by what the reader asked for: a whole page, a child opened in place, the numbers
behind a NavTree row, an enrichment line, and the rest of a cut value. `browse` is the one
response the eight page routes share, and lives here rather than beside the presenters because
it decides a status and builds a response.
"""

from fastapi import APIRouter

from hyphae.view.pages.node.routes import details, enrichment, expansions, pages, popovers

# Extended rather than `include_router`, for the reason `view/app.py` extends this one.
router = APIRouter()
for part in (pages, expansions, popovers, enrichment, details):
    router.routes.extend(part.router.routes)
