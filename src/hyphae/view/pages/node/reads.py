"""One store row read into the shape the node page's markup takes: facts, a log row, numbers.

The seam the markup sits behind. A `Row` is whatever columns the query returned; past here a
body, a log row or a popover reads named fields of a type it declares itself, so a query that
dropped a column raises here rather than printing a dash under a label
(`view/pages/node/markup/`).

The node itself is built one layer down (`view/builders.py`): what a row is *called* is the
same wherever it is read, and only what a row is *shown as* is this page's own.
"""

from typing import NamedTuple

from hyphae.extract.pricing import TokenUsage
from hyphae.view.builders import tool_about, tool_titles
from hyphae.view.nodes import Kind, Node
from hyphae.view.pages.node.columns import Shape
from hyphae.view.pages.node.markup import body as node_body
from hyphae.view.pages.node.markup import logs, numbers, values
from hyphae.view.pages.node.numbers import Numbers
from hyphae.view.store import Row


def _read[F: NamedTuple](facts: type[F], row: Row) -> F:
    """One facts type filled from the columns its own fields name.

    The field names *are* the column names, which is what keeps a facts type and its header
    query in step: a query that dropped a column raises here, naming it, rather than reaching
    a body that would print a dash under its label.
    """
    return facts._make(row[field] for field in facts._fields)


def node_facts(node: Node, row: Row) -> node_body.Facts:
    """The facts a node's body prints, read off the row its header query answered.

    Where a store row stops being a bag of columns: past here a body reads named fields of a
    type. Total over `Kind`, the two buckets sharing a shape because neither is a row of the
    store — what they hold is counted on the node itself.
    """
    match node.kind:
        case Kind.SESSION:
            return _read(node_body.SessionFacts, row)
        case Kind.TURN:
            return _read(node_body.TurnFacts, row)
        case Kind.RUN:
            return _read(node_body.RunFacts, row)
        case Kind.CALL:
            return _read(node_body.CallFacts, row)
        case Kind.TOOL:
            return _read(node_body.ToolFacts, row)
        case Kind.COMPACTION:
            return _read(node_body.CompactionFacts, row)
        case Kind.UNATTRIBUTED | Kind.UNATTACHED:
            # The one kind not read off a row: a bucket stands for no store row, so its two
            # numbers are the ones the node already carries from counting its children.
            return node_body.BucketFacts(
                cost_usd=node.cost_usd, unpriced_api_calls=node.unpriced_api_calls
            )


def logged(shape: Shape, node: Node, row: Row) -> logs.Logged:
    """One row of a children log: the node its wide column links to, beside what the row prints.

    Keyed by the log's shape rather than the node's kind, because the shape is what decides the
    columns the row has to fill. `Shape.NONE` lists nothing, so it has no row to build.
    """
    match shape:
        case Shape.TURNS:
            return logs.LoggedTurn(
                node=node,
                turn_index=row["turn_index"],
                api_calls=row["api_calls"],
                tool_calls=row["tool_calls"],
                started_at=row["started_at"],
            )
        case Shape.CALLS:
            return logs.LoggedCall(
                node=node,
                call_index=row["call_index"],
                model=row["model"],
                text=row["text"],
                tool_calls=row["tool_calls"],
                # The words rather than the rows: naming a tool call is Python's
                # (`view/text/tool_names.py`), so the query ships the fields and this composes them.
                called=", ".join(tool_titles(row.get("called_tools") or ())),
                text_chars=row["text_chars"],
                started_at=row["started_at"],
            )
        case Shape.TOOLS:
            return logs.LoggedTool(
                node=node,
                tool_index=row["tool_index"],
                name=row["name"],
                about=tool_about(row.get("name") or "", row.get("fields")),
                is_error=row["is_error"],
                result_chars=row["result_chars"],
                started_at=row["started_at"],
            )
        case Shape.RUNS:
            return logs.LoggedRun(
                node=node,
                agent_type=row["agent_type"],
                tool_errors=row["tool_errors"],
                started_at=row["started_at"],
            )
        case Shape.NONE:
            raise ValueError("A log of no shape lists no rows.")


def node_numbers(row: Row) -> Numbers:
    """A popover's readings for a node made of api calls, off the row `view_numbers` answered.

    The whole row at once — the window the component prints, the counts and dollars the
    charge lines are composed from, and the per-model groups they are priced at — so the
    route past here reads named fields rather than indexing the row six more times.
    """
    return Numbers(
        window=numbers.Window(
            model=row["model"],
            fill=row["fill"],
            window_tokens=row["window_tokens"],
            added=row["added"],
            cost_usd=row["cost_usd"],
            api_calls=row["api_calls"],
            unpriced_api_calls=row["unpriced_api_calls"],
        ),
        cache_read_tokens=row["cache_read_tokens"],
        new_input_tokens=row["new_input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        subtree_usd=row["subtree_usd"],
        session_usd=row["session_usd"],
        spent=tuple((group["model"], _usage(group)) for group in row["spent"]),
    )


def _usage(group: Row) -> TokenUsage:
    """One model's summed tokens as the price table takes them.

    The TTL split is summed per call in SQL, under the same fallback `pricing.py` applies to
    one — a call that reported no split puts its whole write on the 5-minute rate — so the
    group carries a split whether or not every call in it did.
    """
    return TokenUsage(
        input=group["input_tokens"],
        output=group["output_tokens"],
        cache_read=group["cache_read_tokens"],
        cache_creation=group["cache_creation_tokens"],
        cache_5m=group["cache_5m_tokens"],
        cache_1h=group["cache_1h_tokens"],
    )


def tool_numbers(row: Row) -> numbers.Tool:
    """A popover's readings for one tool call, off the row `view_numbers_tool` answered.

    The siblings are named here rather than in the query: what a tool call is called is
    Python's (`view/text/tool_names.py`), and the query ships the fields each name is composed of.
    """
    return numbers.Tool(
        input_chars=row["input_chars"],
        result_chars=row["result_chars"],
        offload_file=row["offload_file"],
        spawned_run=row["spawned_run"],
        siblings=tool_titles(row["siblings"]),
        siblings_cut=row["siblings_cut"],
    )


def compaction_numbers(row: Row) -> numbers.Compaction:
    """A popover's readings for one compaction, off `view_numbers_compaction`'s row."""
    return numbers.Compaction(
        pre_tokens=row["pre_tokens"],
        post_tokens=row["post_tokens"],
        freed=row["freed"],
        trigger=row["trigger"],
    )


def record_value(row: Row, citation: str) -> values.Record:
    """One archived record as its fragment prints it, off `Value.RECORD`'s row."""
    return values.Record(
        line_no=row["line_no"],
        type=row["type"],
        uuid=row["uuid"],
        timestamp=row["timestamp"],
        raw_chars=row["raw_chars"],
        raw=row["raw"],
        citation=citation,
    )
