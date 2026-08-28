"""How one store row becomes a node: the builder for each kind, and the helpers they share.

Every surface that names a node — a NavTree row, a crumb, a children log row, a pane — calls
one of these, so the title, the URL and the share a reader sees are the same wherever they
read it. `view/nodes.py` holds the vocabulary they build in.
"""

from collections.abc import Sequence

from hyphae.view.enrichment import Descriptions
from hyphae.view.format import ELLIPSIS
from hyphae.view.formatters import Fields, name_tool
from hyphae.view.nodes import (
    COST_PLACES,
    LEAD_SEPARATOR,
    NO_SPEND,
    SPEECH_MARK,
    TALLY_CHARS,
    UNATTACHED_TITLE,
    UNATTRIBUTED_TITLE,
    Context,
    Kind,
    Ledger,
    Node,
    Ref,
    Spend,
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
    return Context(
        fill=held["fill"],
        added=held["added"],
        window=held["window"],
        # Only the query behind a turn returns one: only a turn's own growth is worth reading
        # against the context its session opened on.
        base=held.get("base"),
    )


def _share(cost: float | None, whole: float) -> float | None:
    """A node's share of the session's spend, or None when there is no share to speak of."""
    return cost / whole if cost is not None and whole else None


def _spend(cost: float | None, ref: Ref, held: Ledger) -> Spend:
    """One node's badge: what it cost, and — where runs hang under it — what they cost with it."""
    under = held.below(ref)
    total = round(cost + under, COST_PLACES) if cost is not None and under else None
    return Spend(cost, total, _share(cost, held.whole), _share(total, held.whole))


def _words(text: str | None) -> str:
    """What a node is called, whatever the query that composed it left NULL."""
    return text or ""


def _named(name: str, fields: Fields | None) -> tuple[str, str]:
    """One tool call's lead and words, for the two kinds of node that print a tool's name.

    Where the registry names the tool, its glyph stands in for the name and rides in the words
    rather than the lead: a children log heads its lead in a column of its own, and a mark
    saying which tool this is has to survive that (`Node.log_title`). Where it does not, the
    tool's name leads the shape-driven words instead.
    """
    named = name_tool(name, fields or {})
    return ("", f"{named.mark} {named.words}") if named.mark else (name, named.words)


def tool_titles(called: Sequence[Row]) -> list[str]:
    """A list of tool calls named one at a time, for the surfaces that print them on one line.

    An api call's row in a children log says which tools it called, and a tool call's popover
    says what was asked for beside it. Both are lists of the rows the tools log holds, so both
    are named through `_named` — the lead and the words joined the way `Node.title` joins them,
    because a list of tool calls that read differently from the rows it stands for would be a
    second answer to what a call is called.
    """
    return [
        LEAD_SEPARATOR.join(part for part in _named(one["name"], one["fields"]) if part)
        for one in called
    ]


def tool_about(name: str, fields: Fields | None) -> str:
    """The line under a tool call's title in a children log: what the call was *for*.

    A `Bash` row heads with the command it ran, so the description the caller wrote reads
    underneath it. Empty where the record carried no description, and where the title is
    already that description: a row does not print one value twice.
    """
    held = fields or {}
    said = str(held.get("description") or "")
    return said if said and said not in _named(name, held)[1] else ""


def session_node(header: Row, held: Ledger, described: Descriptions) -> Node:
    """The root of every NavTree: the session everything under it was recorded in.

    The one node whose halves are read the other way round. What a session spent is the whole
    of its subtree — there is nothing above it to gather it — so its own half is that less
    every run under it, which is its main thread.
    """
    whole = header["cost_usd"] or 0
    under = held.below(Ref(Kind.SESSION, None, header["session_id"]))
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
        spend=Spend(
            own=(main := round(whole - under, COST_PLACES)),
            total=whole if under else None,
            share=_share(main, held.whole),
            total_share=1.0 if under and whole else None,
        ),
        unpriced_api_calls=header["unpriced_api_calls"],
        enriched=described.session is not None,
        context=_context(header),
    )


def turn_node(session_id: str, source: str, row: Row, held: Ledger, described: str | None) -> Node:
    """One turn as a node, from a NavTree row, a timeline row, or the turn's own header."""
    return Node(
        kind=Kind.TURN,
        session_id=session_id,
        source=source,
        node_id=row["turn_id"],
        words=_words(described or _turn_title(row)),
        spend=_spend(row["cost_usd"], Ref(Kind.TURN, source, row["turn_id"]), held),
        unpriced_api_calls=row["unpriced_api_calls"],
        enriched=described is not None,
        context=_context(row),
    )


