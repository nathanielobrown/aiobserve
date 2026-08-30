//! The store's two lists: the projects landing and the session list, with the composition behind
//! them.
//!
//! Ported from `src/hyphae/view/listing.py`. A `?sort=` column and a filter predicate cannot be
//! bound parameters, so the library query stays the citable core and this module wraps it: a WHERE
//! of fixed predicates, an ORDER BY built from two lookups, and a LIMIT. The two tables are closed,
//! so a key outside them is a 400 and never a fragment of SQL.

use std::collections::HashMap;

use chrono::NaiveDate;
use hyphae_extract::sessions;
use hyphae_store::{Param, Row, RowError, Store, queries};

use crate::browse::PageError;
use crate::citation;
use crate::components::Markup;
use crate::components::listing as components;
use crate::components::parts::Count;
use crate::enrichment::enriched;
use crate::format as fmt;
use crate::knobs::{self, BadAsk};
use crate::nav_tree::Bound;
use crate::store::{Page, Query, ViewError, page_rows};
use crate::urls;
use crate::viewer::Viewer;

/// Where the session list lives, and where every crumb chain starts.
pub const LIST_URL: &str = "/sessions";

/// Newest first: the session someone is looking for is usually the one that just ran.
pub const DEFAULT_SORT: &str = "started_at";
pub const DEFAULT_DIRECTION: &str = "desc";

/// A link back to the list, carrying everything that made this view of it.
///
/// Every link the list writes goes through here. A filter that rode the sort headings but not the
/// pager would widen the list halfway through reading it.
pub fn list_url(
    sort: &str,
    direction: &str,
    page: i64,
    size: i64,
    filters: &[(&str, &str)],
) -> String {
    let mut query: Vec<(&str, String)> = vec![
        ("sort", sort.to_owned()),
        ("direction", direction.to_owned()),
    ];
    if page > 1 {
        query.push(("page", page.to_string()));
    }
    if size != knobs::SESSIONS.default {
        query.push(("size", size.to_string()));
    }
    // A filter with an empty value narrows nothing, and Python's dict merge drops it.
    query.extend(
        filters
            .iter()
            .filter(|(_, value)| !value.is_empty())
            .map(|(key, value)| (*key, (*value).to_owned())),
    );
    format!("{LIST_URL}?{}", urls::query(&query))
}

/// The session list narrowed to one project, or nothing when there is no list to open.
///
/// The path is the whole one and not the head a row shows — the list's filter matches a path
/// prefix, and a cut one matches nothing.
pub fn project_link(project_dir: Option<&str>) -> Option<String> {
    project_dir.map(|dir| {
        list_url(
            DEFAULT_SORT,
            DEFAULT_DIRECTION,
            1,
            knobs::SESSIONS.default,
            &[("project", dir)],
        )
    })
}

/// What the session list can be sorted by: a column of `view_sessions`, mapped to its header
/// label. A closed table, and the only place a request's `sort` value is ever looked up — an
/// unknown key is a 400, never a fragment of SQL. Output tokens and active time are not here: they
/// ride the row as the second line of the cost and wall cells, and a column nobody ranks a corpus
/// by is texture rather than a heading.
pub const SORTS: [(&str, &str); 11] = [
    ("started_at", "Started"),
    ("title", "Session"),
    ("project_dir", "Project"),
    ("turns", "Turns"),
    ("api_calls", "Calls"),
    ("tool_calls", "Tools"),
    ("compactions", "Compactions"),
    // By the count, though the cell shows the rate: one tool call that failed is a session at
    // 100%, and not the session a reader sorting by errors is looking for.
    ("tool_errors", "Errors"),
    ("cost_usd", "Cost"),
    ("wall_ms", "Wall"),
    ("agent_runs", "Subagents"),
];

/// What a request's value has to parse as before it can bind. A value that will not parse is a
/// 400, so the type is also the only vetting a filter value gets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterType {
    Text,
    Integer,
    Date,
}

impl FilterType {
    /// The HTML input a filter's type gets on the form.
    fn control(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Integer => "number",
            Self::Date => "date",
        }
    }

    /// The word the 400 names, which is `ParamType`'s own spelling.
    fn word(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Integer => "integer",
            Self::Date => "date",
        }
    }
}

