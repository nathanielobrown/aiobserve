"""How one store row becomes a node: the builder for each kind, and the helpers they share.

Every surface that names a node — a NavTree row, a crumb, a children log row, a pane — calls
one of these, so the title, the URL and the share a reader sees are the same wherever they
read it. `view/nodes.py` holds the vocabulary they build in.
"""

from collections.abc import Sequence

from hyphae.view.enrichment import Descriptions
from hyphae.view.format import ELLIPSIS
from hyphae.view.formatters import formatted
from hyphae.view.nodes import (
    SPEECH_MARK,
    TALLY_CHARS,
    UNATTACHED_TITLE,
    UNATTRIBUTED_TITLE,
    Context,
    Kind,
    Node,
)
from hyphae.view.store import Row


def _context(row: Row) -> Context | None:
    """Where the row says its node left the window, or None where it says nothing.

    A level of nodes that end on no window leaves the column out, and a node whose model our
    table has no window for answers NULL inside it: both are a bar the NavTree does not draw,
    the way a model we cannot price is a cost it does not print.
    """
    held = row.get("context")
    if held is None or held["fill"] is None or held["window"] is None:
        return None
    return Context(fill=held["fill"], added=held["added"], window=held["window"])


def _share(cost: float | None, whole: float) -> float | None:
    """A node's share of the session's spend, or None when there is no share to speak of."""
    return cost / whole if cost is not None and whole else None


def _words(text: str | None) -> str:
    """What a node is called, whatever the query that composed it left NULL."""
    return text or ""


def session_node(header: Row, described: Descriptions) -> Node:
    """The root of every NavTree: the session everything under it was recorded in."""
    cost = header["cost_usd"] or 0
    return Node(
        kind=Kind.SESSION,
        session_id=header["session_id"],
        source=None,
        node_id=header["session_id"],
        # What the enrichment pass said it was, else the title Claude Code gave it, else the
        # id — which is what a reader pasted to arrive here, so the row is never blank.
        words=_words(
            (described.session.description if described.session else None)
            or header["title"]
            or header["session_id"]
        ),
        cost_usd=cost,
        unpriced_api_calls=header["unpriced_api_calls"],
        share=1.0 if cost else None,
        enriched=described.session is not None,
        context=_context(header),
    )


def turn_node(session_id: str, source: str, row: Row, whole: float, described: str | None) -> Node:
    """One turn as a node, from a NavTree row, a timeline row, or the turn's own header."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.TURN,
        session_id=session_id,
        source=source,
        node_id=row["turn_id"],
        words=_words(described or _turn_title(row)),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
        enriched=described is not None,
        context=_context(row),
    )


def run_node(session_id: str, row: Row, whole: float, described: str | None) -> Node:
    """One agent run as a node, hoisted to wherever its spawning call sits."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.RUN,
        session_id=session_id,
        # A run's id is the source its own rows carry.
        source=row["run_id"],
        node_id=row["run_id"],
        # Which agent ran leads the name wherever no column heads it (`Node.lead`), bracketed
        # so a tree of runs reads as a column of types — and after it what the pass said the
        # run did, else the brief it was given, else nothing.
        lead=f"[{row['agent_type']}]" if row["agent_type"] else "",
        separator=" ",
        words=_words(described or row["brief"]),
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
        enriched=described is not None,
        context=_context(row),
    )


def _tally(names: Sequence[str], chars: int) -> str:
    """How many of each tool an api call invoked, in the order each tool first appears.

    The half of an api call's title that survives every cut (`Node.tail`), so it is bounded
    here rather than by the surface: a group that will not fit is dropped whole and the drop
    marked, because `+2(Ba…` counts calls of a tool the reader cannot name.
    """
    counted: dict[str, int] = {}
    for name in names:
        counted[name] = counted.get(name, 0) + 1
    tallied = ""
    for name, made in counted.items():
        group = f" +{made}({name})"
        if len(tallied) + len(group) > chars:
            return tallied + ELLIPSIS
        tallied += group
    return tallied


