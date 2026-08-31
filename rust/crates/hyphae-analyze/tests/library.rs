//! The library smoke tier: every shipped `.sql` runs, and every one obeys the house rules.
//!
//! The twin of `tests/analyze/test_queries.py`. Discovery, not enumeration — every leaf
//! sweeps the whole catalog, so a query added by any consumer of the library (the analysis
//! process, the viewer) is covered the moment it lands, and one shipped without a manifest
//! entry fails here rather than at a reader's prompt.
//!
//! Python parametrizes and gets one node id per query; Rust has no parametrize, so each rule
//! is one leaf that loops and names the query it failed on. The rules and the catalog they
//! run over are the same.
//!
//! [`FIXTURE_BINDINGS`] is where a query says what to bind on the fixture corpus. A query
//! whose manifest marks a parameter required must appear there, or the smoke leaf fails
//! naming it.

use std::collections::BTreeSet;

use hyphae_analyze::{CORPUS_RELATIONS, Request};
use hyphae_enrich::schema::Level;
use hyphae_store::manifest::{self, Scope};
use hyphae_store::{queries, schema};
use hyphae_testsupport::landmarks::{
    ANCESTOR, COMPACTED, COMPACTED_BOUNDARY, CONFIG_ONLY, DENSE_CALL, DENSE_CALL_TURN, DENSE_TOOL,
    DENSE_TURN, DENSE_TURN_CALL, FORK_ORIGIN, FORK_ORIGIN_RUN, MAIN, MYCELIA, OFFLOAD_FILE, RESUME,
    RESUME_LONG_RECORD, SERVER_TOOLS, SLASH_TURN, SPINE, SPINE_RUN,
};
use hyphae_testsupport::{cache, windows};
use indexmap::IndexMap;
use regex::Regex;