/// What the session list can be narrowed by, per query-string key. Closed, like [`SORTS`]: a key
/// outside it is a 400, and a key inside it contributes a fixed predicate and a bound value —
/// request text never becomes SQL. Read in this order, so the WHERE and the citation read the same
/// whatever order a URL happened to put them in.
pub const FILTERS: [(&str, FilterType); 5] = [
    // A path prefix, not a path: a worktree checkout sits under the repository it was cut from,
    // so filtering by a project has to hold its worktrees' sessions the way the CLI's `--project`
    // does. One statement of the rule, in `hyphae_extract::sessions`.
    ("project", FilterType::Text),
    ("since", FilterType::Date),
    ("until", FilterType::Date),
    ("skill", FilterType::Text),
    // A floor rather than a flag, so `errors=1` reads "any" and a larger number "at least".
    ("errors", FilterType::Integer),
];

/// The predicate one filter composes into the WHERE, naming its own bound parameter and nothing
/// else. It reads a column of `view_sessions`, which is what the composition wraps.
fn predicate(key: &str) -> String {
    match key {
        "project" => sessions::project_predicate("project_dir", "$project"),
        "since" => "started_at >= $since".to_owned(),
        // Inclusive of the day named: someone asking for sessions until the 7th means the 7th.
        "until" => "started_at < $until + INTERVAL 1 DAY".to_owned(),
        "skill" => "list_contains(skills, $skill)".to_owned(),
        "errors" => "tool_errors >= $errors".to_owned(),
        other => panic!("no filter named `{other}`"),
    }
}

/// The two orderings a reader can ask for, as the SQL keyword each one puts in the ORDER BY.
const DIRECTIONS: [(&str, &str); 2] = [("asc", "ASC"), ("desc", "DESC")];

/// The same two as `aria-sort` spells them. ARIA defines the tokens and `asc` is not one of them,
/// so a heading marked with the query string's own word announces no order at all.
const ARIA_SORT: [(&str, &str); 2] = [("asc", "ascending"), ("desc", "descending")];

/// What one row of the list shows of the values a transcript wrote: each string cut to a head, the
/// skills and the agent types cut to their first few with a count of what was left, and the PR
/// links the page has no column for dropped. Composed here rather than in the query because the
/// list's filters read the whole values, and applied outside the window, so it cuts the rows one
/// page shows and nothing else.
///
/// Each cut takes one character more than the row prints, which is how the component knows a value
/// was stopped rather than ended and marks it (`format::cut`).
const SHOWN: &str = "SELECT * EXCLUDE (pr_urls) REPLACE (
    substr(title, 1, $head_chars + 1) AS title,
    substr(project_dir, 1, $head_chars + 1) AS project_dir,
    list_transform(list_slice(coalesce(skills, []), 1, $head_items),
        name -> substr(name, 1, $item_chars + 1)) AS skills,
    list_slice(coalesce(agent_types, []), 1, $head_items) AS agent_types
), greatest(len(coalesce(skills, [])) - $head_items, 0) AS skills_cut,
   greatest(len(coalesce(agent_types, [])) - $head_items, 0) AS agent_types_cut FROM";

/// One page of the session list, and whether the store holds another after it.
pub struct Listing {
    pub rows: Vec<Row>,
    pub more: bool,
}

