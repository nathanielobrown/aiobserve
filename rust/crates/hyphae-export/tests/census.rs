//! The dry run: what a send would ship, counted by shaping every session and sending nothing.
//!
//! The count is the one number an operator sees before spending an hour and a backend's
//! ingest quota, so it has to be the mapper's own answer rather than a convenient
//! approximation of it.
//!
//! The twin of `tests/export/test_otlp__census.py`.

use std::path::Path;

use chrono::{DateTime, TimeDelta, Utc};
use hyphae_export::census::{Census, CensusError, census};
use hyphae_export::otlp::{METADATA_ONLY, SpanKey, session_spans, span_id};
use hyphae_model::SessionTrace;
use hyphae_store::Store;
use hyphae_store::source::StoreSource;
use hyphae_testsupport::cache;
use hyphae_testsupport::corpus::at;
use hyphae_testsupport::landmarks::{FORK_ORIGIN, FORK_RUN, MYCELIA, SPINE, SPINE_RUN};
use hyphae_testsupport::otlp::emitted;
use tempfile::TempDir;

/// The store a dry run reads when one is named, mirroring the pipeline plan's census pattern:
/// the leaf returns rather than inventing a corpus, since no fixture set is the real one.
const CORPUS_ENV: &str = "HYPHAE_CENSUS_STORE";
/// The project whose sessions that store holds; the canonical corpus is mycelia's.
const CORPUS_PROJECT_ENV: &str = "HYPHAE_CENSUS_PROJECT";

/// What the mapper emits per session, spelled independently in SQL. Kept as the formula
/// rather than today's total so the leaf does not rot as fixtures land: one root per shipped
/// session, every live turn and api call, every live tool call *no run named as its launch*,
/// every agent run, and every compaction that survives the copied-prefix replay rule — a
/// fork-source compaction at or before its run's `started_at`, or in a fork run that started
/// at no recorded time, is a copy.
///
/// The two middle terms are where a plausible formula goes wrong. Suppression is keyed by
/// tool call *id*, so a run whose call the session records twice suppresses both rows, and a
/// workflow fan-out that spawns many runs from one call suppresses that one row while
/// emitting a span per run. Counting a matched pair as one call traded for one run — the
/// shape a hand-written formula reaches for — undercounts a fan-out by every run past the
/// first.
const MAPPING: &str = "
SELECT
    (SELECT count(*) FROM extract_state WHERE session_id IN {ids})
  + (SELECT count(*) FROM turns WHERE session_id IN {ids} AND NOT replayed)
  + (SELECT count(*) FROM api_calls WHERE session_id IN {ids} AND NOT replayed)
  + (SELECT count(*) FROM tool_calls call
     WHERE call.session_id IN {ids} AND NOT call.replayed AND NOT EXISTS (
        SELECT 1 FROM agent_runs run
        WHERE run.session_id = call.session_id AND run.tool_use_id = call.id))
  + (SELECT count(*) FROM agent_runs WHERE session_id IN {ids})
  + (SELECT count(*) FROM compactions compaction
     LEFT JOIN agent_runs run
       ON run.session_id = compaction.session_id AND run.id = compaction.source
     WHERE compaction.session_id IN {ids}
       AND (run.id IS NULL OR NOT run.is_fork
            OR (run.started_at IS NOT NULL AND compaction.timestamp > run.started_at)))
";

/// Planted, synthetic: no recorded fixture holds a fork-source compaction, and the shape this
/// tier is about is a fork that copied one out of its parent's transcript. `FORK_RUN` is the
/// corpus's one fork run, and it started at this recorded instant.
fn fork_started_at() -> DateTime<Utc> {
    at("2026-07-21T22:05:03.221")
}

const PLANTED_COMPACTION: &str = "planted-compaction-0000-0000-000000000000";
const PLANT: &str = "
INSERT INTO compactions VALUES
    (?, ?, 'main', ?, 'auto', 100, 10, 5),
    (?, ?, ?, ?, 'auto', 100, 10, 5)
";

/// Planted, synthetic: no recorded fixture holds a workflow fan-out, and the canonical corpus
/// holds six groups of runs sharing one spawning call — the largest 93 runs from a single
/// `Workflow` call.
const FANOUT_RUN: &str = "planted-fanout-run";

/// The exportable corpus, writable, so a leaf can plant the shape no fixture records.
fn counted() -> (TempDir, Store) {
    let (scratch, path) = cache::writable_copy(&cache::exportable_store());
    let store = Store::open_for_write(&path).expect("the copy opens for writing");
    (scratch, store)
}

