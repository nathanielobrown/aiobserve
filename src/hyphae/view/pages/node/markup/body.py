"""One node as the pane reads it, a shape of facts per kind.

What is here is the node itself: the NavTree around it, the crumbs above it and the log under
it belong to the page, and the whole of a value it only previews is its own fetch. A pane and a
NavTree row that disagreed here would tell a reader two stories about one node.

One body, two mounts — the node's own page wraps it, and `expansion` stands it in a log row of
somebody else's page. The facts a kind reads are a type rather than a store row, so a query
that stopped returning a column is a type error rather than a fact that quietly prints a dash.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import NamedTuple, assert_never
from urllib.parse import quote

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, parts
from hyphae.view.nodes import Node, run_url
from hyphae.view.pages.node.columns import Shape, spanned
from hyphae.view.pages.node.markup.logs import Logged, log
from hyphae.view.text import cuts, render
from hyphae.view.text import format as fmt


class SessionFacts(NamedTuple):
    """The session everything else was recorded in: where it ran, and what it came to.

    Neither the name it was recorded under nor the directory it ran in is here: the heading
    above prints the one and the crumb above that links the other, and a fact is for what
    nothing else on the page says.
    """

    session_id: str
    git_branch: str | None
    version: str | None
    entrypoint: str | None
    started_at: dt.datetime | None
    wall_ms: int | None
    active_ms: int | None
    turns: int
    api_calls: int
    tool_calls: int
    tool_errors: int
    agent_runs: int
    compactions: int
    cost_usd: float | None
    unpriced_api_calls: int
    output_tokens: int
    # The one list among the facts, and the pull requests the session's commands touched. Each
    # grows with the session, so the query cuts it and says how many it left: a pane is the one
    # part of a page no size a reader types bounds.
    skills: Sequence[str]
    skills_cut: int
    pr_urls: Sequence[str]
    pr_urls_cut: int


class TurnFacts(NamedTuple):
    """One turn: what it was asked, when, and what answering it took.

    `command_name` is set where the turn was typed as a slash command — its prompt is the
    `<command-…>` wrapper Claude Code expanded it into, and what a reader is looking for is the
    command.
    """

    turn_id: str
    command_name: str | None
    turn_index: int
    started_at: dt.datetime | None
    replayed: bool
    api_calls: int
    tool_calls: int
    tool_errors: int
    cost_usd: float | None
    unpriced_api_calls: int


class RunFacts(NamedTuple):
    """One agent run: the definition it ran, where it was spawned, and what its thread came to."""

    run_id: str
    agent_type: str | None
    model: str | None
    spawn_depth: int
    is_fork: bool
    started_at: dt.datetime | None
    wall_ms: int | None
    turns: int
    api_calls: int
    tool_calls: int
    tool_errors: int
    compactions: int
    cost_usd: float | None
    unpriced_api_calls: int
    output_tokens: int


class CallFacts(NamedTuple):
    """One api call: the request that was made, and what came back."""

    call_index: int
    model: str | None
    fallback_from: str | None
    effort: str | None
    stop_reason: str | None
    attribution_skill: str | None
    started_at: dt.datetime | None
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float | None
    unpriced_api_calls: int


class ToolFacts(NamedTuple):
    """One tool call. No cost of its own: what it took is the api call's.

    `run_id` is set on a `Task` call, which is where an agent run begins; `offload_file` where
    the result was too large for the transcript and Claude Code wrote it beside one.
    """

    session_id: str
    run_id: str | None
    tool_index: int
    name: str | None
    server_side: bool
    is_error: bool
    incomplete: bool
    started_at: dt.datetime | None
    wall_ms: int | None
    offload_file: str | None


class CompactionFacts(NamedTuple):
    """One compaction: where the thread's context was rewritten, and what it cost in tokens."""

    trigger: str | None
    timestamp: dt.datetime | None
    pre_tokens: int | None
    post_tokens: int | None
    duration_ms: int | None