/// One page of the session list, ordered by one of [`SORTS`] — the design's composition.
///
/// The library query stays the citable core: it goes in a subquery untouched, and what is wrapped
/// around it is a WHERE of [`FILTERS`] predicates, an ORDER BY built from two lookups, a LIMIT, and
/// [`SHOWN`] over the rows that survive all three. `session_id` breaks ties in the same direction,
/// which makes every sort a total order and the page boundaries stable between requests. The rows
/// carrying no value sort last either way.
///
/// `described` says whether the store holds the enrichment tables to join. It is an argument rather
/// than a check here because it is a fact about the store, not about the request.
pub fn sorted_sessions(
    store: &Store,
    sort: &str,
    direction: &str,
    page: i64,
    size: i64,
    filters: &[(&'static str, Param)],
    described: bool,
) -> Result<Listing, ViewError> {
    let keyword = lookup(&DIRECTIONS, direction).expect("the route checked the direction");
    let listing = core(Page::Sessions);
    // What the pass said each session was, joined before the sort so a row carries it: the left
    // join adds columns and never a row, so it changes neither the order nor the count.
    let joined = if described {
        format!(
            " LEFT JOIN ({}) USING (session_id)",
            core(Page::DescribedSessions)
        )
    } else {
        String::new()
    };
    // `FILTERS` order, not the query string's: the SQL a citation stands for is the same whichever
    // way a URL was typed.
    let applied: Vec<String> = FILTERS
        .iter()
        .filter(|(key, _)| filters.iter().any(|(named, _)| named == key))
        .map(|(key, _)| predicate(key))
        .collect();
    let where_clause = if applied.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", applied.join(" AND "))
    };
    // One row past the page: cheaper than a second query, and all a pager needs to know.
    let mut bound: Vec<(&str, Param)> = vec![
        ("limit", Param::Int(size + 1)),
        ("offset", Param::Int((page - 1) * size)),
        ("head_chars", Param::Int(queries::LIST_CHARS as i64)),
        ("item_chars", Param::Int(queries::LIST_ITEM_CHARS as i64)),
        ("head_items", Param::Int(queries::LIST_ITEMS as i64)),
    ];
    bound.extend(filters.iter().map(|(key, value)| (*key, value.clone())));
    // The joined query cuts its own strings, and takes the same head a row's other strings do.
    if described {
        bound.push(("tag_chars", Param::Int(queries::TAG_CHARS as i64)));
        bound.push(("kind_chars", Param::Int(queries::TAG_CHARS as i64)));
        bound.push(("head_kinds", Param::Int(queries::LIST_CATEGORIES as i64)));
    }
    let sql = format!(
        "{SHOWN} (SELECT * FROM ({listing}){joined}{where_clause} \
         ORDER BY {sort} {keyword} NULLS LAST, session_id {keyword} LIMIT $limit OFFSET $offset)"
    );
    let mut rows = store.fetch(&sql, &bound)?;
    let more = rows.len() as i64 > size;
    rows.truncate(size as usize);
    Ok(Listing { rows, more })
}

/// One library query as a subquery: the file, stripped of the trailing semicolon a statement ends
/// with, which cannot stand inside parentheses.
fn core(query: Page) -> &'static str {
    queries::load(query.stem()).trim().trim_end_matches(';')
}

fn lookup<'a>(table: &'a [(&str, &str)], key: &str) -> Option<&'a str> {
    table
        .iter()
        .find(|(named, _)| *named == key)
        .map(|(_, value)| *value)
}

/// Every query-string key the session list reads: the filters, plus what orders and pages them.
fn list_key(key: &str) -> bool {
    FILTERS.iter().any(|(named, _)| *named == key)
        || matches!(key, "sort" | "direction" | "page" | "size")
}

/// The filters one request asked for, each parsed as the type its predicate binds.
///
/// A key outside the list's own is a 400 rather than a no-op: a mistyped filter would otherwise
/// show the whole corpus and look like an answer. An empty value is not a filter — the list's form
/// submits every field, so a blank one has to mean "not filtering".
pub fn narrowing(params: &HashMap<String, String>) -> Result<Vec<(&'static str, Param)>, BadAsk> {
    if let Some(unknown) = params.keys().find(|key| !list_key(key)) {
        let mut keys: Vec<&str> = FILTERS.iter().map(|(key, _)| *key).collect();
        keys.extend(["sort", "direction", "page", "size"]);
        keys.sort_unstable();
        let _ = unknown;
        return Err(BadAsk(format!("The list takes {}.", keys.join(", "))));
    }
    FILTERS
        .iter()
        .filter_map(|(key, kind)| {
            let given = params.get(*key).filter(|value| !value.is_empty())?;
            Some(as_bound(key, *kind, given).map(|value| (*key, value)))
        })
        .collect()
}

