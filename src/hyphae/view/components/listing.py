"""The store's two lists: the projects landing, and the session list under its filter form.

Both are tables of one row per thing, so both are built the same way — a typed row in, a `<tr>`
out. What each row prints is what its type carries; the links they mint are `view/listing.py`'s,
and the SQL behind them `view/store.py`'s.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import htpy

from hyphae.view.citation import Cited
from hyphae.view.components import Html, citation, layout, parts
from hyphae.view.nodes import LIST_URL
from hyphae.view.text import cuts
from hyphae.view.text import format as fmt


class Control(NamedTuple):
    """One filter as the form renders it."""

    key: str
    # The HTML input type the filter's parameter type earns.
    type: str
    # What this request asked for, so the form comes back holding what was typed into it.
    value: str


class ProjectRow(NamedTuple):
    """One project as the landing page prints it: three windows of spend, and when it last ran.

    `link` is the session list narrowed to this project, or None where there is no list to
    open — a session that named no directory, or a path longer than the head the page shows.
    A window the project has no sessions in sums nothing, so its cost and its unpriced count
    are absent rather than zero.
    """

    project_dir: str | None
    link: str | None
    recent_sessions: int
    recent_cost: float | None
    recent_unpriced: int | None
    window_sessions: int
    window_cost: float | None
    window_unpriced: int | None
    sessions: int
    cost_usd: float
    unpriced_api_calls: int
    last_active: dt.datetime | None


def projects_page(
    *,
    rows: Sequence[ProjectRow],
    recent_days: int,
    window_days: int,
    cut: int,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """Every project the store holds sessions for, most recently active first.

    The two trailing windows are headed with the days they were bound to, so a heading cannot
    say one thing while the column counts another — the footer cites the same numbers.
    """
    return layout.page(
        tab_title="Projects — hyphae",
        scripts=None,
        main=htpy.fragment[
            [
                htpy.h1["Projects"],
                htpy.table(id="projects")[
                    [
                        htpy.thead[
                            htpy.tr[
                                [
                                    htpy.th(scope="col")["Project"],
                                    # Set the way the cells under them are: a count is read
                                    # down its column.
                                    htpy.th(".number", scope="col")[f"{recent_days}d"],
                                    htpy.th(".number", scope="col")[f"{window_days}d"],
                                    htpy.th(".number", scope="col")["All time"],
                                    htpy.th(scope="col")["Last active"],
                                ]
                            ]
                        ],
                        htpy.tbody[[_project(row=row) for row in rows]],
                    ]
                ],
                # What the page left out, said rather than dropped: the store keeps every
                # project, and this one shows the most recently active.
                htpy.p(".more", data_more_projects=cut)[f"+{fmt.count(cut)} more project(s)"]
                if cut
                else None,
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )


def _project(*, row: ProjectRow) -> Html:
    """One project's row: where its sessions read, its three windows, and its last activity."""
    return htpy.tr(data_project=row.project_dir or "")[
        [
            htpy.td(".path")[_project_name(row=row)],
            _window(
                sessions_field="recent_sessions",
                sessions=row.recent_sessions,
                cost_field="recent_cost",
                cost=row.recent_cost,
                unpriced=row.recent_unpriced,
            ),
            _window(
                sessions_field="window_sessions",
                sessions=row.window_sessions,
                cost_field="window_cost",
                cost=row.window_cost,
                unpriced=row.window_unpriced,
            ),
            _window(
                sessions_field="sessions",
                sessions=row.sessions,
                cost_field="cost_usd",
                cost=row.cost_usd,
                unpriced=row.unpriced_api_calls,
            ),
            htpy.td(".when")[
                parts.stacked(
                    field="ago",
                    primary=cuts.ago(row.last_active),
                    secondary_field="last_active",
                    secondary=fmt.when(row.last_active),
                    unit=None,
                    primary_mark=None,
                    secondary_mark=None,
                )
            ],
        ]
    ]


def _project_name(*, row: ProjectRow) -> Html:
    """The path, as a link where there is a list to open and as text where there is not.

    The link filters the list by the whole path, which is why a row whose path is longer than
    the head this page shows is text: a link carrying a cut path lands on nothing. The sessions
    naming no directory are text for a different reason — there is no project to open, and
    `?project=` matches no session at all.

    Cut like a session-list row's path, and for the same reason: the query hands back one
    character past the width, and a path too long to link is the one a reader most needs the
    mark on — the row it lands on is the one with no link to explain itself.
    """
    if row.link:
        return htpy.a(data_field="project_dir", href=row.link)[
            cuts.project_path(cuts.short(row.project_dir))
        ]
    shown = cuts.project_path(cuts.short(row.project_dir)) if row.project_dir else "(no project)"
    return htpy.span(data_field="project_dir")[shown]


