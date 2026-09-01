"""The popover behind a NavTree row: what the badge and the bar on it stand for.

Three shapes, because three kinds of node are measured three ways. A node made of api calls
has a window and a price; a tool call has neither — its tokens are its api call's — and reports
the size of what it gave back; a compaction has no calls at all and reports the window it
dropped. Each arrives as one element htmx swaps under the row that asked (`docs/viewer.md`).
"""

from collections.abc import Sequence
from typing import NamedTuple

import htpy

from hyphae.view.components import Html, parts
from hyphae.view.pages.node.numbers import Breakout, Charge
from hyphae.view.text import format as fmt


class Window(NamedTuple):
    """A node measured in api calls: where it left the context window, and what it cost."""

    model: str | None
    fill: int | None
    window_tokens: int | None
    added: int | None
    cost_usd: float | None
    api_calls: int | None
    unpriced_api_calls: int | None


class Tool(NamedTuple):
    """A tool call measured in characters: what it was passed, and what it gave back."""

    input_chars: int | None
    result_chars: int | None
    offload_file: str | None
    spawned_run: bool
    siblings: Sequence[str]
    siblings_cut: int


class Compaction(NamedTuple):
    """A compaction measured in the window it dropped: both ends, and the word recorded for why."""

    pre_tokens: int | None
    post_tokens: int | None
    freed: int | None
    trigger: str | None


def popover(
    *,
    key: str,
    citation: str,
    node: Window,
    charges: Sequence[Charge],
    total_wash: str,
    breakout: Breakout | None,
) -> Html:
    """The numbers behind a turn, an api call, an agent run or a session.

    Two columns that each come to a total. The counts are the node's last answering call and
    come to the window above them; the dollars are every call it made and come to the total
    under them. Each dollar carries the badge's own ground, so a share read here and a share
    read on the row are drawn at one depth (`view/numbers.py`).
    """
    return _shell(key=key, citation=citation)[
        [
            htpy.dl(".context")[
                [
                    _line(term="model", body=htpy.dd(data_field="model")[fmt.text(node.model)]),
                    # Where the node left the window, over the window itself. The scale is named
                    # rather than assumed: a session that asked for a larger one still reports
                    # its base model, so a window we hold no number for is said out loud instead
                    # of scaling the counts to a guess (`extract/pricing.py`).
                    _line(
                        term="context used",
                        body=htpy.dd[
                            [
                                htpy.span(data_field="fill")[fmt.count(node.fill)],
                                " / ",
                                htpy.span(data_field="window")[
                                    fmt.count(node.window_tokens)
                                    if node.window_tokens
                                    else "unknown"
                                ],
                            ]
                        ],
                    ),
                ]
            ],
            htpy.dl(".charges")[
                [
                    [_charge(line=line) for line in charges],
                    htpy.div(".sum")[
                        [
                            htpy.dt["total added"],
                            # Signed, always: what a node put into the window is a change, and a
                            # change printed bare reads as a total. A session has nothing before
                            # it to have added to, and prints the dash.
                            htpy.dd(data_field="added")[fmt.signed(node.added)],
                            _cost(field="cost_usd", wash=total_wash, cost=node.cost_usd),
                        ]
                    ],
                    _breakout(breakout=breakout),
                ]
            ],
            # How many calls the dollars cover, where they cover more than the counts above do.
            # Absent at one call, which is every api-call row: `over 1 api call` says nothing a
            # reader asked.
            htpy.p(".beside")[
                [
                    "over ",
                    htpy.span(data_field="api_calls")[fmt.count(node.api_calls)],
                    " api calls",
                ]
            ]
            if node.api_calls and node.api_calls > 1
            else None,
            htpy.p(".beside")[
                [
                    htpy.span(data_field="unpriced_api_calls")[fmt.count(node.unpriced_api_calls)],
                    " at a model our price table lacks",
                ]
            ]
            if node.unpriced_api_calls
            else None,
        ]
    ]


