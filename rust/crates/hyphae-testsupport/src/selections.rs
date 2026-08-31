//! Which page to fetch: one node of every kind, and one level of every shape a log has.
//!
//! Every kind is here on purpose — the pane dispatches on the kind, and a kind missing from a
//! sweep is a kind whose page nothing renders. Each is read out of the store rather than pinned,
//! so a re-recorded fixture moves the selection instead of reddening the tier. A module of the
//! shared crate rather than one test's helpers, so every tier that sweeps the kinds sweeps the
//! same list — the twin of `tests/view/selections.py`.

use std::path::Path;

use hyphae_store::Store;

use crate::landmarks::{ANCESTOR, DENSE_TURN, MAIN};

/// The corpus's densest main-thread turn — 4 api calls under it — so the pane's children log has
/// more than one row and the NavTree has a level under the selection worth rendering.
pub fn turn_url() -> String {
    format!("/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}")
}

/// One node of every kind a URL can name: the kind, the SQL that finds one, and the URL template
/// its columns fill in the order they are selected.
pub const KINDS: [(&str, &str, &str); 8] = [
    (
        "session",
        "SELECT id FROM sessions ORDER BY id LIMIT 1",
        "/session/{0}",
    ),
    (
        "turn",
        "SELECT session_id, source, id FROM live_turns ORDER BY session_id, source, \"index\" \
         LIMIT 1",
        "/session/{0}/thread/{1}/turn/{2}",
    ),
    (
        "run",
        "SELECT session_id, id FROM live_agent_runs ORDER BY session_id, id LIMIT 1",
        "/session/{0}/run/{1}",
    ),
    (
        "call",
        "SELECT session_id, source, id FROM live_api_calls ORDER BY session_id, source, \"index\" \
         LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
    ),
    (
        "tool",
        "SELECT session_id, source, id FROM live_tool_calls ORDER BY session_id, source, id \
         LIMIT 1",
        "/session/{0}/thread/{1}/tool/{2}",
    ),
    (
        "compaction",
        "SELECT session_id, source, id FROM live_compactions ORDER BY session_id, source, id \
         LIMIT 1",
        "/session/{0}/thread/{1}/compaction/{2}",
    ),
    // The two buckets, each found by what puts a row in it: a call answering no turn of its own
    // thread, and a run whose spawning call resolves to nothing at all.
    (
        "unattributed",
        "SELECT c.session_id, c.source FROM live_api_calls c \
         LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
           AND t.id = c.turn_id \
         WHERE t.id IS NULL ORDER BY c.session_id, c.source LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
    ),
    (
        "unattached",
        "SELECT a.session_id FROM live_agent_runs a \
         LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id \
           AND tc.id = a.tool_use_id AND tc.source <> a.id \
         LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source \
           AND c.id = tc.api_call_id \
         WHERE c.id IS NULL ORDER BY a.session_id LIMIT 1",
        "/session/{0}/unattached",
    ),
];

/// The widest parent the store holds for each shape a children log takes: the shape, the SQL that
/// finds it, the URL template, and the word the log heads its count with.
///
/// Every shape is here because the log is assembled per shape — a shape missing from the sweep is
/// a shape whose page size and whose count above it nothing reads. Widest because a page has to be
/// shorter than its level for either to be legible: against a level of one, a page that served an
/// extra row and a heading that counted the page would both look right.
pub const LEVELS: [(&str, &str, &str, &str); 6] = [
    (
        "session",
        "SELECT session_id FROM live_turns WHERE source = 'main' GROUP BY 1 \
         ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}",
        "turns",
    ),
    (
        "run",
        "SELECT a.session_id, a.id FROM live_agent_runs a \
         JOIN live_turns t ON t.session_id = a.session_id AND t.source = a.id \
         GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/run/{1}",
        "turns",
    ),
    (
        "turn",
        "SELECT session_id, source, turn_id FROM live_api_calls WHERE turn_id IS NOT NULL \
         GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/turn/{2}",
        "calls",
    ),
    (
        "call",
        "SELECT session_id, source, api_call_id FROM live_tool_calls \
         GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
        "tools",
    ),
    // The two buckets, which page the same way: one out of a query, one out of a list the page
    // already holds.
    (
        "unattributed",
        "SELECT c.session_id, c.source FROM live_api_calls c \
         LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
           AND t.id = c.turn_id \
         WHERE t.id IS NULL GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
        "calls",
    ),
    (
        "unattached",
        "SELECT a.session_id FROM live_agent_runs a \
         LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id \
           AND tc.id = a.tool_use_id AND tc.source <> a.id \
         LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source \
           AND c.id = tc.api_call_id \
         WHERE c.id IS NULL GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}/unattached",
        "runs",
    ),
];