/// Bindings that make a query return something on the fixture corpus, per query name. The
/// production defaults are pinned by their own leaves; these are the fixture-sized values.
fn fixture_bindings(name: &str) -> IndexMap<String, String> {
    let line = RESUME_LONG_RECORD.to_string();
    let pairs: &[(&str, &str)] = match name {
        // The production floor of 3 sessions holds no pair on this corpus, so the smoke run
        // would exercise the filter and never the join under it.
        "co_occurrence" => &[("min_sessions", "1")],
        // Every fixture agent type ran exactly once, so the production floor of 5 admits none.
        "select_runs" => &[("min_runs", "1")],
        // Redaction leaves every recorded command line as `[redacted]`, and the handful that
        // survive into the corpus views sit below the production floor of 5.
        "command_failures" => &[("min_occurrences", "1")],
        // Where to split the corpus's two idle reloads, which followed silences of 6,035 and
        // 23,773 seconds: anything between them puts one on each side of the bound.
        "reload_cost_split" => &[("short_gap_seconds", "10000")],
        // One of the two fixture sessions holding a failed tool call.
        "error_records" => &[("session_id", SERVER_TOOLS)],
        // Both fixture errors are one-offs, so the production floor of 5 lists neither.
        "error_signatures" => &[("min_occurrences", "1")],
        // Redaction cuts every recorded `file_path` to `[redacted]`, so the corpus holds one
        // directory — the bucket for a path with none — and no failing call in it at all.
        "path_failures" => &[("min_occurrences", "0")],
        "records_slice" => &[
            ("session_id", RESUME),
            ("source", MAIN),
            ("first_line", "1"),
            ("last_line", "5"),
        ],
        "run_timeline" => &[("session_id", SPINE), ("source", SPINE_RUN)],
        "session_timeline"
        | "session_overview"
        | "view_runs"
        | "view_session_header"
        | "enrichment_digest" => &[("session_id", SPINE)],
        // `spine/` is the fixture session with agent runs; its main thread never compacted,
        // so the compaction markers come from the session that did.
        "view_compactions" => &[("session_id", ANCESTOR), ("source", MAIN)],
        "view_run_header" => &[("session_id", SPINE), ("run_id", SPINE_RUN)],
        // `spine/` failed nothing, so the errors list is bound at one of the two fixture
        // sessions that did — the one whose failure sits on a run thread rather than on
        // `main`, which is the shape the session-wide list exists for.
        "view_session_errors" => &[("session_id", FORK_ORIGIN)],
        // The tree levels beside a node page, bound at the session the tree tests open and at
        // the turn under it holding 4 api calls, so each level answers with more than one row.
        "view_nav_tree_turns" | "view_records" | "view_turn_records" => {
            &[("session_id", ANCESTOR), ("source", MAIN)]
        }
        "view_nav_tree_calls" => &[
            ("session_id", ANCESTOR),
            ("source", MAIN),
            ("turn_id", DENSE_TURN),
        ],
        // Bound at one api call, which is the level under a call; the turn is what the other
        // question binds — every tool call under a turn, the level `noapi` puts there — and
        // the CLI has no way to send the NULL that asks it, so this run exercises the first.
        "view_nav_tree_tools" => &[
            ("session_id", FORK_ORIGIN),
            ("source", FORK_ORIGIN_RUN),
            ("api_call_id", DENSE_CALL),
            ("turn_id", DENSE_CALL_TURN),
        ],
        // One node read whole, one per kind that has fields of its own; then the viewer's
        // drill-down, bound at the corpus's densest shapes so each answers with several rows.
        "view_turn_header" | "view_turn_calls" | "view_turn_prompt" => &[
            ("session_id", ANCESTOR),
            ("source", MAIN),
            ("turn_id", DENSE_TURN),
        ],
        "view_call_header" | "view_call_text" | "view_call_thinking" => &[
            ("session_id", ANCESTOR),
            ("source", MAIN),
            ("api_call_id", DENSE_TURN_CALL),
        ],
        // The command arm answers NULL off a call that is not a `Bash` call, which is a row
        // and not a failure — the smoke run asks whether the query runs.
        "view_tool_header" | "view_tool_input" | "view_tool_command" | "view_tool_result"
        | "view_numbers_tool" => &[
            ("session_id", FORK_ORIGIN),
            ("source", FORK_ORIGIN_RUN),
            ("tool_call_id", DENSE_TOOL),
        ],
        "view_call_tools" => &[
            ("session_id", FORK_ORIGIN),
            ("source", FORK_ORIGIN_RUN),
            ("api_call_id", DENSE_CALL),
        ],
        // The numbers behind one tree row. Bound at a turn rather than at a session, because
        // the turn is the one kind whose delta is measured against a sibling — the arm with a
        // window function under it — and at the thread the tree tests open, where that turn
        // has one before it to be measured against.
        "view_numbers" => &[
            ("session_id", SPINE),
            ("source", MAIN),
            ("node_id", SLASH_TURN),
            ("kind", "turn"),
        ],
        // And the compaction's, at the first of `compaction/`'s two recorded boundaries.
        "view_numbers_compaction" => &[
            ("session_id", COMPACTED),
            ("source", MAIN),
            ("compaction_id", COMPACTED_BOUNDARY),
        ],
        // The corpus holds exactly one offloaded tool result, and this is it.
        "view_offload" => &[("session_id", CONFIG_ONLY), ("name", OFFLOAD_FILE)],
        // A turn the corpus records a command on, so the value comes back as one a reader
        // reads rather than as the NULL every turn nobody typed a slash at holds.
        "view_turn_command_args" | "view_turn_said" => &[
            ("session_id", SPINE),
            ("source", MAIN),
            ("turn_id", SLASH_TURN),
        ],
        // A run the corpus records a spawning `Agent` call for, so both values come back as
        // the strings a pane previews rather than as the NULL a run with no such call holds.
        "view_run_brief" | "view_run_prompt" | "view_run_result" | "view_run_said" => {
            &[("session_id", SPINE), ("run_id", SPINE_RUN)]
        }
        "view_record" => &[("session_id", RESUME), ("source", MAIN), ("line_no", &line)],
        "select_enrichments" => &[("level", "agent_run")],
        // The landing page's clock. Inside the corpus's own dates, so both trailing windows
        // hold sessions on a store whose recordings recede.
        "view_project_rollups" => &[("as_of", windows::AS_OF_WHOLE)],
        // The viewer's own read of the three tables, at the thread a session page renders:
        // the plant describes `spine/` at every level, so all three arms of the union answer.
        "view_enrichment" => &[("session_id", SPINE), ("source", MAIN)],
        "view_session_said" => &[("session_id", SPINE)],
        _ => &[],
    };
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), (*value).to_owned()))
        .collect()
}