/// One filter's query-string text as the value DuckDB binds, or the 400 it earns.
fn as_bound(key: &str, kind: FilterType, text: &str) -> Result<Param, BadAsk> {
    let refused = || BadAsk(format!("The list's {key} takes {} values.", kind.word()));
    match kind {
        FilterType::Text => Ok(Param::Text(text.to_owned())),
        FilterType::Integer => text.parse::<i64>().map(Param::Int).map_err(|_| refused()),
        FilterType::Date => NaiveDate::parse_from_str(text, "%Y-%m-%d")
            .map(Param::Date)
            .map_err(|_| refused()),
    }
}

/// Whether the row carries a column at all — the enrichment columns are absent entirely over a
/// store with no pass to join, which is what Python's `row.get` reads past.
fn has(row: &Row, column: &str) -> bool {
    row.columns().iter().any(|named| named == column)
}

/// One store row as the row the landing page prints.
///
/// The link is minted through the list's own builder, so a project opens the list the way the list
/// links to itself, and off `project_filter` rather than the path the row shows: the filter matches
/// a whole path, and a cut one matches nothing.
fn project_row(row: &Row) -> Result<components::ProjectRow, RowError> {
    Ok(components::ProjectRow {
        project_dir: row.opt_str("project_dir")?.map(str::to_owned),
        link: project_link(row.opt_str("project_filter")?),
        recent_sessions: row.i64("recent_sessions")?,
        recent_cost: row.opt_f64("recent_cost")?,
        recent_unpriced: row.opt_i64("recent_unpriced")?,
        window_sessions: row.i64("window_sessions")?,
        window_cost: row.opt_f64("window_cost")?,
        window_unpriced: row.opt_i64("window_unpriced")?,
        sessions: row.i64("sessions")?,
        cost_usd: row.opt_f64("cost_usd")?,
        unpriced_api_calls: row.i64("unpriced_api_calls")?,
        last_active: row.opt_timestamp("last_active")?,
    })
}

/// One store row as the row the session list prints.
///
/// The three lists arrive as DuckDB lists and are NULL where the session has none, so each is read
/// as empty — the component prints what it is handed. The enrichment columns are absent entirely
/// over a store with no pass to join, which is why they are asked for rather than read.
fn session_row(row: &Row) -> Result<components::SessionRow, RowError> {
    let said = if has(row, "description") {
        row.opt_str("description")?.filter(|text| !text.is_empty())
    } else {
        None
    };
    let described = match said {
        Some(description) => Some(components::Described {
            description: description.to_owned(),
            category: row.str("category")?.to_owned(),
            outcome: row.str("outcome")?.to_owned(),
        }),
        None => None,
    };
    Ok(components::SessionRow {
        session_id: row.str("session_id")?.to_owned(),
        started_at: row.opt_timestamp("started_at")?,
        title: row.opt_str("title")?.map(str::to_owned),
        project_dir: row.opt_str("project_dir")?.map(str::to_owned),
        turns: row.i64("turns")?,
        api_calls: row.i64("api_calls")?,
        tool_calls: row.i64("tool_calls")?,
        compactions: row.i64("compactions")?,
        tool_errors: row.i64("tool_errors")?,
        cost_usd: row.opt_f64("cost_usd")?,
        output_tokens: row.i64("output_tokens")?,
        unpriced_api_calls: row.i64("unpriced_api_calls")?,
        wall_ms: row.opt_i64("wall_ms")?,
        active_ms: row.opt_i64("active_ms")?,
        agent_types: counts(row, "agent_types", "runs")?,
        agent_types_cut: row.i64("agent_types_cut")?,
        skills: row
            .strings("skills")?
            .into_iter()
            .map(str::to_owned)
            .collect(),
        skills_cut: row.i64("skills_cut")?,
        work: if has(row, "work") {
            counts(row, "work", "turns")?
        } else {
            Vec::new()
        },
        work_cut: if has(row, "work_cut") {
            row.opt_i64("work_cut")?.unwrap_or(0)
        } else {
            0
        },
        described,
    })
}