def run_node(session_id: str, row: Row, held: Ledger, described: str | None) -> Node:
    """One agent run as a node, hoisted to wherever its spawning call sits."""
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
        spend=_spend(row["cost_usd"], Ref(Kind.RUN, row["run_id"], row["run_id"]), held),
        unpriced_api_calls=row["unpriced_api_calls"],
        enriched=described is not None,
        context=_context(row),
        # A run that compacted ran its window out, whatever the last call it made says it held.
        maxed=row["compactions"] > 0,
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


def call_node(session_id: str, source: str, row: Row, held: Ledger) -> Node:
    """One api call as a node: what it said, else the tools it called, else the model."""
    cost = row["cost_usd"] or 0
    # What the call went on to do, where the query that read it fetched that: the tool names
    # in the order they were called, and the first call's own title. A children log's query
    # fetches neither — its rows are named by the model, and its node is only ever a link.
    tools = row.get("tools") or {}
    names: Sequence[str] = tools.get("names") or ()
    # A call that answered with tool calls and no text has nothing to quote, so it is named
    # by what it did: the tool it called first, that call's own name, and a count of the rest.
    # One that neither spoke nor called a tool is named by the model that answered.
    spoken = row.get("text_head")
    silent = not spoken and bool(names)
    # Named through the same derivation the tool row under it takes, so the glyph a reader
    # picks a `Read` out of a tree by leads here too (`_named`).
    first = tools.get("first") or {}
    lead, called = _named(first.get("name") or "", first.get("fields")) if silent else ("", "")
    return Node(
        kind=Kind.CALL,
        session_id=session_id,
        source=source,
        node_id=row["api_call_id"],
        lead=lead,
        # Marked where the words are speech, including on a call that also ran tools: what
        # the model said is the one thing on the row nothing else on the page says.
        words=_words(called if silent else f"{SPEECH_MARK} {spoken}" if spoken else row["model"]),
        tail=_tally(names[1:], TALLY_CHARS) if silent else "",
        spend=_spend(cost, Ref(Kind.CALL, source, row["api_call_id"]), held),
        unpriced_api_calls=row["unpriced_api_calls"],
        context=_context(row),
    )


def tool_node(session_id: str, source: str, row: Row, held: Ledger) -> Node:
    """One tool call as a node. No cost of its own: what it took is the api call's.

    Except a ⚒ row, which asked for a run and is charged what the api call holding it cost —
    the nearest thing the store prices to what the reader is looking at. Costless wherever no
    run hangs under it, which is every other tool there is.
    """
    lead, words = _named(row["name"], row.get("fields"))
    # `view_nav_tree_tools.sql` is the one query that reads the call's price, because the
    # NavTree is the one surface that draws the badge. A row that asked for nothing takes
    # neither the price nor the mark saying our table could not complete it.
    asked = held.below(Ref(Kind.TOOL, source, row["tool_call_id"]))
    spent = row.get("call_cost_usd") or 0

    return Node(
        kind=Kind.TOOL,
        session_id=session_id,
        source=source,
        node_id=row["tool_call_id"],
        # The tool's name leads, and its title says which call of that tool this is — a page
        # of twenty `Read` rows otherwise says twenty times that a file was read (`_named`).
        lead=lead,
        words=words,
        spend=_spend(spent if asked else None, Ref(Kind.TOOL, source, row["tool_call_id"]), held),
        unpriced_api_calls=(row.get("unpriced_api_calls") or 0) if asked else 0,
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
        spend=NO_SPEND,
        unpriced_api_calls=0,
        # The one node whose bar reads backwards: what it freed, between the two fills it
        # recorded, against the window of the call its thread made nearest to it.
        context=_context(row),
    )


def unattributed_node(session_id: str, source: str, row: Row, held: Ledger) -> Node:
    """One thread's calls that answer no turn, as the timeline's own cursorless row reads them."""
    return Node(
        kind=Kind.UNATTRIBUTED,
        session_id=session_id,
        source=source,
        node_id=source,
        words=UNATTRIBUTED_TITLE,
        spend=_spend(row["cost_usd"], Ref(Kind.UNATTRIBUTED, source, source), held),
        unpriced_api_calls=row["unpriced_api_calls"],
    )


def unattached_node(session_id: str, rows: list[Row], held: Ledger) -> Node:
    """The session's runs no spawning call resolved, gathered under one node.

    Spans every thread rather than sitting on one: what makes a run unattached is that nothing
    says which thread spawned it, so the bucket hangs off the session. One number and not two:
    its own is already the sum of the runs it gathers, so a subtree half over the same rows
    would say the same thing twice.
    """
    cost = round(sum(row["cost_usd"] for row in rows), COST_PLACES)
    return Node(
        kind=Kind.UNATTACHED,
        session_id=session_id,
        source=None,
        node_id=session_id,
        words=UNATTACHED_TITLE,
        spend=Spend(cost, None, _share(cost, held.whole), None),
        unpriced_api_calls=sum(row["unpriced_api_calls"] for row in rows),
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
