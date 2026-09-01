"""The offload page's markup: the typed file, and the chunk of it this page serves."""

from collections.abc import Mapping
from typing import NamedTuple
from urllib.parse import quote

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, layout
from hyphae.view.text import format as fmt


class OffloadFile(NamedTuple):
    """One offloaded tool result as its page prints it, built from its store row."""

    name: str
    size_bytes: int
    content_chars: int
    lossy_decode: bool
    chunk: str


def offload_page(
    *,
    session_id: str,
    file: OffloadFile,
    after: int | None,
    size: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """One chunk of a tool result Claude Code wrote to a file beside the transcript.

    `after` is where the next chunk starts, or None where this one reached the end.
    """
    return layout.page(
        tab_title=f"{file.name} — hyphae",
        scripts=None,
        main=htpy.section(id="offload", data_offload=file.name)[
            [
                htpy.h1(data_field="name")[file.name],
                htpy.p(".numbers")[
                    [
                        htpy.a(href=f"/session/{session_id}")[session_id],
                        htpy.span[
                            [
                                htpy.span(data_field="size_bytes")[fmt.count(file.size_bytes)],
                                " bytes on disk",
                            ]
                        ],
                        htpy.span[
                            [
                                htpy.span(data_field="content_chars")[
                                    fmt.count(file.content_chars)
                                ],
                                " chars stored",
                            ]
                        ],
                        # Only when it happened: the extractor could not decode the file as
                        # text and replaced what it could not read, so what is shown here is
                        # not what the tool wrote.
                        htpy.span(data_field="lossy_decode")["some bytes did not decode as text"]
                        if file.lossy_decode
                        else None,
                    ]
                ],
                htpy.pre(data_field="content")[file.chunk],
                htpy.p(".more", data_more_offload=after)[
                    htpy.a(
                        href=f"/session/{session_id}/offload/{quote(file.name, safe='/')}"
                        f"?after={after}&size={size}"
                    )[f"next {fmt.count(size)} chars"]
                ]
                if after is not None
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )
