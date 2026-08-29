"""The small pieces a page is built out of, each printed the one way the viewer prints it."""

from collections.abc import Sequence
from typing import NamedTuple

import htpy

from hyphae.view import bounds, cuts, highlight, render
from hyphae.view import format as fmt
from hyphae.view.components import Html
from hyphae.view.detail import Detail, EnrichmentLines
from hyphae.view.enrichment import GLYPH, GLYPH_CLASS, Enrichment
from hyphae.view.labels import label


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


class Count(NamedTuple):
    """One name a session's row counts, and how often it counted it.

    Built at the route from whichever column the query counted — runs for an agent type, turns
    for a kind of work — so the component prints a count without knowing what was counted.
    """

    name: str
    count: int


def stacked(
    *,
    field: str,
    primary: str,
    secondary_field: str,
    secondary: str,
    unit: str | None,
    primary_mark: Html | None,
    secondary_mark: Html | None,
) -> Html:
    """One two-line cell: the value a reader scans a column for, and the texture under it.

    Both halves are labelled, so a test reads either without matching prose, and the unit word
    sits outside the labelled span — what a reader sees is a number and a word, what a
    `data-field` carries is the value the store holds.

    A mark hangs off whichever line owns what it qualifies, which is why there are two slots:
    the session list stacks output tokens under a cost and marks the cost, the projects landing
    stacks a cost under a count and marks the cost again.
    """
    return htpy.fragment[
        [
            htpy.span(data_field=field, class_="primary")[primary],
            primary_mark,
            htpy.span(".secondary")[
                [
                    htpy.span(data_field=secondary_field)[secondary],
                    secondary_mark,
                    # The space before the unit is a child of its own: htpy emits none between
                    # elements, and this one is the difference between `0 errors` and `0errors`.
                    [" ", unit] if unit else None,
                ]
            ],
        ]
    ]


def unpriced(*, calls: int | None) -> Html | None:
    """The mark a cost carries when our price table priced none of some calls under it.

    A total missing calls is not what was spent, and the page has to say so. Outside the
    labelled span either way — a `data-field` carries the number the store holds and nothing
    else — and written once because both pages that print a cost hang it off one. No calls and
    no count are the same mark: a window the store summed nothing over priced nothing wrong.
    """
    if not calls:
        return None
    return htpy.sup(title=f"{fmt.count(calls)} call(s) at a model our price table lacks")["*"]


def badge(*, step: str, field: str, value: float) -> Html:
    """One half of a NavTree row's cost: the number, and the step standing for its share.

    Written once because a row with agent runs under it draws two of them, its own spend and
    its subtree's, and each takes the step its own share earns. This is the markup the ceiling
    in `view/bounds.py` is measured over.
    """
    return htpy.span(class_=f"badge {step}", data_field=field)[fmt.money(value)]


def mark(*, character: str) -> Html:
    """The mark saying what a thing is: what kind of node a row, a crumb or a heading names.

    `aria-hidden` and no `title`: the mark stands for a word the markup around it already
    carries — the NavTree row's class, the crumb's field name, the pane's `data-body`, the
    column's head — so a reader who cannot see it loses nothing, and a `title` here would be
    the same word 3,217 times in one page. Written with no space of its own: every caller puts
    one after it, and a byte here is 3,217 bytes of page (`view/bounds.py`).
    """
    return htpy.span(".icon", aria_hidden="true")[character]


def glyph(*, enriched: bool) -> Html | None:
    """The mark on a title a model helped write, and the space after it.

    Bare wherever a title repeats — a NavTree row, a crumb, a log line, a walk control —
    because what a reader wants beside a mark is on the pane, once, hanging off the paragraph
    the pass wrote. The space is inside the test: a row with no mark owes no space.
    """
    if not enriched:
        return None
    return htpy.fragment[[htpy.span(class_=GLYPH_CLASS)[GLYPH], " "]]


def counted(*, entries: Sequence[Count], mark_cuts: bool) -> Html:
    """A counted list of names — the agent types a session spawned, the kinds of work it did.

    Every integer goes through `fmt.count`, like every other one a page prints, and every name
    through `cuts.item`, which marks the ones the query stopped. `mark_cuts` is how a caller
    opts out, for a list whose vocabulary is closed: a taxonomy value is cut at a width its own
    words cannot reach (`analyze/queries.py:TAG_CHARS`), so a mark on one would say a name went
    on when nothing was left behind.
    """
    return htpy.fragment[
        [
            [
                ", " if at else None,
                cuts.item(entry.name) if mark_cuts else entry.name,
                f" ×{fmt.count(entry.count)}",
            ]
            for at, entry in enumerate(entries)
        ]
    ]


def more(*, cut: int) -> Html | None:
    """What a cut list left out, in the one wording every list on a row uses."""
    return htpy.fragment[f" and {fmt.count(cut)} more"] if cut else None


def tags(*, category: str, outcome: str, stale: bool) -> Html:
    """What an enrichment pass said an item was and how it went.

    The vocabularies are closed (`enrich/taxonomy.py`); `stale` says the row was written under
    a prompt or taxonomy version this build has moved past, which is a reason to re-run a pass
    and not a reason to distrust the words.
    """
    return htpy.fragment[
        [
            htpy.span(".tag", data_field="category")[category],
            htpy.span(".tag", data_field="outcome")[outcome],
            htpy.span(".tag.stale", data_field="stale")["stale"] if stale else None,
        ]
    ]


