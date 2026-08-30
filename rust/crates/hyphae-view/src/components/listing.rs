//! The store's two lists: the projects landing, and the session list under its filter form.
//!
//! Ported from `src/hyphae/view/components/listing.py`. Both are tables of one row per thing, so
//! both are built the same way — a typed row in, a `<tr>` out. What each row prints is what its
//! type carries; the composition behind them, and the links they mint, are `listing.rs`'s.

use chrono::{DateTime, Utc};
use hypertext::prelude::*;

use crate::citation::Cited;
use crate::components::parts::Count;
use crate::components::{Markup, citation, layout, parts};
use crate::cuts;
use crate::format as fmt;
use crate::listing::LIST_URL;

/// One filter as the form renders it.
pub struct Control {
    pub key: &'static str,
    /// The HTML input type the filter's parameter type earns.
    pub kind: &'static str,
    /// What this request asked for, so the form comes back holding what was typed into it.
    pub value: String,
}

/// One project as the landing page prints it: three windows of spend, and when it last ran.
///
/// `link` is the session list narrowed to this project, or nothing where there is no list to
/// open — a session that named no directory, or a path longer than the head the page shows. A
/// window the project has no sessions in sums nothing, so its cost and its unpriced count are
/// absent rather than zero.
pub struct ProjectRow {
    pub project_dir: Option<String>,
    pub link: Option<String>,
    pub recent_sessions: i64,
    pub recent_cost: Option<f64>,
    pub recent_unpriced: Option<i64>,
    pub window_sessions: i64,
    pub window_cost: Option<f64>,
    pub window_unpriced: Option<i64>,
    pub sessions: i64,
    pub cost_usd: Option<f64>,
    pub unpriced_api_calls: i64,
    pub last_active: Option<DateTime<Utc>>,
}

/// Every project the store holds sessions for, most recently active first.
///
/// The two trailing windows are headed with the days they were bound to, so a heading cannot say
/// one thing while the column counts another — the footer cites the same numbers.
pub fn projects_page(
    rows: &[ProjectRow],
    recent_days: i64,
    window_days: i64,
    cut: i64,
    citations: &[(String, Cited)],
    dev: bool,
) -> Markup {
    let main = rsx! {
        <h1>"Projects"</h1>
        <table id="projects">
            <thead>
                <tr>
                    <th scope="col">"Project"</th>
                    // Set the way the cells under them are: a count is read down its column.
                    <th class="number" scope="col">(recent_days)"d"</th>
                    <th class="number" scope="col">(window_days)"d"</th>
                    <th class="number" scope="col">"All time"</th>
                    <th scope="col">"Last active"</th>
                </tr>
            </thead>
            <tbody>@for row in rows { (project(row)) }</tbody>
        </table>
        // What the page left out, said rather than dropped: the store keeps every project, and
        // this one shows the most recently active.
        @if cut != 0 {
            <p class="more" data-more-projects=(cut)>
                "+"(fmt::count(Some(cut)))" more project(s)"
            </p>
        }
    }
    .memoize();
    layout::page(
        "Projects — hyphae",
        None,
        main,
        citation::footer(citations),
        dev,
    )
}

/// One project's row: where its sessions read, its three windows, and its last activity.
fn project(row: &ProjectRow) -> Markup {
    rsx! {
        <tr data-project=(row.project_dir.as_deref().unwrap_or(""))>
            <td class="path">(project_name(row))</td>
            (window("recent_sessions", row.recent_sessions, "recent_cost", row.recent_cost, row.recent_unpriced))
            (window("window_sessions", row.window_sessions, "window_cost", row.window_cost, row.window_unpriced))
            (window("sessions", row.sessions, "cost_usd", row.cost_usd, Some(row.unpriced_api_calls)))
            <td class="when">
                (parts::stacked(parts::Stacked {
                    field: "ago",
                    primary: &cuts::ago(row.last_active),
                    secondary_field: "last_active",
                    secondary: &fmt::when(row.last_active),
                    unit: None,
                    primary_mark: None,
                    secondary_mark: None,
                }))
            </td>
        </tr>
    }
    .memoize()
}

/// The path, as a link where there is a list to open and as text where there is not.
///
/// The link filters the list by the whole path, which is why a row whose path is longer than the
/// head this page shows is text: a link carrying a cut path lands on nothing. The sessions naming
/// no directory are text for a different reason — there is no project to open.
fn project_name(row: &ProjectRow) -> Markup {
    let path = cuts::project_path(row.project_dir.as_deref());
    if let Some(link) = &row.link {
        return rsx! { <a data-field="project_dir" href=(link)>(&path)</a> }.memoize();
    }
    let named = row
        .project_dir
        .as_deref()
        .is_some_and(|dir| !dir.is_empty());
    let shown = if named {
        path
    } else {
        "(no project)".to_owned()
    };
    rsx! { <span data-field="project_dir">(shown)</span> }.memoize()
}

/// One window's cell: how many sessions it holds, over what they cost.
///
/// The field names are the store's own column names rather than the column heading, so a test
/// reads a window without matching the label the page prints over it.
fn window(
    sessions_field: &str,
    sessions: i64,
    cost_field: &str,
    cost: Option<f64>,
    unpriced: Option<i64>,
) -> Markup {
    rsx! {
        <td class="number">
            (parts::stacked(parts::Stacked {
                field: sessions_field,
                primary: &fmt::count(Some(sessions)),
                secondary_field: cost_field,
                secondary: &fmt::money(cost),
                unit: None,
                primary_mark: None,
                secondary_mark: parts::unpriced(unpriced.unwrap_or(0)),
            }))
        </td>
    }
    .memoize()
}

/// What a pass said one session was, as a row of the list prints it.
///
/// Three values or none of them: a row the pass reached carries all three, and a row it has not
/// carries no enrichment line at all.
pub struct Described {
    pub description: String,
    pub category: String,
    pub outcome: String,
}

/// One session as a row of the list prints it, built from its store row.
///
/// The three lists grow with a session, so the query cuts each and says how many it left: a row of
/// the list is multiplied by the size of the page. `described` comes from an enrichment pass and is
/// absent over a store no pass has reached.
pub struct SessionRow {
    pub session_id: String,
    pub started_at: Option<DateTime<Utc>>,
    pub title: Option<String>,
    pub project_dir: Option<String>,
    pub turns: i64,
    pub api_calls: i64,
    pub tool_calls: i64,
    pub compactions: i64,
    pub tool_errors: i64,
    pub cost_usd: Option<f64>,
    pub output_tokens: i64,
    pub unpriced_api_calls: i64,
    pub wall_ms: Option<i64>,
    pub active_ms: Option<i64>,
    pub agent_types: Vec<Count>,
    pub agent_types_cut: i64,
    pub skills: Vec<String>,
    pub skills_cut: i64,
    pub work: Vec<Count>,
    pub work_cut: i64,
    pub described: Option<Described>,
}

/// One sortable column: the store's own column name, its label, and where its link goes.
pub struct Heading {
    pub key: &'static str,
    pub label: &'static str,
    pub url: String,
}

/// Where the list goes from here, and which sessions of it this page is showing.
pub struct Pages {
    pub first: i64,
    pub shown: i64,
    pub previous: Option<String>,
    pub next: Option<String>,
}

/// One page of sessions, under the filter, sort and size the URL carried.
///
/// `describes` says whether the store holds an enrichment pass's answers at all, which decides
/// whether the list carries a work column: an empty one over a store no pass has touched is a
/// claim the store cannot support. The same pager stands above and below the table.
pub struct SessionsPage<'a> {
    pub rows: &'a [SessionRow],
    pub headings: &'a [Heading],
    pub sort: &'a str,
    pub direction: &'a str,
    pub aria_direction: &'a str,
    pub controls: &'a [Control],
    pub projects: &'a [String],
    pub pages: Pages,
    pub describes: bool,
    pub citations: &'a [(String, Cited)],
    pub dev: bool,
}

