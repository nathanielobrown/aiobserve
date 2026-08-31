//! What may not leave the machine, and what must.
//!
//! Publishing a transcript to a third party is irreversible, so this tier sweeps the raw
//! request bytes rather than the parsed attributes: a stray field, a message built from a
//! prompt, or an attribute added next month is caught without anyone remembering to update a
//! list.
//!
//! Redaction flattened every recorded string to `[redacted]` or a `fixture-*` pseudonym, so no
//! leaf here can assert on real transcript text. Sentinels are planted onto real rows instead.
//!
//! The port of `tests/export/test_otlp__privacy.py`.

use std::collections::BTreeSet;

use hyphae_export::delivery::Shipping;
use hyphae_export::otlp::{METADATA_ONLY, TextPolicy};
use hyphae_store::Store;
use hyphae_testsupport::cache;
use hyphae_testsupport::otlp::{Value, any_value};
use hyphae_testsupport::receiver::{Receiver, Reply, TestClock, deliver, sentinel_backend};
use tempfile::TempDir;

/// Every column holding text the agent or the user wrote, and a distinct planted value for
/// each — distinct so a failure names the field that leaked, and every one longer than
/// [`TRUNCATED`] so the widening leaf below can tell a truncated value from a whole one.
/// Invented strings, planted onto real rows of a copied store: the recorded values were
/// redacted away.
const EXCLUDED: &[(&str, &str, &str)] = &[
    ("sessions", "title", "planted-leak-session-title"),
    ("sessions", "agent_name", "planted-leak-session-agent-name"),
    ("turns", "prompt", "planted-leak-turn-prompt"),
    // The name of a slash command is structure; what the user typed after it is not.
    ("turns", "command_args", "planted-leak-turn-command-args"),
    ("api_calls", "text", "planted-leak-api-call-text"),
    ("api_calls", "thinking", "planted-leak-api-call-thinking"),
    ("tool_calls", "input", "planted-leak-tool-call-input"),
    ("tool_calls", "result", "planted-leak-tool-call-result"),
    ("agent_runs", "brief", "planted-leak-agent-run-brief"),
    ("pr_links", "pr_url", "planted-leak-pr-link-url"),
    ("pr_links", "pr_repository", "planted-leak-pr-repository"),
];

/// The attribute key each excluded column ships under when text is opted in.
const TEXT_KEYS: &[&str] = &[
    "claude_code.session.title",
    "claude_code.session.agent_name",
    "claude_code.turn.prompt",
    "claude_code.turn.command_args",
    "claude_code.api_call.text",
    "claude_code.api_call.thinking",
    "claude_code.tool_call.input",
    "claude_code.tool_call.result",
    "claude_code.agent_run.brief",
    "claude_code.pr_link.url",
    "claude_code.pr_link.repository",
];

/// Characters kept per field in the widening pass. Shorter than every sentinel, so a whole
/// planted value cannot pass for a truncated one.
const TRUNCATED: usize = 20;

/// The exportable corpus with a sentinel in every excluded column of every row.
fn planted() -> (TempDir, Store) {
    let (scratch, path) = cache::writable_copy(&cache::exportable_store());
    let store = Store::open_for_write(&path).expect("the copy opens for writing");
    for (table, column, sentinel) in EXCLUDED {
        store
            .connection()
            .execute(&format!("UPDATE {table} SET \"{column}\" = ?"), [sentinel])
            .expect("the sentinel plants");
        // A column with no rows would make its sentinel unfalsifiable, so each one is checked
        // to have landed somewhere.
        let landed: i64 = store
            .connection()
            .query_row(
                &format!("SELECT count(*) FROM {table} WHERE \"{column}\" = ?"),
                [sentinel],
                |row| row.get(0),
            )
            .expect("the plant reads back");
        assert!(landed > 0, "nothing to plant {sentinel} onto");
    }
    (scratch, store)
}