def summary(*, enrichment: Enrichment, lines: EnrichmentLines) -> Html:
    """What a pass said about the item whose page this is, beside the header counting what it did.

    Model-written from a private transcript, so it is text like any other the viewer renders.
    """
    return htpy.section(".enrichment", data_enrichment=enrichment.item_id)[
        [
            htpy.p[
                [
                    htpy.span(
                        class_=GLYPH_CLASS,
                        data_field="enriched",
                        title=enrichment.provenance,
                    )[GLYPH],
                    " ",
                    enrichment_line(item=lines.description),
                ]
            ],
            htpy.p(".tags")[
                tags(
                    category=enrichment.category, outcome=enrichment.outcome, stale=enrichment.stale
                )
            ],
            htpy.p(".friction")[enrichment_line(item=lines.friction)] if lines.friction else None,
        ]
    ]


def enrichment_line(*, item: Detail | None) -> Html | None:
    """One line a pass wrote as the pane shows it: the head, and the fetch that brings the rest.

    A pass answers in paragraphs, so nearly every line it writes about a run runs past the
    width — the mark alone would say there is more and offer no way to it.

    The link sits outside the labelled span and inside the block it swaps, which is what makes
    `closest .enrichment-line` land: the glyph and the provenance hanging off it stay put.
    """
    if item is None:
        return None
    return htpy.span(".enrichment-line", data_enrichment_line=item.name)[
        [
            htpy.span(data_field=item.name)[item.head],
            [" ", _whole(item=item, target="closest .enrichment-line", classes=".more")]
            if item.cut
            else None,
        ]
    ]


def _whole(*, item: Detail, target: str, classes: str) -> Html:
    """The link that fetches the rest of a cut value into the block its head stood in."""
    return htpy.a(
        classes,
        data_whole=item.name,
        href=item.url,
        hx_get=item.url,
        hx_target=target,
        hx_swap="outerHTML",
    )[["+", htpy.span(data_field="cut")[fmt.count(item.cut)], " more character(s)"]]


def prose(*, field: str, value: str | None) -> Html:
    """One value as the markdown a session wrote it in.

    Rendered rather than printed because that is what it is: a prompt, a model's answer and the
    brief a run was given are written in markdown by whoever typed them. `view/render.py` owns
    the escaping — html passthrough off, an image a placeholder, a link only where a browser
    should follow it — and nothing here may hand a value on that did not come through it.

    Written once for the two mounts that show one value: the head a pane previews, and the
    whole of it the fetch swaps into that same block.
    """
    return htpy.div(".prose", data_field=field)[render.markdown(value)]


def fact(*, name: str, value: object, cut: bool) -> Html:
    """One labelled fact of a header.

    Every value goes through `fmt.text`, so a column the store left NULL prints the dash the
    rest of the viewer prints rather than Python's `None`. And through `cuts.head`, which is
    the pane's half of the one-extra-character protocol.

    `cut` is how a caller opts out, for a value this width would mean something else to: a
    joined list already bounded by its query loses the count of what it left rather than a tail
    of its last member.
    """
    printed = cuts.head(value) if cut else fmt.text(value if isinstance(value, str) else None)
    return _pair(name, printed)


def labelled(*, name: str, value: Html) -> Html:
    """One labelled fact whose value the caller composed, for the ones no formatter makes.

    A list and the count of what its query cut, today. The `<dl>` shape is `fact`'s, so one
    place decides what a labelled fact looks like whichever of the two wrote it.
    """
    return _pair(name, value)


def _pair(name: str, value: object) -> Html:
    """The `<dt>`/`<dd>` pair both mounts write, with the space a reader needs between them.

    htpy writes nothing between two elements, so without the `" "` a reader whose stylesheet
    never arrived meets `Cost$1.48` (`tests/view/test_app__headers.py`).
    """
    return htpy.div[[htpy.dt[label(name)], " ", htpy.dd(data_field=name)[value]]]


def detail(*, item: Detail) -> Html:
    """One of a node's own values as the pane shows it: the head, and the way to the rest.

    The link fetches the whole value and replaces this block, which is the one place a fat
    column crosses the wire whole (`view/store.py`'s per-value queries). A head whose row said
    what it was written in is marked up in that syntax; the rest is prose.

    Prose is walled as the quotation it is — someone's words inside our page — and a payload is
    not: a border on every value would say "this is a value" rather than "somebody wrote this".
    The same flag decides both, so a value cannot render as markdown and read as a payload.
    """
    if item.syntax:
        shown = code(value=item.head, syntax=item.syntax, field=item.name)
    elif item.markdown:
        shown = prose(field=item.name, value=item.head)
    else:
        shown = htpy.pre(data_field=item.name)[item.head]
    return htpy.section(
        class_="detail quoted" if item.markdown else "detail", data_detail=item.name
    )[
        [
            htpy.h3[label(item.name)],
            shown,
            htpy.p(".more")[_whole(item=item, target="closest .detail", classes="")]
            if item.cut
            else None,
        ]
    ]