fn counts(row: &Row, column: &str, counted: &str) -> Result<Vec<Count>, RowError> {
    Ok(row
        .counts(column, counted)?
        .into_iter()
        .map(|(name, count)| Count { name, count })
        .collect())
}

/// Every project the store holds sessions for, most recently active first.
pub fn projects_page(viewer: &Viewer) -> Result<Markup, PageError> {
    // The clock both trailing windows are measured back from, read here and bound like any other
    // parameter. The query reads no clock of its own: a page counting "the last 7 days" from SQL's
    // `now()` would cite a line that answers something else tomorrow, and the footer's whole
    // promise is that a reader can re-run what the page ran.
    let bound: Bound = vec![
        ("as_of", Param::Date(fmt::utcnow().date_naive())),
        ("recent_days", Param::Int(queries::PAGE_RECENT_DAYS)),
        ("window_days", Param::Int(queries::PAGE_WINDOW_DAYS)),
        ("head_chars", Param::Int(queries::LIST_CHARS as i64)),
        ("projects", Param::Int(knobs::PROJECTS.default)),
    ];
    let rows = {
        let store = viewer.reader.connect()?;
        page_rows(&store, Page::ProjectRollups, &bound)?
    };
    // What the page cut, which the query counted before its LIMIT: a landing page that silently
    // dropped projects would be a corpus a reader cannot see.
    let cut = match rows.first() {
        Some(first) => first.i64("matched_projects")? - rows.len() as i64,
        None => 0,
    };
    let shown: Vec<components::ProjectRow> =
        rows.iter().map(project_row).collect::<Result<_, _>>()?;
    Ok(components::projects_page(
        &shown,
        queries::PAGE_RECENT_DAYS,
        queries::PAGE_WINDOW_DAYS,
        cut,
        // The bindings the two window headings print, so a heading and its column read the same
        // numbers — the citation below carries them too.
        &citation::citations(&[(Page::ProjectRollups.stem(), bound)]),
        viewer.dev,
    ))
}

