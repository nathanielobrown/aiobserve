"""What the route table has to hold: every page the app serves, described by its own docstring.

The world is the live app rather than a fixture — the whole point of generating the table is
that a page cannot ship undocumented, and only the app itself can say which pages exist.
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.routing import Route

from aiobserve.view import nodes
from tests.tools.conftest import cells
from tools import gen_routes


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """The viewer over an empty store, built once — every leaf here reads routes, not rows."""
    return gen_routes.built_app()


@pytest.fixture(scope="module")
def table() -> str:
    """The generated table, built once through the same path the cog block runs."""
    return gen_routes.generate()


def gets(app: FastAPI) -> list[Route]:
    """Every route the app answers a GET at — the mounted static files are not one."""
    return [
        route for route in app.routes if isinstance(route, Route) and "GET" in (route.methods or ())
    ]


def served(app: FastAPI) -> set[str]:
    """The path of each of those routes."""
    return {route.path for route in gets(app)}


def listed(table: str) -> set[str]:
    """The route of every row of the generated table, read out of its second column."""
    return {row[1].strip("`") for row in cells(table)}


def test_every_page_the_app_serves_is_in_the_table(app: FastAPI, table: str) -> None:
    # The headline: a new page must not be able to ship undocumented. Every GET the app
    # answers is either a row of the table, a fragment (excluded by rule), or a named
    # exclusion — nothing else is allowed to be missing.
    fragments = {path for path in served(app) if path.startswith(gen_routes.FRAGMENT_ROOT)}
    expected = served(app) - fragments - set(gen_routes.EXCLUDED)
    missing = expected - listed(table)
    assert not missing, f"pages the app serves that the table does not list: {sorted(missing)}"
    assert listed(table) == expected


def test_every_exclusion_is_a_route_that_still_exists(app: FastAPI) -> None:
    # A suppression outlives what it suppressed unless something says otherwise: a deleted
    # route must take its exclusion with it rather than leaving a line nobody can explain.
    assert set(gen_routes.EXCLUDED) <= served(app)


def test_fragment_routes_are_excluded_by_rule(table: str) -> None:
    # Fragments are excluded by where they live, not by a list someone maintains — so the
    # constants the app mints them from are what this leaf reads.
    for prefix in (nodes.BODY_URL, nodes.KIN_URL):
        assert prefix.startswith(gen_routes.FRAGMENT_ROOT)
        assert not [route for route in listed(table) if route.startswith(prefix)]


def test_a_page_with_no_docstring_crashes_the_generator() -> None:
    # A blank description is worse than no table: the generator names the handler and stops.
    stub = FastAPI(docs_url=None, redoc_url=None)

    @stub.get("/undocumented")
    def undocumented() -> PlainTextResponse:
        return PlainTextResponse("")

    undocumented.__doc__ = None
    with pytest.raises(ValueError, match="undocumented"):
        gen_routes.table(stub)


def test_a_description_is_the_handlers_own_first_sentence(app: FastAPI, table: str) -> None:
    # The docstring is the single source for the description column, so a row's words are
    # the handler's own — read here through the session page, which every reader lands on.
    handler = next(route for route in gets(app) if route.path == "/sessions")
    described = {row[1].strip("`"): row[0] for row in cells(table)}
    assert (handler.endpoint.__doc__ or "").startswith(described["/sessions"])


def test_the_table_ends_without_its_own_newline(table: str) -> None:
    # `main()` prints, and the cog splice owns the framing newline: a generator that ended
    # with one would leave a blank line inside every block it fills.
    assert table and not table.endswith("\n")
