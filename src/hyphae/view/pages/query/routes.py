"""The query page: one library query's SQL, where every citation in a footer goes.

The name is a key of the query manifest and never a path, which is what makes a request for
`../../secret` a miss rather than a file (`docs/viewer.md`).
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from hyphae.analyze import macros, manifest, queries
from hyphae.view.citation import QUERY_URL
from hyphae.view.deps import ViewerDep
from hyphae.view.pages.query import markup

router = APIRouter()


@router.get(f"{QUERY_URL}/{{query_name}}")
def query_page(request: Request, query_name: str, viewer: ViewerDep) -> Response:
    """One library query's SQL, under the bindings a page cited it with.

    Where every citation in a footer goes. The name is a key of the query manifest and
    never a path: a name the manifest does not declare is a 404 before anything is read,
    which is what makes a request for `../../secret` a miss rather than a file.
    """
    if query_name not in manifest.names():
        raise HTTPException(404, "No query by that name ships with this build.")
    statement = queries.load(query_name)
    return viewer.html(
        markup.query_page(
            name=query_name,
            sql=statement,
            # What a shell has to run first, where the statement calls a library macro:
            # both consumers install these, and a reader pasting the statement alone has
            # no way to find out why the catalog does not know the name.
            macro_setup=macros.needed_by(statement),
            # Whatever the citation carried, printed back rather than bound to anything:
            # this page runs no query, so a binding here is a fact about the page that
            # sent you. It is the one place a request's own text reaches rendering, and it
            # crosses the seam as plain data rather than as the request.
            bindings=dict(request.query_params),
            dev=viewer.dev,
        )
    )