/// The clock a query file may not read: a `current_date` filter goes green on a frozen
/// fixture store today and returns nothing next month.
const CLOCK: [&str; 5] = [
    "current_date",
    "current_timestamp",
    "now",
    "today",
    "get_current_timestamp",
];

/// The view prefix only a store an enrichment pass has written to holds.
const ENRICHMENT_VIEWS: &str = "enriched_";

/// Every query the library ships, sorted — the catalog `build.rs` walked.
///
/// Each rule below is a loop, and a loop over nothing passes. The floor is what stops a
/// catalog that stopped being walked from turning this whole file green.
fn names() -> Vec<&'static str> {
    let mut names: Vec<&str> = queries::QUERIES.iter().map(|(stem, _)| *stem).collect();
    assert!(names.len() > 60, "the catalog holds {}", names.len());
    names.sort_unstable();
    names
}

/// One query's SQL with its comments cut — every rule below reads what runs.
fn statement(name: &str) -> String {
    Regex::new("--[^\n]*")
        .expect("the comment pattern compiles")
        .replace_all(queries::load(name), "")
        .into_owned()
}

/// Every bare identifier in one query file, for the static rules below.
fn identifiers(name: &str) -> BTreeSet<String> {
    scan("[A-Za-z_][A-Za-z0-9_]*", &statement(name), 0)
}

/// What a query reads: the identifier after each FROM or JOIN, CTE names included.
///
/// A rollup column is named after the table it counts (`turns`, `api_calls`), so a bare
/// identifier scan cannot tell a table read from a column selected.
fn relations(name: &str) -> BTreeSet<String> {
    scan(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
        &statement(name),
        1,
    )
}

/// The `$name` parameters the SQL text itself references.
fn declared_parameters(name: &str) -> BTreeSet<String> {
    scan(r"\$([A-Za-z_][A-Za-z0-9_]*)", &statement(name), 1)
}

/// Every match of `pattern` in `text`, taken at capture group `group`.
fn scan(pattern: &str, text: &str, group: usize) -> BTreeSet<String> {
    Regex::new(pattern)
        .expect("the pattern compiles")
        .captures_iter(text)
        .map(|found| found[group].to_owned())
        .collect()
}

/// Whether a query needs a store an enrichment pass has already written to.
fn reads_enrichment(name: &str) -> bool {
    let read = relations(name);
    Level::ALL.iter().any(|level| read.contains(level.table()))
        || read
            .iter()
            .any(|relation| relation.starts_with(ENRICHMENT_VIEWS))
}