/// Every page one store can serve — the list, and every node of every session it holds.
///
/// One URL per node the NavTree can reach, read from the store the way the routes read it, so a
/// sweep over this list is a sweep over the whole viewer rather than over the two pages that used
/// to exist. Every URL here answers 200: the two buckets are included only where the store has
/// something to put in them, because an empty bucket is a node that is not there.
pub fn pages(db: &Path) -> Vec<String> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let all = |sql: &str| store.fetch(sql, &[]).expect("the store answers");
    let mut urls = vec!["/".to_owned()];
    for row in all("SELECT id FROM sessions") {
        urls.push(format!("/session/{}", row.str("id").expect("a session id")));
    }
    // A run's own id is the thread it ran on, so its URL says it once; everything else hangs off
    // the thread it was recorded on.
    for row in all("SELECT session_id, id FROM live_agent_runs") {
        urls.push(format!(
            "/session/{}/run/{}",
            row.str("session_id").expect("a session id"),
            row.str("id").expect("a run id"),
        ));
    }
    for (kind, table) in [
        ("turn", "live_turns"),
        ("call", "live_api_calls"),
        ("tool", "live_tool_calls"),
        ("compaction", "live_compactions"),
    ] {
        for row in all(&format!("SELECT session_id, source, id FROM {table}")) {
            urls.push(format!(
                "/session/{}/thread/{}/{kind}/{}",
                row.str("session_id").expect("a session id"),
                row.str("source").expect("a thread"),
                row.str("id").expect("a node id"),
            ));
        }
    }
    // A thread's unattributed bucket exists where one of its calls answers no turn *of that
    // thread* — a fork replays calls whose `turn_id` names a turn of the thread it forked from.
    for row in all(
        "SELECT DISTINCT c.session_id, c.source FROM live_api_calls c \
         LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
           AND t.id = c.turn_id \
         WHERE t.id IS NULL",
    ) {
        urls.push(format!(
            "/session/{}/thread/{}/unattributed",
            row.str("session_id").expect("a session id"),
            row.str("source").expect("a thread"),
        ));
    }
    // And the session's unattached bucket exists where a run's spawning call resolves to nothing
    // at all, which is the join `view_runs` makes, failing.
    for row in all("SELECT DISTINCT a.session_id FROM live_agent_runs a \
         LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id \
           AND tc.id = a.tool_use_id AND tc.source <> a.id \
         LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source \
           AND c.id = tc.api_call_id \
         WHERE c.id IS NULL")
    {
        urls.push(format!(
            "/session/{}/unattached",
            row.str("session_id").expect("a session id"),
        ));
    }
    urls
}

/// One recorded call to `tool`: the session, the thread, and the call's id.
///
/// Read out of the store rather than pinned, because a tool call this tier can render is one the
/// NavTree reaches — an id copied out of a transcript may name a record a later line replaced, and
/// its page is a 404 an absence assertion cannot tell from an answer.
pub fn call_to(db: &Path, tool: &str) -> (String, String, String) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, source, id FROM live_tool_calls WHERE name = $name \
             ORDER BY session_id, source, id LIMIT 1",
            &[("name", tool.into())],
        )
        .expect("the store answers");
    let row = rows
        .first()
        .unwrap_or_else(|| panic!("the corpus records a call to {tool}"));
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.str("source").expect("a thread").to_owned(),
        row.str("id").expect("a tool call id").to_owned(),
    )
}

/// The URL of one recorded node of `kind`, whichever the store answers with.
pub fn node_url(db: &Path, kind: &str) -> String {
    let (_, sql, shape) = KINDS
        .iter()
        .find(|(named, _, _)| *named == kind)
        .unwrap_or_else(|| panic!("no selection for {kind}"));
    filled(db, sql, shape)
}

/// The URL of the widest level of `shape`, and the word its log counts with.
pub fn level_url(db: &Path, shape: &str) -> (String, &'static str) {
    let (_, sql, template, word) = LEVELS
        .iter()
        .find(|(named, _, _, _)| *named == shape)
        .unwrap_or_else(|| panic!("no level for {shape}"));
    (filled(db, sql, template), word)
}

/// One row's columns pasted into a template, in the order the query selected them.
fn filled(db: &Path, sql: &str, template: &str) -> String {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store.fetch(sql, &[]).expect("the store answers");
    let row = rows.first().expect("the corpus holds one");
    let mut url = template.to_owned();
    for (at, column) in row.columns().iter().enumerate() {
        url = url.replace(
            &format!("{{{at}}}"),
            row.str(column).expect("a selected column is text"),
        );
    }
    url
}