def _window(
    *,
    sessions_field: str,
    sessions: int,
    cost_field: str,
    cost: float | None,
    unpriced: int | None,
) -> Html:
    """One window's cell: how many sessions it holds, over what they cost.

    The field names are the store's own column names rather than the column heading, so a test
    reads a window without matching the label the page prints over it.
    """
    return htpy.td(".number")[
        parts.stacked(
            field=sessions_field,
            primary=fmt.count(sessions),
            secondary_field=cost_field,
            secondary=fmt.money(cost),
            unit=None,
            primary_mark=None,
            secondary_mark=parts.unpriced(calls=unpriced),
        )
    ]


class Described(NamedTuple):
    """What a pass said one session was, as a row of the list prints it.

    Three values or none of them: a row the pass reached carries all three, and a row it has
    not carries no enrichment line at all.
    """

    description: str
    category: str
    outcome: str


class SessionRow(NamedTuple):
    """One session as a row of the list prints it, built from its store row.

    The three lists grow with a session, so the query cuts each and says how many it left: a
    row of the list is multiplied by the size of the page. `description` and the two tags come
    from an enrichment pass and are absent over a store no pass has reached.
    """

    session_id: str
    started_at: dt.datetime | None
    title: str | None
    project_dir: str | None
    turns: int
    api_calls: int
    tool_calls: int
    compactions: int
    tool_errors: int
    cost_usd: float
    output_tokens: int
    unpriced_api_calls: int
    wall_ms: int | None
    active_ms: int | None
    agent_types: Sequence[parts.Count]
    agent_types_cut: int
    skills: Sequence[str]
    skills_cut: int
    work: Sequence[parts.Count]
    work_cut: int
    described: Described | None


class Heading(NamedTuple):
    """One sortable column: the store's own column name, its label, and where its link goes."""

    key: str
    label: str
    url: str


class Pages(NamedTuple):
    """Where the list goes from here, and which sessions of it this page is showing."""

    first: int
    shown: int
    previous: str | None
    next: str | None


def sessions_page(
    *,
    rows: Sequence[SessionRow],
    headings: Sequence[Heading],
    sort: str,
    direction: str,
    aria_direction: str,
    controls: Sequence[Control],
    projects: Sequence[str],
    pages: Pages,
    describes: bool,
    citations: Mapping[str, Cited],
    dev: bool,
) -> Html:
    """One page of sessions, under the filter, sort and size the URL carried.

    `describes` says whether the store holds an enrichment pass's answers at all, which decides
    whether the list carries a work column: an empty one over a store no pass has touched is a
    claim the store cannot support. The same pager stands above and below the table.
    """
    turning = _turning(pages)
    return layout.page(
        tab_title="Sessions — hyphae",
        scripts=None,
        main=htpy.fragment[
            [
                htpy.h1["Sessions"],
                _form(sort=sort, direction=direction, controls=controls),
                # Suggestions, not a closed set: a project the store has no sessions for is an
                # empty list rather than an error, and typing a path the datalist lacks still
                # runs.
                htpy.datalist(id="project-names")[
                    [htpy.option(value=project) for project in projects]
                ],
                parts.pager(name="top", pages=turning),
                htpy.table(id="sessions")[
                    [
                        htpy.thead[
                            htpy.tr[
                                [
                                    [
                                        _heading(heading=heading, sort=sort, aria=aria_direction)
                                        for heading in headings
                                    ],
                                    htpy.th(scope="col")["Skills"],
                                    htpy.th(scope="col")["Work"] if describes else None,
                                ]
                            ]
                        ],
                        htpy.tbody[[_session(row=row, describes=describes) for row in rows]],
                    ]
                ],
                parts.pager(name="bottom", pages=turning),
            ]
        ],
        footer=citation.footer(citations=citations),
        dev=dev,
    )


def _form(*, sort: str, direction: str, controls: Sequence[Control]) -> Html:
    """One input per filter the list offers, built from the controls the route composed.

    Submitting keeps the sort and drops the page: page 4 of a narrower list is a different page.
    """
    return htpy.form(id="filters", method="get", action=LIST_URL)[
        [
            htpy.input(type="hidden", name="sort", value=sort),
            htpy.input(type="hidden", name="direction", value=direction),
            [
                htpy.label(data_filter=control.key)[
                    [
                        control.key,
                        htpy.input(
                            type=control.type,
                            name=control.key,
                            value=control.value,
                            list="project-names" if control.key == "project" else None,
                        ),
                    ]
                ]
                for control in controls
            ],
            htpy.button(type="submit")["Filter"],
            htpy.a(href=LIST_URL)["clear"],
        ]
    ]


def _heading(*, heading: Heading, sort: str, aria: str) -> Html:
    """A column heading: a link that re-sorts, marked when it is the sort in force.

    The mark is spelled in ARIA's own vocabulary and not the query string's, because a token
    ARIA does not define announces nothing.
    """
    return htpy.th(
        scope="col",
        data_column=heading.key,
        aria_sort=aria if heading.key == sort else None,
    )[htpy.a(href=heading.url)[heading.label]]