class BucketFacts(NamedTuple):
    """A bucket, which is not a row of the store.

    It stands for what attached to nothing, so it has a spend and a count and no fields of its
    own — both of them read off the node rather than off a query.
    """

    cost_usd: float | None
    unpriced_api_calls: int


# What a body may be handed. Total over the kinds a URL can name — a kind with no member would
# render a heading and nothing under it, which reads as a node with no facts.
type Facts = (
    SessionFacts | TurnFacts | RunFacts | CallFacts | ToolFacts | CompactionFacts | BucketFacts
)


def body(*, node: Node, facts: Facts, suffix: str) -> Html:
    """One node's title and the facts its kind reads, for either mount."""
    return htpy.section(".body", data_body=node.kind)[
        [
            htpy.h1[
                [
                    parts.mark(character=node.icon),
                    " ",
                    htpy.span(data_field="title")[node.pane_title],
                ]
            ],
            _facts(facts=facts, suffix=suffix),
        ]
    ]


def expansion(
    *,
    node: Node,
    facts: Facts,
    suffix: str,
    shape: Shape,
    children: int | None,
    rows: Sequence[Logged],
    citations: Mapping[str, Cited],
) -> Html:
    """One node's body alone, for a log row on somebody else's page.

    The same section a node page wraps, with the way to the node's own page where the page has
    the NavTree and the crumbs. A call lists the tools it called under its facts, through the
    log the page itself renders; every other kind stands a count and a link in the list's
    place. Either way the nesting stops here — an expansion that opened an expansion is an
    accordion of accordions, and the node already has a page.

    A row of the log's own table, swapped in after the row that asked for it, spanning every
    column that row fills: the parent's shape is not in the URL, so the span comes from the
    kind of node this is — `columns.spanned` maps it back to the log that lists it.
    """
    return htpy.tr(".expansion", data_expansion=node.kind)[
        htpy.td(colspan=spanned(node.kind))[
            [
                body(node=node, facts=facts, suffix=suffix),
                log(
                    shape=shape,
                    rows=rows,
                    total=children,
                    suffix=suffix,
                    pager=None,
                    opens=False,
                )
                if rows
                else None,
                _way_out(node=node, suffix=suffix, shape=shape, children=children, rows=rows),
                # What the fragment ran, beside what it rendered — the same provenance a page's
                # footer carries, on the element that was swapped in.
                citation.listed(citations=citations),
            ]
        ]
    ]


def _way_out(
    *, node: Node, suffix: str, shape: Shape, children: int | None, rows: Sequence[Logged]
) -> Html:
    """The link out of an expansion.

    It carries the count wherever nothing above it listed the level; where the log did, the
    log's own heading counts it and the link says the one thing left to say.
    """
    counted = children is not None and not rows
    return htpy.p(".children", data_children=node.kind)[
        htpy.a(href=f"{node.url}{suffix}")[
            [htpy.span(data_field="children")[fmt.count(children)], f" {shape}"]
            if counted
            else "its own page"
        ]
    ]


def _facts(*, facts: Facts, suffix: str) -> Html:
    """The body's one dispatch: the facts of whichever kind of node this is."""
    match facts:
        case SessionFacts():
            return _session(facts=facts)
        case TurnFacts():
            return _turn(facts=facts)
        case RunFacts():
            return _run(facts=facts)
        case CallFacts():
            return _call(facts=facts)
        case ToolFacts():
            return _tool(facts=facts, suffix=suffix)
        case CompactionFacts():
            return _compaction(facts=facts)
        case BucketFacts():
            return _bucket(facts=facts)
        case _:
            assert_never(facts)


def _fact(name: str, value: str | None) -> Html:
    """One fact cut at the pane's width, which is what every fact but the composed one wants."""
    return parts.fact(name=name, value=value, cut=True)


