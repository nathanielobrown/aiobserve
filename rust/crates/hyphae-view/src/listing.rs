//! The store's two lists: the projects landing and the session list, with the composition behind
//! them.
//!
//! Ported from `src/hyphae/view/listing.py`. A `?sort=` column and a filter predicate cannot be
//! bound parameters, so the library query stays the citable core and this module wraps it: a WHERE
//! of fixed predicates, an ORDER BY built from two lookups, and a LIMIT. The two tables are closed,
//! so a key outside them is a 400 and never a fragment of SQL.

use crate::knobs;
use crate::urls;

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