def _turning(pages: Pages) -> parts.Pager:
    """The list's paging as the shared control prints it, built once for the two that show it.

    The words between the links are the sessions this page holds rather than which page of how
    many it is: a reader tiling a store by paging through it is counting rows, and the list has
    no last page to number against — the query reads one row past the page, never the rest.
    """
    last = pages.first + pages.shown - 1
    return parts.Pager(
        field="range",
        words=(
            f"Sessions {fmt.count(pages.first)}–{fmt.count(last)}" if pages.shown else "No sessions"
        ),
        # Newest first, so the page before this one holds newer sessions.
        previous=parts.Step(pages.previous, "← newer page") if pages.previous else None,
        next=parts.Step(pages.next, "older page →") if pages.next else None,
    )


def _session(*, row: SessionRow, describes: bool) -> Html:
    """One session's row, in the order the headings above it name."""
    return htpy.tr(data_session_id=row.session_id)[
        [
            # When, at the scale a list is scanned at, over the timestamp a report would quote.
            htpy.td(".when")[
                parts.stacked(
                    field="ago",
                    primary=cuts.ago(row.started_at),
                    secondary_field="started_at",
                    secondary=fmt.when(row.started_at),
                    unit=None,
                    primary_mark=None,
                    secondary_mark=None,
                )
            ],
            htpy.td(".title")[_title(row=row)],
            htpy.td(".path", data_field="project_dir")[
                cuts.project_path(cuts.short(row.project_dir))
            ],
            htpy.td(".number", data_field="turns")[fmt.count(row.turns)],
            htpy.td(".number", data_field="api_calls")[fmt.count(row.api_calls)],
            htpy.td(".number", data_field="tool_calls")[fmt.count(row.tool_calls)],
            htpy.td(".number", data_field="compactions")[fmt.count(row.compactions)],
            # The rate says whether a session was going wrong; the count is what ranks it,
            # which is why the heading over it sorts by the line underneath.
            htpy.td(".number")[
                parts.stacked(
                    field="error_rate",
                    primary=fmt.share(row.tool_errors, row.tool_calls),
                    secondary_field="tool_errors",
                    secondary=fmt.count(row.tool_errors),
                    unit="errors",
                    primary_mark=None,
                    secondary_mark=None,
                )
            ],
            # The unpriced count rides beside the cost: a total with calls our price table
            # missed is not the session's cost, and the page has to say so.
            htpy.td(".number")[
                parts.stacked(
                    field="cost_usd",
                    primary=fmt.money(row.cost_usd),
                    secondary_field="output_tokens",
                    secondary=fmt.count(row.output_tokens),
                    unit="out",
                    primary_mark=parts.unpriced(calls=row.unpriced_api_calls),
                    secondary_mark=None,
                )
            ],
            htpy.td(".number")[
                parts.stacked(
                    field="wall_ms",
                    primary=fmt.duration(row.wall_ms),
                    secondary_field="active_ms",
                    secondary=fmt.duration(row.active_ms),
                    unit="active",
                    primary_mark=None,
                    secondary_mark=None,
                )
            ],
            # The two lists a transcript named have their members marked where the cut bit;
            # the kinds of work do not, because that vocabulary is closed and cannot reach its
            # cut.
            htpy.td(".names", data_field="agent_types")[
                [
                    parts.counted(entries=row.agent_types, mark_cuts=True),
                    parts.more(cut=row.agent_types_cut),
                ]
            ],
            htpy.td(".names", data_field="skills")[
                [
                    ", ".join(cuts.item(skill) for skill in row.skills),
                    parts.more(cut=row.skills_cut),
                ]
            ],
            htpy.td(".names", data_field="work")[
                [
                    parts.counted(entries=row.work, mark_cuts=False),
                    parts.more(cut=row.work_cut),
                ]
            ]
            if describes
            else None,
        ]
    ]


def _title(*, row: SessionRow) -> Html:
    """The prompt's own title, over what a pass said the session was.

    The enrichment line is absent over a store no pass has touched, and over a session it has
    not reached yet. Never a stale tag: the list joins the words and not the versions that
    would judge them, and the session's own page says so a click away.
    """
    return htpy.fragment[
        [
            htpy.a(".primary", data_field="title", href=f"/session/{row.session_id}")[
                cuts.short(row.title or row.session_id)
            ],
            _described(session_id=row.session_id, said=row.described),
        ]
    ]


def _described(*, session_id: str, said: Described | None) -> Html | None:
    """What a pass said this session was, under the prompt's own title."""
    if said is None:
        return None
    return htpy.span(".enrichment.secondary", data_enrichment=session_id)[
        [
            htpy.span(data_field="description")[cuts.short(said.description)],
            # A tag is a pill with a right margin and no left one, so this space is what keeps
            # the first one's border off the last word of the description.
            " ",
            parts.tags(category=said.category, outcome=said.outcome, stale=False),
        ]
    ]