/// One page of sessions, under the filter, sort and size the URL carries.
pub fn session_list(
    viewer: &Viewer,
    params: &HashMap<String, String>,
) -> Result<Markup, PageError> {
    let sort = params.get("sort").map_or(DEFAULT_SORT, String::as_str);
    let direction = params
        .get("direction")
        .map_or(DEFAULT_DIRECTION, String::as_str);
    if lookup(&SORTS, sort).is_none() || lookup(&DIRECTIONS, direction).is_none() {
        let sorts: Vec<&str> = SORTS.iter().map(|(key, _)| *key).collect();
        let ways: Vec<&str> = DIRECTIONS.iter().map(|(key, _)| *key).collect();
        return Err(BadAsk(format!(
            "Sort by one of {}, in direction {}.",
            sorts.join(", "),
            ways.join(" or ")
        ))
        .into());
    }
    let page = number(params, "page", 1)?;
    let size = number(params, "size", knobs::SESSIONS.default)?;
    if page < 1 || !(1..=knobs::SESSIONS.ceiling).contains(&size) {
        return Err(BadAsk(format!(
            "Ask for page 1 or later, at a size between 1 and {}.",
            knobs::SESSIONS.ceiling
        ))
        .into());
    }
    let filters = narrowing(params)?;
    // What the URL said, kept as text: the links have to reproduce the request, and the form has
    // to come back filled in with what was typed into it.
    let given: Vec<(&str, String)> = FILTERS
        .iter()
        .map(|(key, _)| (*key, params.get(*key).cloned().unwrap_or_default()))
        .collect();
    let (describes, listing, projects) = {
        let store = viewer.reader.connect()?;
        // Whether the store holds the enrichment tables at all, which decides both what the list
        // joins and what it cites: a page cites what it ran.
        let describes = enriched(&store)?;
        let listing = sorted_sessions(&store, sort, direction, page, size, &filters, describes)?;
        let projects = page_rows(
            &store,
            Page::Projects,
            &[
                ("head_chars", Param::Int(queries::LIST_CHARS as i64)),
                ("head_projects", Param::Int(queries::LIST_PROJECTS as i64)),
            ],
        )?;
        (describes, listing, projects)
    };
    // A header link flips the direction of the column already sorted by, and opens any other
    // column at the direction that puts its largest values first. Re-sorting starts from the first
    // page: page 4 of one order says nothing about page 4 of another.
    let flipped = if direction == "desc" { "asc" } else { "desc" };
    let linked: Vec<(&str, String)> = given
        .iter()
        .map(|(key, value)| (*key, value.clone()))
        .collect();
    let marks: Vec<(&str, &str)> = linked
        .iter()
        .map(|(key, value)| (*key, value.as_str()))
        .collect();
    let headings: Vec<components::Heading> = SORTS
        .iter()
        .map(|(key, label)| components::Heading {
            key,
            label,
            url: list_url(
                key,
                if *key == sort {
                    flipped
                } else {
                    DEFAULT_DIRECTION
                },
                1,
                size,
                &marks,
            ),
        })
        .collect();
    let controls: Vec<components::Control> = FILTERS
        .iter()
        .zip(given.iter())
        .map(|((key, kind), (_, value))| components::Control {
            key,
            kind: kind.control(),
            value: value.clone(),
        })
        .collect();
    let rows: Vec<components::SessionRow> = listing
        .rows
        .iter()
        .map(session_row)
        .collect::<Result<_, _>>()?;
    let names: Vec<String> = projects
        .iter()
        .map(|row| Ok(row.str("project_dir")?.to_owned()))
        .collect::<Result<_, RowError>>()?;
    let pages = components::Pages {
        first: (page - 1) * size + 1,
        shown: rows.len() as i64,
        previous: (page > 1).then(|| list_url(sort, direction, page - 1, size, &marks)),
        next: listing
            .more
            .then(|| list_url(sort, direction, page + 1, size, &marks)),
    };
    // What the page shows of each row is composed around the query like the paging is: re-running
    // the file alone answers with whole titles, paths and skill lists.
    let mut cited: Bound = vec![
        ("sort", Param::Text(sort.to_owned())),
        ("direction", Param::Text(direction.to_owned())),
        ("limit", Param::Int(size)),
        ("offset", Param::Int((page - 1) * size)),
        ("head_chars", Param::Int(queries::LIST_CHARS as i64)),
        ("item_chars", Param::Int(queries::LIST_ITEM_CHARS as i64)),
        ("head_items", Param::Int(queries::LIST_ITEMS as i64)),
    ];
    cited.extend(filters.iter().map(|(key, value)| (*key, value.clone())));
    let mut ran: Vec<(&'static str, Bound)> = vec![(Page::Sessions.stem(), cited)];
    // Joined to that page rather than run against it, so it is cited on its own — and only over a
    // store whose enrichment tables exist to join.
    if describes {
        ran.push((
            Page::DescribedSessions.stem(),
            vec![
                ("head_chars", Param::Int(queries::LIST_CHARS as i64)),
                ("tag_chars", Param::Int(queries::TAG_CHARS as i64)),
                ("kind_chars", Param::Int(queries::TAG_CHARS as i64)),
                ("head_kinds", Param::Int(queries::LIST_CATEGORIES as i64)),
            ],
        ));
    }
    Ok(components::sessions_page(&components::SessionsPage {
        rows: &rows,
        headings: &headings,
        sort,
        direction,
        // The same ordering in ARIA's vocabulary, for the heading that marks it: the form and the
        // links carry the query string's word, the mark carries ARIA's.
        aria_direction: lookup(&ARIA_SORT, direction).expect("the direction is one of two"),
        controls: &controls,
        projects: &names,
        pages,
        describes,
        citations: &citation::citations(&ran),
        dev: viewer.dev,
    }))
}

/// One whole-number knob of the list's URL, or the 400 a value that will not parse earns.
fn number(params: &HashMap<String, String>, key: &str, fallback: i64) -> Result<i64, BadAsk> {
    match params.get(key) {
        None => Ok(fallback),
        Some(text) => text
            .parse()
            .map_err(|_| BadAsk(format!("The list's {key} takes a whole number."))),
    }
}