pub fn sessions_page(shown: &SessionsPage<'_>) -> Markup {
    let main = rsx! {
        <h1>"Sessions"</h1>
        (form(shown.sort, shown.direction, shown.controls))
        // Suggestions, not a closed set: a project the store has no sessions for is an empty
        // list rather than an error, and typing a path the datalist lacks still runs.
        <datalist id="project-names">
            @for project in shown.projects { <option value=(project)></option> }
        </datalist>
        (pager("top", &shown.pages))
        <table id="sessions">
            <thead>
                <tr>
                    @for head in shown.headings { (heading(head, shown.sort, shown.aria_direction)) }
                    <th scope="col">"Skills"</th>
                    @if shown.describes { <th scope="col">"Work"</th> }
                </tr>
            </thead>
            <tbody>@for row in shown.rows { (session(row, shown.describes)) }</tbody>
        </table>
        (pager("bottom", &shown.pages))
    }
    .memoize();
    layout::page(
        "Sessions — hyphae",
        None,
        main,
        citation::footer(shown.citations),
        shown.dev,
    )
}

/// One input per filter the list offers, built from the controls the route composed.
///
/// Submitting keeps the sort and drops the page: page 4 of a narrower list is a different page.
fn form(sort: &str, direction: &str, controls: &[Control]) -> Markup {
    rsx! {
        <form id="filters" method="get" action=(LIST_URL)>
            <input type="hidden" name="sort" value=(sort)>
            <input type="hidden" name="direction" value=(direction)>
            @for control in controls {
                <label data-filter=(control.key)>
                    (control.key)
                    <input
                        type=(control.kind)
                        name=(control.key)
                        value=(&control.value)
                        list=[(control.key == "project").then_some("project-names")]
                    >
                </label>
            }
            <button type="submit">"Filter"</button>
            <a href=(LIST_URL)>"clear"</a>
        </form>
    }
    .memoize()
}

/// A column heading: a link that re-sorts, marked when it is the sort in force.
///
/// The mark is spelled in ARIA's own vocabulary and not the query string's, because a token ARIA
/// does not define announces nothing.
fn heading(head: &Heading, sort: &str, aria: &str) -> Markup {
    rsx! {
        <th
            scope="col"
            data-column=(head.key)
            aria-sort=[(head.key == sort).then_some(aria)]
        ><a href=(&head.url)>(head.label)</a></th>
    }
    .memoize()
}

/// The controls the list carries above and below the table.
///
/// `place` tells the two apart — including for a reader who hears them rather than seeing where
/// they sit. The three controls are inline and no rule holds them apart, so each link carries the
/// space that separates it from the range.
fn pager(place: &str, pages: &Pages) -> Markup {
    let last = pages.first + pages.shown - 1;
    let range = if pages.shown != 0 {
        format!(
            "Sessions {}–{}",
            fmt::count(Some(pages.first)),
            fmt::count(Some(last))
        )
    } else {
        "No sessions".to_owned()
    };
    rsx! {
        <nav class="pager" data-pager=(place) aria-label=(format!("{place} pager"))>
            @if let Some(previous) = &pages.previous {
                <a data-page="previous" href=(previous)>"← newer page"</a>" "
            }
            <span data-field="range">(range)</span>
            @if let Some(next) = &pages.next {
                " "<a data-page="next" href=(next)>"older page →"</a>
            }
        </nav>
    }
    .memoize()
}