/// Every session a run would ship, shaped the way `export()` receives it.
fn traces(store: &Store, project: &Path) -> Vec<SessionTrace> {
    let source = StoreSource::new(store);
    source
        .sessions(project)
        .expect("the store places its sessions")
        .iter()
        .map(|session| source.extract(session).expect("the session reads back"))
        .collect()
}

/// One number out of the store.
fn scalar(store: &Store, query: &str) -> i64 {
    store
        .connection()
        .query_row(query, [], |row| row.get(0))
        .expect("the query answers with one number")
}

/// The span total the store's own rows say the mapper owes, over the shipped sessions.
fn mapping_true(store: &Store, shipped: &[SessionTrace]) -> i64 {
    // The ids go in as literals rather than as a bound list: DuckDB's `IN` takes no list
    // parameter, and every id here is one the store itself handed over.
    let ids: Vec<String> = shipped
        .iter()
        .map(|trace| {
            assert!(
                trace
                    .session
                    .id
                    .chars()
                    .all(|held| held.is_ascii_alphanumeric() || held == '-'),
                "{} is not an id this leaf can inline",
                trace.session.id
            );
            format!("'{}'", trace.session.id)
        })
        .collect();
    scalar(
        store,
        &MAPPING.replace("{ids}", &format!("({})", ids.join(", "))),
    )
}

#[test]
fn the_census_counts_what_the_mapper_would_ship() {
    // If every shipped session is shaped...
    let (_scratch, store) = counted();
    let shipped = traces(&store, Path::new(MYCELIA));
    let counts = census(&shipped, &METADATA_ONLY).expect("the corpus counts");
    // ...then the census agrees with the shapes themselves, session for session and span for
    // span...
    assert_eq!(counts.sessions, shipped.len());
    let shaped: usize = shipped.iter().map(|trace| emitted(trace).len()).sum();
    assert_eq!(counts.spans, shaped);
    // ...and with the store's own rows read through the mapping formula, which is the check
    // that catches a mapper counting a matched run/tool pair twice.
    assert_eq!(counts.spans as i64, mapping_true(&store, &shipped));
}

#[test]
fn the_compaction_term_follows_the_mapper_not_the_rollup_view() {
    // If a session's compaction also appears under its fork's source, timestamped inside the
    // prefix that fork copied — planted, since no recorded fixture holds one...
    let (_scratch, store) = counted();
    let before = census(&traces(&store, Path::new(MYCELIA)), &METADATA_ONLY)
        .expect("the corpus counts")
        .compactions;
    plant(&store, fork_started_at() - TimeDelta::minutes(1));
    // ...then `live_compactions` returns both copies. Its `_COUNTED` comment claims the table
    // is replay-free, and a compaction carries no `replayed` flag to make that true...
    let view = scalar(&store, "SELECT count(*) FROM live_compactions");
    assert_eq!(view, scalar(&store, "SELECT count(*) FROM compactions"));
    // ...while the census counts the original and drops the copy, because that is what the
    // send does. Reading the view here would over-report by every fork copy in the corpus.
    let counts = census(&traces(&store, Path::new(MYCELIA)), &METADATA_ONLY).expect("it counts");
    assert_eq!(counts.compactions, before + 1);
    assert_eq!(view as usize, before + 2);
}

#[test]
fn a_duplicated_compaction_with_two_live_copies_crashes_the_census() {
    // If the same planted copy is timestamped *after* the fork's first own record, the rule
    // reads both copies as live and the session would ship one compaction as two spans...
    let (_scratch, store) = counted();
    plant(&store, fork_started_at() + TimeDelta::minutes(1));
    // ...so the census crashes, naming the session and the id an operator has to look at.
    // Every duplicated group in the canonical corpus keeps exactly one live copy today; this
    // is the guard for the day a fork shape lands that the rule cannot separate.
    let crashed = census(&traces(&store, Path::new(MYCELIA)), &METADATA_ONLY)
        .expect_err("two live copies cannot be counted");
    assert!(
        matches!(crashed, CensusError::Ambiguous { .. }),
        "{crashed}"
    );
    let message = crashed.to_string();
    assert!(
        message.contains(FORK_ORIGIN) && message.contains(PLANTED_COMPACTION),
        "{message}"
    );
}