def call_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One api call as a node: what it said, else the tools it called, else the model."""
    cost = row["cost_usd"] or 0
    # What the call went on to do, where the query that read it fetched that: the tool names
    # in the order they were called, and the first call's own title. A children log's query
    # fetches neither — its rows are named by the model, and its node is only ever a link.
    tools = row.get("tools") or {}
    names: Sequence[str] = tools.get("names") or ()
    # A call that answered with tool calls and no text has nothing to quote, so it is named
    # by what it did: the tool it called first, that call's title, and a count of the rest.
    # One that neither spoke nor called a tool is named by the model that answered.
    spoken = row.get("text_head")
    silent = not spoken and bool(names)
    return Node(
        kind=Kind.CALL,
        session_id=session_id,
        source=source,
        node_id=row["api_call_id"],
        lead=names[0] if silent else "",
        # Marked where the words are speech, including on a call that also ran tools: what
        # the model said is the one thing on the row nothing else on the page says.
        words=_words(
            tools.get("head") if silent else f"{SPEECH_MARK} {spoken}" if spoken else row["model"]
        ),
        tail=_tally(names[1:], TALLY_CHARS) if silent else "",
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
        context=_context(row),
    )


def tool_node(session_id: str, source: str, row: Row) -> Node:
    """One tool call as a node. No cost of its own: what it took is the api call's."""
    named = formatted(row["name"], row.get("fields") or {})
    return Node(
        kind=Kind.TOOL,
        session_id=session_id,
        source=source,
        node_id=row["tool_call_id"],
        # The tool's name leads, and its title says which call of that tool this is — a page
        # of twenty `Read` rows otherwise says twenty times that a file was read. The title is
        # the query's (`analyze/macros.py`), so the four surfaces that name a tool call agree.
        # Where the tool names its own calls, the glyph stands in for the name and rides in the
        # words rather than the lead: a children log heads its lead in a column of its own, and
        # a mark saying which tool this is has to survive that (`Node.log_title`).
        lead="" if named else row["name"],
        words=f"{named.mark} {named.words}" if named else _words(row.get("title")),
        cost_usd=None,
        unpriced_api_calls=0,
        share=None,
        # Every query a tool node is built from selects it, and the column is NOT NULL, so a
        # row arriving without it is a query that forgot rather than a call that may have
        # failed (`export/duckdb.py`).
        is_error=row["is_error"],
    )


def compaction_node(session_id: str, source: str, row: Row) -> Node:
    """One compaction as a node. A stop on the walk, and a node with no spend of its own."""
    return Node(
        kind=Kind.COMPACTION,
        session_id=session_id,
        source=source,
        node_id=row["compaction_id"],
        words=_words(f"compaction · {row['trigger']}"),
        cost_usd=None,
        unpriced_api_calls=0,
        share=None,
    )


def unattributed_node(session_id: str, source: str, row: Row, whole: float) -> Node:
    """One thread's calls that answer no turn, as the timeline's own cursorless row reads them."""
    cost = row["cost_usd"]
    return Node(
        kind=Kind.UNATTRIBUTED,
        session_id=session_id,
        source=source,
        node_id=source,
        words=UNATTRIBUTED_TITLE,
        cost_usd=cost,
        unpriced_api_calls=row["unpriced_api_calls"],
        share=_share(cost, whole),
    )


def unattached_node(session_id: str, rows: list[Row], whole: float) -> Node:
    """The session's runs no spawning call resolved, gathered under one node.

    Spans every thread rather than sitting on one: what makes a run unattached is that nothing
    says which thread spawned it, so the bucket hangs off the session.
    """
    cost = sum(row["cost_usd"] for row in rows)
    return Node(
        kind=Kind.UNATTACHED,
        session_id=session_id,
        source=None,
        node_id=session_id,
        words=UNATTACHED_TITLE,
        cost_usd=cost,
        unpriced_api_calls=sum(row["unpriced_api_calls"] for row in rows),
        share=_share(cost, whole),
    )


def _turn_title(row: Row) -> str:
    """What to call a turn: the command it ran and what followed, else the prompt as typed.

    The prompt is last because a slash command's prompt is the `<command-…>` wrapper Claude
    Code put around it, which says nothing in the width of a NavTree.
    """
    if row["command_name"] is not None:
        return f"{row['command_name']} {row['command_args'] or ''}".strip()
    # The store declares a turn's prompt NOT NULL (`export/duckdb.py`), so this arm always
    # has something to say, even when what it says is the empty string.
    return row["prompt"]
