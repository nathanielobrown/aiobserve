"""The errors page's markup: the list of failures, and one row of it."""

from collections.abc import Mapping, Sequence

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, layout
from hyphae.view.failures import Failure
from hyphae.view.text import format as fmt


def errors_page(
    *,
    session_id: str,
    listed: Sequence[Failure],
    cut: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """Where one session failed, whichever thread it happened on.

    A list rather than a pane: a failure is not a place in the NavTree, so there is nothing to
    open a path to. Each row leads to the tool call's own page, which carries the crumbs that
    place it.
    """
    return layout.page(
        tab_title=f"{session_id} errors — hyphae",
        scripts=None,
        main=htpy.section(id="errors")[
            [
                htpy.h1["Failed tool calls"],
                htpy.p(".numbers")[
                    [
                        htpy.a(href=f"/session/{session_id}")[session_id],
                        htpy.span[
                            [
                                htpy.span(data_field="matched")[fmt.count(len(listed) + cut)],
                                " failed call(s)",
                            ]
                        ],
                    ]
                ],
                htpy.ol(".errors")[[_failure(item=item) for item in listed]],
                # What the page left out, said rather than dropped: the store keeps every
                # failure, and this page shows the first of them in the order they happened.
                htpy.p(".more", data_more_errors=cut)[
                    [
                        "+",
                        htpy.span(data_field="cut")[fmt.count(cut)],
                        " more failed call(s)",
                    ]
                ]
                if cut
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )


def _failure(*, item: Failure) -> Html:
    """One failed tool call as the list shows it: where it reads, whose thread, and when."""
    return htpy.li(data_error=item.node.key)[
        [
            htpy.a(href=item.node.url)[htpy.span(data_field="title")[item.node.nav_tree_title]],
            # The thread it ran on, because the list spans all of them: two failures of one
            # tool name are told apart by which agent hit them.
            htpy.span(".source", data_field="source")[item.node.source],
            htpy.span(data_field="started_at")[fmt.when(item.started_at)],
        ]
    ]