#[test]
fn one_call_shared_by_many_runs_is_suppressed_once() {
    // If a second run names the same spawning tool call as one the corpus already records...
    let (_scratch, store) = counted();
    store
        .connection()
        .execute(
            "INSERT INTO agent_runs SELECT * REPLACE (? AS id) FROM agent_runs \
             WHERE session_id = ? AND id = ?",
            duckdb::params![FANOUT_RUN, SPINE, SPINE_RUN],
        )
        .expect("the fan-out run plants");
    let shipped = traces(&store, Path::new(MYCELIA));
    let spine = shipped
        .iter()
        .find(|trace| trace.session.id == SPINE)
        .expect("the corpus holds the spine session");
    let spawning = spine
        .agent_runs
        .iter()
        .find(|run| run.id == SPINE_RUN)
        .and_then(|run| run.tool_use_id.clone())
        .expect("the recorded run names its spawning call");
    let spawn = spine
        .tool_calls
        .iter()
        .find(|call| call.id == spawning)
        .expect("the spawning call is recorded");
    let spans = session_spans(spine, &METADATA_ONLY).expect("the session shapes");
    // ...then the shared call emits no `execute_tool` span at all: suppression is keyed by
    // the call's id, so one row goes however many runs named it...
    let identifiers: Vec<&Vec<u8>> = spans.iter().map(|span| &span.span_id).collect();
    let suppressed =
        span_id(SPINE, SpanKey::ToolCall, &spawn.source, &spawn.id).expect("the id keys");
    assert!(!identifiers.contains(&&suppressed));
    // ...both runs emit their own `invoke_agent` span, hanging off the model call that made
    // the request...
    let launched: Vec<&opentelemetry_proto::tonic::trace::v1::Span> = [SPINE_RUN, FANOUT_RUN]
        .iter()
        .map(|run| span_id(SPINE, SpanKey::AgentRun, "", run).expect("the id keys"))
        .map(|id| {
            spans
                .iter()
                .find(|span| span.span_id == id)
                .expect("each run emits a span")
        })
        .collect();
    assert_eq!(
        launched
            .iter()
            .map(|span| span.name.as_str())
            .collect::<Vec<_>>(),
        ["invoke_agent claude", "invoke_agent claude"]
    );
    let parent =
        span_id(SPINE, SpanKey::ApiCall, &spawn.source, &spawn.api_call_id).expect("the id keys");
    assert!(launched.iter().all(|span| span.parent_span_id == parent));
    // ...and the census still agrees with the store's own rows. A formula that trades each
    // suppressed call for one run undercounts this session by every run past the first, which
    // is a shape only a real corpus holds.
    let counts = census(&shipped, &METADATA_ONLY).expect("the planted corpus counts");
    assert_eq!(counts.spans as i64, mapping_true(&store, &shipped));
}

#[test]
fn the_census_holds_over_a_real_corpus() {
    // Only the corpus a run would really ship can answer this, and no fixture set is it, so
    // the leaf returns rather than inventing one. It reads nothing and sends nothing: the
    // store opens read-only, and a crash here is the ambiguity guard, not a delivery failure.
    let named = std::env::var(CORPUS_ENV)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if named.is_empty() {
        return;
    }
    let store = Store::open_read_only(Path::new(&named)).expect("the named store opens");
    let project = std::env::var(CORPUS_PROJECT_ENV).unwrap_or_else(|_| MYCELIA.to_owned());
    let shipped = traces(&store, Path::new(&project));
    let counts = census(&shipped, &METADATA_ONLY).expect("the real corpus counts");
    assert_eq!(counts.spans as i64, mapping_true(&store, &shipped));
}

/// The corpus's fork compaction, planted twice: once on the parent thread, once in the fork
/// at `copied_at`.
fn plant(store: &Store, copied_at: DateTime<Utc>) {
    store
        .connection()
        .execute(
            PLANT,
            duckdb::params![
                PLANTED_COMPACTION,
                FORK_ORIGIN,
                fork_started_at() - TimeDelta::hours(1),
                PLANTED_COMPACTION,
                FORK_ORIGIN,
                FORK_RUN,
                copied_at,
            ],
        )
        .expect("the planted compactions land");
}

/// A censused corpus is only evidence when it counted something.
#[test]
fn the_fixture_corpus_ships_spans_at_all() {
    let (_scratch, store) = counted();
    let counts: Census =
        census(&traces(&store, Path::new(MYCELIA)), &METADATA_ONLY).expect("the corpus counts");
    assert!(counts.sessions > 0 && counts.spans > counts.sessions);
    // The compaction term is a real number rather than a zero, so the leaves above that move
    // it by one are moving something.
    assert!(counts.compactions > 0);
}
