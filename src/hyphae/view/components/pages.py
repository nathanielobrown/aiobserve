"""The pages that are not a node's: an error, a query, the raw records, a file.

Each answers a question no single node holds, so each is a page of its own rather than a body
inside the node frame (`docs/viewer.md`).
"""

import htpy

from hyphae.view.components import layout, parts
from hyphae.view.highlight import Syntax


def error_page(*, status: int, message: str, dev: bool) -> htpy.Renderable:
    """What every failure the app catches is answered with — a status, a sentence, a way back.

    The message never repeats what was asked for: a request is untrusted text like any other.
    """
    return layout.page(
        tab_title=f"{status} — hyphae",
        scripts=None,
        main=htpy.section(id="error")[
            [
                htpy.h1(data_field="status")[status],
                htpy.p(data_field="message")[message],
                htpy.p[htpy.a(href="/")["Back to the projects"]],
            ]
        ],
        footer=None,
        dev=dev,
    )


def query_page(
    *, name: str, sql: str, macro_setup: str, bindings: dict[str, str], dev: bool
) -> htpy.Renderable:
    """One library query as the page that cited it links to.

    The SQL this build ships, under the bindings that page ran it with. Nothing is executed
    here — what a reader wants is what the numbers above meant, and the answer to that is the
    statement, not another result set. `macro_setup` is what a shell has to run first where the
    statement calls a library macro, and is empty where it calls none.
    """
    return layout.page(
        tab_title=f"{name}.sql · hyphae",
        scripts=None,
        main=htpy.article(id="query", data_sql=name)[
            [
                htpy.h1[f"{name}.sql"],
                _bindings(bindings),
                _setup(macro_setup),
                parts.code(value=sql, syntax=Syntax.SQL, field="sql"),
            ]
        ],
        footer=None,
        dev=dev,
    )


def _bindings(bindings: dict[str, str]) -> htpy.Renderable:
    """What the citing page bound the statement to, or a line saying it bound nothing."""
    if not bindings:
        return htpy.p(".plain")["Cited with no bindings."]
    return htpy.dl(".facts")[
        [
            htpy.div[[htpy.dt[key], htpy.dd(data_binding=key)[value]]]
            for key, value in bindings.items()
        ]
    ]


def _setup(macro_setup: str) -> htpy.Renderable | None:
    """The definitions the statement calls, above it — and nothing where it calls none.

    Both consumers install these before they run anything, so a reader who pastes the statement
    alone gets a catalog error and no way to find out why (`analyze/macros.py`).
    """
    if not macro_setup:
        return None
    return htpy.fragment[
        [
            htpy.p(".plain")[
                [
                    "Run these first: the definitions this statement calls, which ",
                    htpy.code["hp query"],
                    " and the viewer install before they run it.",
                ]
            ],
            parts.code(value=macro_setup, syntax=Syntax.SQL, field="macros"),
        ]
    ]
