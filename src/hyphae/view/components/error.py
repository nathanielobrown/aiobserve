"""The page every failure the app catches is answered with.

Under `components/` rather than in a page package because no route serves it: the exception
handlers in `view/app.py` render it, whichever page was being built when the failure happened.
"""

import htpy

from hyphae.view.components import Html, layout


def error_page(*, status: int, message: str, dev: bool) -> Html:
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
