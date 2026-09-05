"""A value too fat for the pane it lands in: the head it shows, and the way to the rest.

Nothing here decides how much to show — the head arrives already cut, in SQL, at the `?detail=`
the request asked for. What a `Detail` adds is what the pane needs beside the head: how much
was left behind, where to fetch it, and how to mark it up. The enrichment lines are the same
shape, because a pass writes past the width as readily as a transcript does.

`DETAILS` is where each of those values is declared, once: its name, the two queries behind it,
the URL its whole is fetched from, and how it was written. A pane reads a spec through
`preview` and the fetch reads the same spec, so the six places that used to agree by string
equality are one entry.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NamedTuple, assert_never

from hyphae.analyze import queries
from hyphae.enrich.items import Level
from hyphae.view.enrichment import Enrichment
from hyphae.view.store import Page, Value
from hyphae.view.text import format as fmt
from hyphae.view.text import highlight


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


class Written(StrEnum):
    """How a Detail was written, which decides how both surfaces render it."""

    # A person or a model wrote it, so it is prose and rendered as the markdown it was
    # written in. Every other member is something a program wrote, printed as it was stored.
    MARKDOWN = "markdown"
    # The two a record names outright: a `Bash` call ran shell, and a tool's arguments are an
    # object, so neither has to ask the row what it is holding.
    BASH = "bash"
    JSON = "json"
    # And the one that does ask: the suffix of the file a `Read` returned, else JSON. It is
    # the one arm whose fetch binds `head_chars`, because that suffix is cut in SQL.
    NAMED_FILE = "file"
    # A line an enrichment pass wrote: a span rather than a block, and the only kind whose
    # fetch is gated — a store no pass has touched holds no table to read it from.
    LINE = "line"


class Spec(NamedTuple):
    """One Detail, declared once: what a pane previews and what the fetch behind it serves."""

    # The label key, the pane's `data-detail`, and the column both queries answer under —
    # `f"{name}_chars"` beside it in the header, holding the whole length the link offers.
    name: str
    # Where the whole of it is fetched from, as the route template FastAPI is given: the pane
    # mints its link by filling the same template with the keys of the node it is about.
    route: str
    # The query whose row the pane previews the head out of, and the query that serves the
    # whole under one column named `value`.
    header: Page
    whole: Value
    written: Written


# What a turn's pane previews: what it was asked, and what followed the slash command it ran.
TURN_PROMPT = Spec(
    "prompt",
    "/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}",
    Page.TURN_HEADER,
    Value.TURN_PROMPT,
    Written.MARKDOWN,
)
TURN_COMMAND_ARGS = Spec(
    "command_args",
    "/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}",
    Page.TURN_HEADER,
    Value.TURN_COMMAND_ARGS,
    Written.MARKDOWN,
)
# What an agent run's pane previews: its brief, and the ask and the answer off the call that
# spawned it. All three markdown — one was written by whoever spawned the run, one by the run.
RUN_BRIEF = Spec(
    "brief",
    "/fragment/brief/session/{session_id}/run/{run_id}",
    Page.RUN_HEADER,
    Value.RUN_BRIEF,
    Written.MARKDOWN,
)
RUN_PROMPT = Spec(
    "prompt",
    "/fragment/prompt/session/{session_id}/run/{run_id}",
    Page.RUN_HEADER,
    Value.RUN_PROMPT,
    Written.MARKDOWN,
)
RUN_RESULT = Spec(
    "result",
    "/fragment/result/session/{session_id}/run/{run_id}",
    Page.RUN_HEADER,
    Value.RUN_RESULT,
    Written.MARKDOWN,
)
# What an api call's pane previews: what it said and what it thought, both the model's prose.
CALL_TEXT = Spec(
    "text",
    "/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}",
    Page.CALL_HEADER,
    Value.CALL_TEXT,
    Written.MARKDOWN,
)
CALL_THINKING = Spec(
    "thinking",
    "/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}",
    Page.CALL_HEADER,
    Value.CALL_THINKING,
    Written.MARKDOWN,
)
# And what a tool call's pane previews. The command first, where the call ran one: it is what
# the input is about, and the input below it is the record it was read out of.
TOOL_COMMAND = Spec(
    "command",
    "/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}",
    Page.TOOL_HEADER,
    Value.TOOL_COMMAND,
    Written.BASH,
)
TOOL_INPUT = Spec(
    "input",
    "/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}",
    Page.TOOL_HEADER,
    Value.TOOL_INPUT,
    Written.JSON,
)
TOOL_RESULT = Spec(
    "result",
    "/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}",
    Page.TOOL_HEADER,
    Value.TOOL_RESULT,
    Written.NAMED_FILE,
)
# And the two lines a pass wrote about an item, at each of the three levels it writes at. One
# header query for all six — a page reads what the pass said about everything on it at once —
# and a whole query each, because a fetch serves one value.
TURN_DESCRIPTION = Spec(
    "description",
    "/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}",
    Page.ENRICHMENT,
    Value.TURN_DESCRIPTION,
    Written.LINE,
)
TURN_FRICTION = Spec(
    "friction",
    "/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}",
    Page.ENRICHMENT,
    Value.TURN_FRICTION,
    Written.LINE,
)
RUN_DESCRIPTION = Spec(
    "description",
    "/fragment/description/session/{session_id}/run/{run_id}",
    Page.ENRICHMENT,
    Value.RUN_DESCRIPTION,
    Written.LINE,
)
RUN_FRICTION = Spec(
    "friction",
    "/fragment/friction/session/{session_id}/run/{run_id}",
    Page.ENRICHMENT,
    Value.RUN_FRICTION,
    Written.LINE,
)
SESSION_DESCRIPTION = Spec(
    "description",
    "/fragment/description/session/{session_id}",
    Page.ENRICHMENT,
    Value.SESSION_DESCRIPTION,
    Written.LINE,
)
SESSION_FRICTION = Spec(
    "friction",
    "/fragment/friction/session/{session_id}",
    Page.ENRICHMENT,
    Value.SESSION_FRICTION,
    Written.LINE,
)

# Every Detail the viewer serves, and the only place one is declared. A route that answers a
# whole value and is absent from here is not a Detail: nothing previews a head of it
# (`/fragment/record`, which arrives with a header line of its own).
DETAILS: tuple[Spec, ...] = (
    TURN_PROMPT,
    TURN_COMMAND_ARGS,
    RUN_BRIEF,
    RUN_PROMPT,
    RUN_RESULT,
    CALL_TEXT,
    CALL_THINKING,
    TOOL_COMMAND,
    TOOL_INPUT,
    TOOL_RESULT,
    TURN_DESCRIPTION,
    TURN_FRICTION,
    RUN_DESCRIPTION,
    RUN_FRICTION,
    SESSION_DESCRIPTION,
    SESSION_FRICTION,
)


def syntax_of(written: Written, row: Mapping[str, Any]) -> highlight.Syntax | None:
    """What a value is marked up as, decided once for the preview and the fetch alike.

    None is prose: everything a session or a pass wrote is prose until something says
    otherwise. `NAMED_FILE` is the one arm that asks the row, and it asks by subscript — a
    query that stopped selecting `result_type` is a crash here rather than a value quietly
    marked up as the JSON a suffix-less file falls back to.
    """
    match written:
        case Written.MARKDOWN | Written.LINE:
            return None
        case Written.BASH:
            return highlight.Syntax.BASH
        case Written.JSON:
            return highlight.Syntax.JSON
        case Written.NAMED_FILE:
            return highlight.by_suffix(row["result_type"]) or highlight.Syntax.JSON
        case _:
            assert_never(written)


def preview(spec: Spec, row: Mapping[str, Any], *, size: int, **keys: str) -> Detail | None:
    """One spec's value as its pane shows it, or None where the store holds nothing under it.

    `row` is the header query's row, read under the spec's own name: the head one character
    past `size` — the cut-and-mark protocol `view/text/format.py:cut` reads — and the whole
    length beside it. Nothing is a NULL and an empty string alike: a value with no characters
    in it has no preview to show and nothing to offer the rest of.

    `keys` are the ids of the node the pane is about, which fill the spec's route template to
    mint the link. Passing one the template does not name is harmless; missing one is a
    `KeyError`, which is the URL that would otherwise have been minted wrong.
    """
    head = row[spec.name]
    if not head:
        return None
    # Read rather than defended: a length column is NULL only where the value beside it is,
    # which the line above already left. A header that answers one and not the other is a
    # query that stopped keeping the spec's bargain, and it crashes here.
    chars = row[f"{spec.name}_chars"]
    return Detail(
        name=spec.name,
        head=fmt.cut(head, size),
        cut=chars - size if len(head) > size else 0,
        url=spec.route.format(**keys),
        syntax=syntax_of(spec.written, row),
        markdown=spec.written is Written.MARKDOWN,
    )


def details(*maybe: Detail | None) -> list[Detail]:
    """The details a pane shows: whichever of the columns it asked for the store held."""
    return [item for item in maybe if item is not None]


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
            lines = (TURN_DESCRIPTION, TURN_FRICTION)
            keyed = {"session_id": session_id, "source": source, "turn_id": about.item_id}
        case Level.agent_run:
            lines = (RUN_DESCRIPTION, RUN_FRICTION)
            keyed = {"session_id": session_id, "run_id": about.item_id}
        case Level.session:
            lines = (SESSION_DESCRIPTION, SESSION_FRICTION)
            keyed = {"session_id": about.item_id}
        case _:
            assert_never(about.level)
    # The enrichment reaches here already read, so the row a spec is previewed out of is the
    # tuple itself rather than a second read of `view_enrichment`.
    row = about._asdict()
    return EnrichmentLines(
        description=preview(lines[0], row, size=queries.ENRICHMENT_CHARS, **keyed),
        friction=preview(lines[1], row, size=queries.ENRICHMENT_CHARS, **keyed),
    )