def tool(*, key: str, citation: str, node: Tool) -> Html:
    """The numbers behind one tool call's row: what it returned, and what ran beside it."""
    return _shell(key=key, citation=citation)[
        [
            htpy.dl(".context")[
                [
                    _line(
                        term="asked",
                        body=htpy.dd(data_field="input_chars")[fmt.count(node.input_chars)],
                    ),
                    _line(
                        term="returned",
                        body=htpy.dd(data_field="result_chars")[fmt.count(node.result_chars)],
                    ),
                    _line(
                        term="offloaded to",
                        body=htpy.dd(data_field="offload_file")[node.offload_file],
                    )
                    if node.offload_file
                    else None,
                ]
            ],
            # Where the badge on a ⚒ row comes from. A tool call is billed nothing of its own, so
            # the one tool row that draws a cost draws an attribution rather than a measurement —
            # and a reader who cannot see that reads it as what the tool spent
            # (`view/builders.py:tool_node`).
            htpy.p(".beside", data_attribution="spawn_call")[
                "its own cost is the api call that spawned this run"
            ]
            if node.spawned_run
            else None,
            # What the same api call asked for beside this one, named the way every other surface
            # names a tool call (`view/builders.py:tool_titles`). Parallel work is the reading a
            # row alone cannot give: a call that took a minute took it beside these.
            htpy.p(".beside")[
                [
                    "with ",
                    htpy.span(data_field="siblings")[", ".join(node.siblings)],
                    parts.more(cut=node.siblings_cut),
                ]
                if node.siblings
                else "the only tool call its api call made"
            ],
        ]
    ]


def compaction(*, key: str, citation: str, node: Compaction) -> Html:
    """The numbers behind one compaction's row: both ends of the window, and the span between.

    No window scale here and no price — either would charge the drop with what the calls around
    it did. The bar on the row draws the span alone, which is why both ends are printed: what a
    drop was worth is the two it ran between.
    """
    return _shell(key=key, citation=citation)[
        htpy.dl(".context")[
            [
                _line(
                    term="context before",
                    body=htpy.dd(data_field="pre_tokens")[fmt.count(node.pre_tokens)],
                ),
                _line(
                    term="context after",
                    body=htpy.dd(data_field="post_tokens")[fmt.count(node.post_tokens)],
                ),
                _line(term="freed", body=htpy.dd(data_field="freed")[fmt.count(node.freed)]),
                # What Claude Code recorded as the reason, in its own word: a compaction the
                # model asked for and one the window forced read the same on the row and
                # differently here.
                _line(term="trigger", body=htpy.dd(data_field="trigger")[fmt.text(node.trigger)]),
            ]
        ]
    ]


def _shell(*, key: str, citation: str) -> htpy.Element:
    """The box every popover arrives in, keyed to the row that fetched it.

    `tabindex="-1"` is the copy affordance. The popover is a descendant of the row, so the row
    stays hovered while the pointer is inside it — and a click that lands here focuses it, which
    is what holds it open under `:focus-within` while a reader drags across the numbers.
    """
    return htpy.div(".popover", data_popover=key, data_query=citation, tabindex="-1")


def _line(*, term: str, body: Html) -> Html:
    """One labelled reading of a popover's left column."""
    return htpy.div[[htpy.dt[term], body]]


def _charge(*, line: Charge) -> Html:
    """One category of a cost: what it counted, and what that came to."""
    return htpy.div[
        [
            htpy.dt[line.label],
            htpy.dd(data_field=line.field)[fmt.count(line.tokens)],
            # No dollar at all where our price table lacks the model, rather than a zero: the
            # count beside it is still the store's, and a charge of nothing reads as a
            # measurement.
            _cost(field=line.cost_field, wash=line.wash, cost=line.cost)
            if line.cost is not None
            else None,
        ]
    ]


def _cost(*, field: str, wash: str, cost: float | None) -> Html:
    """One dollar on the ground its share of the session earns it."""
    return htpy.dd(class_=f"badge {wash}", data_field=field)[fmt.charge(cost)]


def _breakout(*, breakout: Breakout | None) -> Html | None:
    """What the agent runs below this node spent, and the two together.

    Drawn only where runs hang there: on every other row the first line would be nothing and the
    second would repeat the one above it (`view/numbers.py`). The share is the subagents' of the
    total, printed where a charge line prints its tokens — it is what a reader is after, and a
    token count for threads they are not reading is not.
    """
    if breakout is None:
        return None
    return htpy.fragment[
        [
            htpy.div(".sum")[
                [
                    htpy.dt["subagent spend"],
                    htpy.dd(data_field="subagent_share")[
                        fmt.share(breakout.subagents, breakout.total)
                    ],
                    _cost(
                        field="cost_subagents",
                        wash=breakout.subagents_wash,
                        cost=breakout.subagents,
                    ),
                ]
            ],
            htpy.div(".sum")[
                [
                    htpy.dt["total spend"],
                    # Empty, and still drawn: the middle column is where the rule over a sum
                    # runs, and a cell left out would break it. There is no share to print —
                    # the total is the whole.
                    htpy.dd,
                    _cost(field="cost_total", wash=breakout.total_wash, cost=breakout.total),
                ]
            ],
        ]
    ]