def _session(*, facts: SessionFacts) -> Html:
    return htpy.fragment[
        [
            htpy.dl(".facts")[
                [
                    _fact("session_id", facts.session_id),
                    _fact("git_branch", facts.git_branch),
                    _fact("version", facts.version),
                    _fact("entrypoint", facts.entrypoint),
                    _fact("started_at", fmt.when(facts.started_at)),
                    _fact("wall_ms", fmt.duration(facts.wall_ms)),
                    _fact("active_ms", fmt.duration(facts.active_ms)),
                    _fact("turns", fmt.count(facts.turns)),
                    _fact("api_calls", fmt.count(facts.api_calls)),
                    _fact("tool_calls", fmt.count(facts.tool_calls)),
                    _fact("tool_errors", fmt.count(facts.tool_errors)),
                    _fact("agent_runs", fmt.count(facts.agent_runs)),
                    _fact("compactions", fmt.count(facts.compactions)),
                    _fact("cost_usd", fmt.money(facts.cost_usd)),
                    # Beside the cost rather than folded into it: a total missing calls our
                    # price table could not price is not what the node cost.
                    _fact("unpriced_api_calls", fmt.count(facts.unpriced_api_calls)),
                    _fact("output_tokens", fmt.count(facts.output_tokens)),
                    _skills(facts=facts),
                ]
            ],
            _prs(facts=facts),
        ]
    ]


def _skills(*, facts: SessionFacts) -> Html:
    """The skills the session loaded, and the count of what its query left behind.

    Composed rather than printed, so the pane's own cut cannot take the count off the end of
    it: a list already bounded by its query loses what it left rather than a tail of its last
    member.
    """
    if not facts.skills:
        return _fact("skills", None)
    return parts.labelled(
        name="skills",
        value=htpy.span[
            [
                ", ".join(cuts.member(skill) for skill in facts.skills),
                parts.more(cut=facts.skills_cut),
            ]
        ],
    )


def _prs(*, facts: SessionFacts) -> Html | None:
    """The pull requests the session's commands touched, cut the way the skills are.

    Links rather than a fact row, because a reader follows them off the page.
    """
    if not facts.pr_urls:
        return None
    shown = [cuts.member(url) for url in facts.pr_urls]
    return htpy.ul(".prs")[
        [
            [htpy.li(data_pr=url)[render.link(url)] for url in shown],
            htpy.li(data_field="prs_cut")[f"and {fmt.count(facts.pr_urls_cut)} more"]
            if facts.pr_urls_cut
            else None,
        ]
    ]


def _turn(*, facts: TurnFacts) -> Html:
    return htpy.fragment[
        [
            htpy.p(".command", data_command=facts.turn_id)[
                htpy.span(data_field="command_name")[cuts.head(facts.command_name)]
            ]
            if facts.command_name is not None
            else None,
            htpy.dl(".facts")[
                [
                    _fact("turn_index", fmt.count(facts.turn_index)),
                    _fact("started_at", fmt.clock(facts.started_at)),
                    _fact("replayed", fmt.flag(facts.replayed)),
                    _fact("api_calls", fmt.count(facts.api_calls)),
                    _fact("tool_calls", fmt.count(facts.tool_calls)),
                    _fact("tool_errors", fmt.count(facts.tool_errors)),
                    _fact("cost_usd", fmt.money(facts.cost_usd)),
                    _fact("unpriced_api_calls", fmt.count(facts.unpriced_api_calls)),
                ]
            ],
        ]
    ]