#[test]
fn every_query_runs() {
    // The bare corpus and the one an enrichment pass has written to: a query reading either
    // family's tables has nothing to answer with on the first.
    let (corpus, enriched) = (cache::corpus_store(), cache::enriched_store());
    for name in names() {
        let query = manifest::entry(name);
        let bindings = fixture_bindings(name);
        // If a parameter is required with no default, this tier has to say what to bind...
        for (parameter, spec) in &query.params {
            assert!(
                !spec.required || bindings.contains_key(parameter),
                "{name} requires ${parameter}: add it to fixture_bindings"
            );
        }
        // `as_of` defaults to today and the trailing window is 28 days wide, so an unbound
        // run asks a frozen corpus about the last four weeks. Every fixture session recedes
        // past that edge on its own schedule, which turns each windowed query into a time
        // bomb. Pinned at the `$as_of` that opens the window before the earliest session.
        let corpus_scope = query.scope == Scope::Corpus;
        let request = Request {
            project: corpus_scope.then(|| MYCELIA.into()),
            since: None,
            as_of: windows::date(windows::AS_OF_WHOLE),
            params: bindings,
        };
        let db = if reads_enrichment(name) {
            &enriched
        } else {
            &corpus
        };
        // ...and the run completes, which is what catches a query a schema bump broke...
        let result = hyphae_analyze::run(db, name, &request)
            .unwrap_or_else(|error| panic!("{name}: {error}"));
        // ...having answered with rows. A query returning nothing on this corpus runs green
        // while asking its question of no data at all, which is the failure this tier is for.
        assert!(
            !result.rows.is_empty(),
            "{name} returned no rows: bind it in fixture_bindings"
        );
    }
}

#[test]
fn every_query_file_has_a_manifest_entry() {
    // The manifest and the directory hold the same set of queries.
    let mut declared: Vec<&str> = manifest::manifest().keys().map(String::as_str).collect();
    declared.sort_unstable();
    assert_eq!(declared, names());
}

#[test]
fn a_citation_with_nothing_bound_ends_at_the_query_file() {
    // A citation is a line someone pastes into a report, so it never trails whitespace. Every
    // shipped query resolves at least one binding, so this is the contract for a caller that
    // composes its own — the viewer builds citations from what it bound, not a manifest.
    assert_eq!(
        queries::citation("sessions", &[]),
        "-- queries/sessions.sql"
    );
}

#[test]
fn the_manifest_declares_exactly_the_parameters_the_sql_uses() {
    // No parameter goes unbound, and no manifest entry describes one that is gone.
    for name in names() {
        let declared: BTreeSet<String> = manifest::entry(name).params.keys().cloned().collect();
        assert_eq!(declared_parameters(name), declared, "{name}");
    }
}

#[test]
fn a_cross_session_query_counts_through_the_corpus_views() {
    let tables: BTreeSet<&str> = schema::TABLES.iter().map(|(table, _)| *table).collect();
    for name in names() {
        // A keyed query fetches one session's own rows, so none of this applies to it.
        if manifest::entry(name).scope != Scope::Corpus {
            continue;
        }
        // The `live_*` family counts a resume's copied rows twice across sessions, and a base
        // table counts a fork's replays as well. A corpus query reads neither: it joins the
        // `corpus_*` views to one of the relations the runner builds from `--project`.
        let read = relations(name);
        assert!(
            !read.iter().any(|relation| relation.starts_with("live_")),
            "{name} reads a live_ relation"
        );
        assert!(
            !read
                .iter()
                .any(|relation| tables.contains(relation.as_str())),
            "{name} reads a base table"
        );
        assert!(
            CORPUS_RELATIONS
                .iter()
                .any(|relation| read.contains(*relation)),
            "{name} reads neither corpus relation"
        );
    }
}

#[test]
fn a_cost_is_never_reported_without_its_unpriced_count() {
    // A cost total says how many calls our price table left out.
    for name in names() {
        let used = identifiers(name);
        if used.contains("cost_usd") {
            assert!(used.contains("unpriced_api_calls"), "{name}");
        }
    }
}

#[test]
fn no_query_reads_the_clock() {
    // Anything time-relative rides `$as_of`, so a frozen store answers the same tomorrow.
    for name in names() {
        let used = identifiers(name);
        for clock in CLOCK {
            assert!(!used.contains(clock), "{name} reads `{clock}`");
        }
    }
}
