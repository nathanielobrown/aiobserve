"""The small pieces a page is built out of, each printed the one way the viewer prints it."""

import htpy

from hyphae.view import bounds, highlight
from hyphae.view import format as fmt
from hyphae.view.components import Html


def code(*, value: str, syntax: highlight.Syntax, field: str) -> Html:
    """One value in the syntax it was written in — a tool's arguments, a record, a query file.

    Marked up by class rather than by inline colour, because the policy in `app.CSP` allows no
    `style` attribute; `static/pygments.css` paints the classes. A value past the ceiling prints
    as stored and says so: the markup costs about four bytes for every byte of value, and past a
    point that is a page nobody can read rather than a page that reads better.
    """
    shown = highlight.lit(value, syntax)
    return htpy.fragment[
        [
            htpy.p(".plain", data_plain=field)[
                [
                    "Printed as stored: ",
                    htpy.span(data_field="over")[fmt.count(shown.over)],
                    " characters is past the ",
                    fmt.count(bounds.HIGHLIGHT_CHARS),
                    " this viewer marks up.",
                ]
            ]
            if shown.over
            else None,
            htpy.pre(data_field=field, class_=f"code {shown.syntax}" if shown.syntax else None)[
                shown.html
            ],
        ]
    ]