/// One export pass over the planted store, under the text policy given.
fn ship(store: &Store, receiver: &Receiver, text: TextPolicy) {
    let clock = TestClock::default();
    let shipping = Shipping {
        text,
        ..Shipping::new(&clock)
    };
    let result =
        deliver(store, sentinel_backend(receiver), shipping).expect("the planted corpus ships");
    assert!(
        !result.extracted.is_empty(),
        "nothing was exported, so nothing this leaf asserts is evidence"
    );
}

/// Every value the shipped spans carry under one attribute key.
fn values(receiver: &Receiver, key: &str) -> Vec<Value> {
    receiver
        .spans()
        .iter()
        .flat_map(|span| span.attributes.clone())
        .filter(|attribute| attribute.key == key)
        .map(|attribute| {
            any_value(
                attribute
                    .value
                    .as_ref()
                    .expect("an attribute carries a value"),
            )
        })
        .collect()
}

/// Every distinct string the shipped spans carry under one attribute key.
fn strings(receiver: &Receiver, key: &str) -> BTreeSet<String> {
    values(receiver, key)
        .into_iter()
        .map(|value| match value {
            Value::Str(text) => text,
            other => panic!("{key} shipped as {other:?}"),
        })
        .collect()
}

/// One column of the shipped rows: the corpus under the analyzed project, live only.
fn column<T: duckdb::types::FromSql>(store: &Store, sql: &str) -> Vec<T> {
    let mut statement = store.connection().prepare(sql).expect("the query prepares");
    let rows = statement
        .query_map([hyphae_testsupport::landmarks::MYCELIA], |row| row.get(0))
        .expect("the query runs");
    rows.map(|row| row.expect("a row")).collect()
}

/// Every attribute key the payload carries — span and event alike, since PR links are events
/// and a key-set sweep that read only spans would miss them.
fn keys(receiver: &Receiver) -> BTreeSet<String> {
    receiver
        .spans()
        .iter()
        .flat_map(|span| {
            span.attributes
                .iter()
                .map(|attribute| attribute.key.clone())
                .chain(span.events.iter().flat_map(|event| {
                    event
                        .attributes
                        .iter()
                        .map(|attribute| attribute.key.clone())
                }))
                .collect::<Vec<_>>()
        })
        .collect()
}