def _run(*, facts: RunFacts) -> Html:
    return htpy.dl(".facts")[
        [
            _fact("run_id", facts.run_id),
            _fact("agent_type", facts.agent_type),
            _fact("model", facts.model),
            _fact("spawn_depth", fmt.count(facts.spawn_depth)),
            _fact("is_fork", fmt.flag(facts.is_fork)),
            _fact("started_at", fmt.clock(facts.started_at)),
            _fact("wall_ms", fmt.duration(facts.wall_ms)),
            _fact("turns", fmt.count(facts.turns)),
            _fact("api_calls", fmt.count(facts.api_calls)),
            _fact("tool_calls", fmt.count(facts.tool_calls)),
            _fact("tool_errors", fmt.count(facts.tool_errors)),
            _fact("compactions", fmt.count(facts.compactions)),
            _fact("cost_usd", fmt.money(facts.cost_usd)),
            _fact("unpriced_api_calls", fmt.count(facts.unpriced_api_calls)),
            _fact("output_tokens", fmt.count(facts.output_tokens)),
        ]
    ]


def _call(*, facts: CallFacts) -> Html:
    return htpy.dl(".facts")[
        [
            _fact("call_index", fmt.count(facts.call_index)),
            _fact("model", facts.model),
            _fact("fallback_from", facts.fallback_from) if facts.fallback_from else None,
            _fact("effort", facts.effort),
            _fact("stop_reason", facts.stop_reason),
            _fact("attribution_skill", facts.attribution_skill),
            _fact("started_at", fmt.clock(facts.started_at)),
            _fact("tool_calls", fmt.count(facts.tool_calls)),
            _fact("input_tokens", fmt.count(facts.input_tokens)),
            _fact("output_tokens", fmt.count(facts.output_tokens)),
            _fact("cache_read_tokens", fmt.count(facts.cache_read_tokens)),
            _fact("cache_creation_tokens", fmt.count(facts.cache_creation_tokens)),
            _fact("cost_usd", fmt.money(facts.cost_usd)),
            _fact("unpriced_api_calls", fmt.count(facts.unpriced_api_calls)),
        ]
    ]


def _tool(*, facts: ToolFacts, suffix: str) -> Html:
    return htpy.fragment[
        [
            # A `Task` call is where an agent run begins, so the run leads the body: it is what
            # a reader came to the call to reach, and everything else about the call is what it
            # took to start it.
            htpy.p(".spawned", data_spawned=facts.run_id)[
                htpy.a(href=f"{run_url(facts.session_id, facts.run_id)}{suffix}")[
                    "the run it started"
                ]
            ]
            if facts.run_id
            else None,
            htpy.dl(".facts")[
                [
                    _fact("tool_index", fmt.count(facts.tool_index)),
                    _fact("name", facts.name),
                    _fact("server_side", fmt.flag(facts.server_side)),
                    _fact("is_error", fmt.flag(facts.is_error)),
                    _fact("incomplete", fmt.flag(facts.incomplete)),
                    _fact("started_at", fmt.clock(facts.started_at)),
                    _fact("wall_ms", fmt.duration(facts.wall_ms)),
                ]
            ],
            # A result too large for the transcript was written to a file beside it, and that
            # file has a page of its own — so the pane says where it went rather than showing
            # an empty result.
            htpy.p(".offload")[
                [
                    "result offloaded to ",
                    htpy.a(
                        data_field="offload_file",
                        href=f"/session/{facts.session_id}/offload/{quote(facts.offload_file)}",
                    )[facts.offload_file],
                ]
            ]
            if facts.offload_file
            else None,
        ]
    ]


def _compaction(*, facts: CompactionFacts) -> Html:
    return htpy.dl(".facts")[
        [
            _fact("trigger", facts.trigger),
            _fact("timestamp", fmt.clock(facts.timestamp)),
            _fact("pre_tokens", fmt.count(facts.pre_tokens)),
            _fact("post_tokens", fmt.count(facts.post_tokens)),
            _fact("duration_ms", fmt.duration(facts.duration_ms)),
        ]
    ]


def _bucket(*, facts: BucketFacts) -> Html:
    return htpy.dl(".facts")[
        [
            _fact("cost_usd", fmt.money(facts.cost_usd)),
            _fact("unpriced_api_calls", fmt.count(facts.unpriced_api_calls)),
        ]
    ]