/// One session's row, in the order the headings above it name.
fn session(row: &SessionRow, describes: bool) -> Markup {
    let path = cuts::short(row.project_dir.as_deref());
    let skills = row
        .skills
        .iter()
        .map(|skill| cuts::item(skill))
        .collect::<Vec<_>>()
        .join(", ");
    rsx! {
        <tr data-session-id=(&row.session_id)>
            // When, at the scale a list is scanned at, over the timestamp a report would quote.
            <td class="when">
                (parts::stacked(parts::Stacked {
                    field: "ago",
                    primary: &cuts::ago(row.started_at),
                    secondary_field: "started_at",
                    secondary: &fmt::when(row.started_at),
                    unit: None,
                    primary_mark: None,
                    secondary_mark: None,
                }))
            </td>
            <td class="title">(title(row))</td>
            <td class="path" data-field="project_dir">(cuts::project_path(Some(&path)))</td>
            <td class="number" data-field="turns">(fmt::count(Some(row.turns)))</td>
            <td class="number" data-field="api_calls">(fmt::count(Some(row.api_calls)))</td>
            <td class="number" data-field="tool_calls">(fmt::count(Some(row.tool_calls)))</td>
            <td class="number" data-field="compactions">(fmt::count(Some(row.compactions)))</td>
            // The rate says whether a session was going wrong; the count is what ranks it, which
            // is why the heading over it sorts by the line underneath.
            <td class="number">
                (parts::stacked(parts::Stacked {
                    field: "error_rate",
                    primary: &fmt::share(Some(row.tool_errors as f64), Some(row.tool_calls as f64)),
                    secondary_field: "tool_errors",
                    secondary: &fmt::count(Some(row.tool_errors)),
                    unit: Some("errors"),
                    primary_mark: None,
                    secondary_mark: None,
                }))
            </td>
            // The unpriced count rides beside the cost: a total with calls our price table missed
            // is not the session's cost, and the page has to say so.
            <td class="number">
                (parts::stacked(parts::Stacked {
                    field: "cost_usd",
                    primary: &fmt::money(row.cost_usd),
                    secondary_field: "output_tokens",
                    secondary: &fmt::count(Some(row.output_tokens)),
                    unit: Some("out"),
                    primary_mark: parts::unpriced(row.unpriced_api_calls),
                    secondary_mark: None,
                }))
            </td>
            <td class="number">
                (parts::stacked(parts::Stacked {
                    field: "wall_ms",
                    primary: &fmt::duration(row.wall_ms),
                    secondary_field: "active_ms",
                    secondary: &fmt::duration(row.active_ms),
                    unit: Some("active"),
                    primary_mark: None,
                    secondary_mark: None,
                }))
            </td>
            // The two lists a transcript named have their members marked where the cut bit; the
            // kinds of work do not, because that vocabulary is closed and cannot reach its cut.
            <td class="names" data-field="agent_types">
                (parts::counted(&row.agent_types, true))
                (parts::more(row.agent_types_cut))
            </td>
            <td class="names" data-field="skills">
                (skills)(parts::more(row.skills_cut))
            </td>
            @if describes {
                <td class="names" data-field="work">
                    (parts::counted(&row.work, false))
                    (parts::more(row.work_cut))
                </td>
            }
        </tr>
    }
    .memoize()
}

/// The prompt's own title, over what a pass said the session was.
///
/// The enrichment line is absent over a store no pass has touched, and over a session it has not
/// reached yet. Never a stale tag: the list joins the words and not the versions that would judge
/// them, and the session's own page says so a click away.
fn title(row: &SessionRow) -> Markup {
    let named = row.title.as_deref().filter(|title| !title.is_empty());
    let shown = cuts::short(Some(named.unwrap_or(&row.session_id)));
    rsx! {
        <a class="primary" data-field="title" href=(format!("/session/{}", row.session_id))>
            (shown)
        </a>
        (described(&row.session_id, row.described.as_ref()))
    }
    .memoize()
}

/// What a pass said this session was, under the prompt's own title.
fn described(session_id: &str, said: Option<&Described>) -> Option<Markup> {
    let said = said?;
    Some(
        rsx! {
            <span class="enrichment secondary" data-enrichment=(session_id)>
                <span data-field="description">(cuts::short(Some(&said.description)))</span>
                // A tag is a pill with a right margin and no left one, so this space is what
                // keeps the first one's border off the last word of the description.
                " "(parts::tags(&said.category, &said.outcome, false))
            </span>
        }
        .memoize(),
    )
}