#[test]
fn the_default_ship_set_carries_metadata_and_no_transcript_text() {
    // If the whole corpus is exported with a distinct sentinel in every excluded column...
    let receiver = Receiver::start();
    let (_scratch, store) = planted();
    ship(&store, &receiver, METADATA_ONLY);
    // ...then not one sentinel appears anywhere in the bytes that went out — the raw payload
    // rather than the parsed attributes, so a span name or a message built from a prompt is
    // caught as surely as an attribute is.
    let bodies = receiver.bodies();
    let leaked: Vec<String> = EXCLUDED
        .iter()
        .filter(|(_, _, sentinel)| {
            bodies
                .iter()
                .any(|body| contains(body, sentinel.as_bytes()))
        })
        .map(|(table, column_name, _)| format!("{table}.{column_name}"))
        .collect();
    assert_eq!(leaked, Vec::<String>::new());
    // ...and the metadata the analysis needs did ship, so a mapper that sends empty spans
    // cannot pass this leaf.
    for (key, sql) in [
        (
            "gen_ai.request.model",
            "SELECT DISTINCT c.model FROM api_calls c JOIN sessions s ON s.id = c.session_id \
             WHERE s.project_dir = ? AND NOT c.replayed",
        ),
        (
            "gen_ai.response.finish_reasons",
            "SELECT DISTINCT c.stop_reason FROM api_calls c JOIN sessions s ON s.id = c.session_id \
             WHERE s.project_dir = ? AND NOT c.replayed AND c.stop_reason IS NOT NULL",
        ),
        (
            "claude_code.turn.command_name",
            "SELECT DISTINCT t.command_name FROM turns t JOIN sessions s ON s.id = t.session_id \
             WHERE s.project_dir = ? AND NOT t.replayed AND t.command_name IS NOT NULL",
        ),
    ] {
        let held: BTreeSet<String> = column::<String>(&store, sql).into_iter().collect();
        assert!(!held.is_empty(), "{key} has nothing to ship");
        assert_eq!(strings(&receiver, key), held, "{key}");
    }
    for (attribute, stored) in [
        ("gen_ai.usage.input_tokens", "input_tokens"),
        ("gen_ai.usage.output_tokens", "output_tokens"),
    ] {
        let shipped: i64 = values(&receiver, attribute)
            .into_iter()
            .map(|value| match value {
                Value::Int(count) => count,
                other => panic!("{attribute} shipped as {other:?}"),
            })
            .sum();
        let held: i64 = column::<i64>(
            &store,
            &format!(
                "SELECT c.{stored} FROM api_calls c JOIN sessions s ON s.id = c.session_id \
                 WHERE s.project_dir = ? AND NOT c.replayed AND c.{stored} IS NOT NULL"
            ),
        )
        .into_iter()
        .sum();
        assert_eq!(shipped, held, "{attribute}");
    }
    let cost: f64 = values(&receiver, "claude_code.api_call.cost_usd")
        .into_iter()
        .map(|value| match value {
            Value::Double(spent) => spent,
            other => panic!("a cost shipped as {other:?}"),
        })
        .sum();
    let held: f64 = column::<f64>(
        &store,
        "SELECT c.cost_usd FROM api_calls c JOIN sessions s ON s.id = c.session_id \
         WHERE s.project_dir = ? AND NOT c.replayed AND c.cost_usd IS NOT NULL",
    )
    .into_iter()
    .sum();
    assert!((cost - held).abs() < 1e-9, "{cost} != {held}");
}

#[test]
fn include_text_widens_the_ship_set_by_exactly_the_named_fields() {
    // If the planted corpus ships once under the default policy...
    let receiver = Receiver::start();
    let (_scratch, store) = planted();
    ship(&store, &receiver, METADATA_ONLY);
    let metadata = keys(&receiver);
    // ...and again with text opted in and a cut far shorter than any sentinel — the delivery
    // rows cleared first, since a second pass otherwise skips what it already shipped...
    receiver.clear();
    receiver.answer(Reply::default());
    store
        .connection()
        .execute_batch("DELETE FROM otlp_delivery")
        .expect("the ledger empties");
    ship(
        &store,
        &receiver,
        TextPolicy {
            include: true,
            max_chars: TRUNCATED,
        },
    );
    let widened = keys(&receiver);
    // ...then the flag adds exactly the fields the design names and drops nothing, so a field
    // added to a span next month is either metadata or is listed here...
    let added: BTreeSet<&str> = widened.difference(&metadata).map(String::as_str).collect();
    assert_eq!(added, TEXT_KEYS.iter().copied().collect());
    assert_eq!(
        metadata.difference(&widened).collect::<Vec<_>>(),
        Vec::<&String>::new()
    );
    // ...and every one of them arrives cut to the ceiling: truncation is not redaction, and
    // what a reader of this data gets is a prefix, never the whole recorded value.
    let bodies = receiver.bodies();
    for (_, _, sentinel) in EXCLUDED {
        let prefix = &sentinel.as_bytes()[..TRUNCATED];
        assert!(
            bodies.iter().any(|body| contains(body, prefix)),
            "{sentinel} never shipped"
        );
        assert!(
            !bodies
                .iter()
                .any(|body| contains(body, sentinel.as_bytes())),
            "{sentinel} shipped whole"
        );
    }
}

/// Whether a payload holds a byte sequence — the sweep, over bytes rather than attributes.
fn contains(body: &[u8], needle: &[u8]) -> bool {
    body.windows(needle.len()).any(|window| window == needle)
}
