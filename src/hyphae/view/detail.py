"""A value too fat for the pane it lands in: the head it shows, and the way to the rest.

Nothing here decides how much to show — the head arrives already cut, in SQL, at the `?detail=`
the request asked for. What a `Detail` adds is what the pane needs beside the head: how much
was left behind, where to fetch it, and how to mark it up. The enrichment lines are the same
shape, because a pass writes past the width as readily as a transcript does.
"""

from typing import NamedTuple, assert_never

from hyphae.analyze import queries
from hyphae.enrich.prompts import Level
from hyphae.view import format as fmt
from hyphae.view import highlight, nodes
from hyphae.view.enrichment import Enrichment


class Detail(NamedTuple):
    """One fat column of a node as its pane shows it: the head, and the way to the rest.

    A pane never decides how much of a value it shows — the head is cut in SQL at the
    `?detail=` the request asked for, and `cut` is what that left for the link to offer.
    """

    name: str
    head: str
    cut: int
    url: str
    # What the head is marked up as, where the record says what the value is written in — the
    # shell a `Bash` call ran, the file a `Read` returned.
    syntax: highlight.Syntax | None
    # And whether what is left is the markdown someone wrote it in. A person and a model write
    # markdown; a program writes what it writes, so a tool's arguments and its output are
    # printed as the store holds them. No value is both, and a syntax the record named wins.
    markdown: bool


class EnrichmentLines(NamedTuple):
    """The two lines an enrichment pass wrote about a node, as the pane shows them.

    Each is a `Detail` like any other fat value the pane previews: the head the query cut, and
    the fetch that brings the rest of it back into the block the head stood in. A pass writes
    as much as it wants to, and nearly every run it describes runs past the width.
    """

    description: Detail | None
    # None where the model saw no friction, which is most items, and where it wrote an empty
    # line — the two are the same nothing to a pane.
    friction: Detail | None


def detail_of(
    name: str,
    head: str | None,
    chars: int | None,
    url: str,
    size: int,
    syntax: highlight.Syntax | None = None,
    *,
    markdown: bool,
) -> Detail | None:
    """One fat column as a pane shows it, or None where the store holds nothing under it.

    Nothing is a NULL or an empty string alike: a value with no characters in it has no
    preview to show and nothing to offer the rest of, whichever of the two the column holds.

    `head` arrives one character past `size`, which is how a value with more behind it is told
    from one that ends where the pane does; `chars` is the whole length the link offers.
    `syntax` is what the record says the value is written in, and the default is prose:
    everything a session wrote is prose until something in the row says otherwise.

    `markdown` says whether that prose is rendered as the markdown it was written in. It takes
    no default: whether a value came from a person, a model or a program is a fact about the
    column, and two callers of one route can read the same column either way — a subagent's
    answer reaches a run's pane as prose and a tool's pane as the output of a program.
    """
    if not head:
        return None
    cut = (chars or 0) - size if len(head) > size else 0
    return Detail(name, fmt.cut(head, size), cut, url, syntax, markdown)


def enrichment_lines(
    about: Enrichment | None, session_id: str, source: str
) -> EnrichmentLines | None:
    """What a pass wrote about the selection, each line with the way to the rest of it.

    The keys are the level's own: a turn's row is keyed by the thread the page is reading, a
    run's and a session's by the session. `source` is that thread, which is the same one the
    descriptions were read for.
    """
    if about is None:
        return None
    match about.level:
        case Level.turn:
            at = f"{nodes.thread_url(session_id, source)}/turn/{about.item_id}"
        case Level.agent_run:
            at = nodes.run_url(session_id, about.item_id)
        case Level.session:
            at = nodes.session_url(about.item_id)
        case _:
            assert_never(about.level)
    return EnrichmentLines(
        description=detail_of(
            "description",
            about.description,
            about.description_chars,
            f"/fragment/description{at}",
            queries.ENRICHMENT_CHARS,
            markdown=False,
        ),
        friction=detail_of(
            "friction",
            about.friction,
            about.friction_chars,
            f"/fragment/friction{at}",
            queries.ENRICHMENT_CHARS,
            markdown=False,
        ),
    )
