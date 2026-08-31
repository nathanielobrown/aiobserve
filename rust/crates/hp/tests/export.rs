//! `hp export-otlp`: the production path, end to end.
//!
//! Argument parsing, environment validation, the store's single writer and `refresh()` — the
//! tiers under `hyphae-export` prove the parts, and these leaves prove the wiring, which is
//! the only thing an operator actually runs.
//!
//! The port of `tests/export/test_otlp__cli.py`. The environment is handed in rather than set
//! on the process, so a developer's shell or `.env` decides nothing here.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use clap::Parser as _;
use hyphae_export::census::Census;
use hyphae_export::delivery::{
    DEFAULT_BATCH_SPANS, DEFAULT_TIMEOUT, ENDPOINT_ENV, GENERIC, HEADERS_ENV, Shipping, SystemClock,
};
use hyphae_export::otlp::{DEFAULT_MAX_CHARS, TextPolicy};
use hyphae_store::Store;
use hyphae_store::source::StoreSource;
use hyphae_testsupport::cache;
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_testsupport::otlp::{Value, attributes, read};
use hyphae_testsupport::receiver::{
    KEY_SENTINEL, Receiver, Reply, deliver, delivery_rows, sentinel_backend,
};
use tempfile::TempDir;

mod common;

use common::{Output, holding, hp_env};

/// This leaf's own copy of the two-session store, at a path the command opens for itself.
///
/// A path rather than an open [`Store`]: DuckDB hands the same process a cached instance and
/// never re-checks the file lock, so a leaf that opened it first would be testing the harness.
fn store_path() -> (TempDir, PathBuf) {
    cache::writable_copy(&cache::delivered_store())
}

/// The environment a configured run reads: this test's receiver, and a planted key beside it.
fn configured(receiver: &Receiver) -> HashMap<String, String> {
    HashMap::from([
        (ENDPOINT_ENV.to_owned(), receiver.url.clone()),
        (HEADERS_ENV.to_owned(), format!("x-key={KEY_SENTINEL}")),
    ])
}

/// No endpoint, no key — nowhere for a run to ship.
fn unconfigured() -> HashMap<String, String> {
    HashMap::new()
}

/// `hp export-otlp <project> --db <db> <rest>`, over the environment given.
fn export(db: &Path, environ: &HashMap<String, String>, rest: &[&str]) -> Output {
    let mut argv = vec![
        "export-otlp",
        MYCELIA,
        "--db",
        db.to_str().expect("a utf-8 path"),
    ];
    argv.extend_from_slice(rest);
    hp_env(&argv, environ)
}

/// The delivery rows a finished run left, minus the clock in the last column.
fn ledger(path: &Path) -> Vec<(String, String, String, String, i64)> {
    let store = Store::open_read_only(path).expect("the store opens read only");
    delivery_rows(&store)
        .into_iter()
        .map(|row| {
            (
                row.session_id,
                row.backend,
                row.fingerprint,
                row.mapper_version,
                row.spans_sent,
            )
        })
        .collect()
}

/// Whether the store holds the ledger table an export creates — the mark a run left at all.
fn has_ledger(path: &Path) -> bool {
    let store = Store::open_read_only(path).expect("the store opens read only");
    let count: i64 = store
        .connection()
        .query_row(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'",
            [],
            |row| row.get(0),
        )
        .expect("the catalog is readable");
    count > 0
}

/// Every value the shipped spans carry under one attribute key.
fn values(receiver: &Receiver, key: &str) -> Vec<Value> {
    receiver
        .spans()
        .iter()
        .filter_map(|span| attributes(span).get(key).cloned())
        .collect()
}

#[test]
fn the_command_ships_what_a_refresh_ships() {
    let receiver = Receiver::start();
    // If one copy of the store is exported by calling `refresh()` in the test...
    let (_direct_scratch, direct) = store_path();
    {
        let store = Store::open_for_write(&direct).expect("the copy opens for writing");
        let clock = SystemClock::new();
        deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
            .expect("the direct pass ships");
    }
    let expected = receiver.spans();
    assert!(
        !expected.is_empty(),
        "nothing shipped, so nothing below is evidence"
    );
    receiver.clear();
    // ...and another copy through the command...
    let (_scratch, path) = store_path();
    let said = export(&path, &configured(&receiver), &["--backend", GENERIC]);
    assert!(said.ok, "{}", said.stderr);
    // ...then the same spans arrive and the same ledger rows land: the command adds argument
    // parsing and a line of output, and nothing that shapes or records a span.
    assert_eq!(receiver.spans(), expected);
    assert_eq!(ledger(&path), ledger(&direct));
    assert_eq!(said.stdout.trim(), "2 session(s) exported, 0 unchanged");
}

#[test]
fn the_service_name_flag_reaches_the_backend() {
    // `--service-name` sends a run to a dataset other than the project directory's name.
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    let said = export(
        &path,
        &configured(&receiver),
        &["--service-name", "mycelia-backfill"],
    );
    assert!(said.ok, "{}", said.stderr);
    assert_eq!(
        read(&receiver.resources()[0].attributes).get("service.name"),
        Some(&Value::Str("mycelia-backfill".to_owned()))
    );
}

#[test]
fn missing_configuration_refuses_before_anything_is_read() {
    // If nothing says where to ship — an absent variable, or one holding only blanks...
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    for absent in ["", "   "] {
        let environ = HashMap::from([(ENDPOINT_ENV.to_owned(), absent.to_owned())]);
        let said = export(&path, &environ, &[]);
        assert!(!said.ok && said.stderr.contains(ENDPOINT_ENV), "{said:?}");
    }
    let said = export(&path, &unconfigured(), &[]);
    assert!(!said.ok && said.stderr.contains(ENDPOINT_ENV), "{said:?}");
    // ...then it refuses before it opens the store: no request went out, and the store came
    // away without even the ledger table a first export creates.
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
    assert!(!has_ledger(&path));
}

#[test]
fn a_failing_run_never_prints_the_key() {
    // If the backend refuses the first batch outright — the crash path, which is where a
    // header gets interpolated into a message by accident...
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    receiver.answer(Reply::status(400));
    let said = export(&path, &configured(&receiver), &[]);
    assert!(!said.ok, "a refused batch stops the run");
    // ...then the key is in none of what the run produced, on either channel — a refusal
    // carries its own sentence to `stderr`, so the message is swept with the rest...
    assert!(!said.stdout.contains(KEY_SENTINEL), "{}", said.stdout);
    assert!(!said.stderr.contains(KEY_SENTINEL), "{}", said.stderr);
    // ...and the request did carry it, so there was something to leak.
    assert_eq!(
        receiver.sent_headers()[0].get("x-key"),
        Some(&KEY_SENTINEL.to_owned())
    );
}

#[test]
fn a_locked_store_fails_fast() {
    // If an extract is running against the same store — held from another process, since
    // DuckDB answers a second open in this one differently — then the command stops at the
    // open: one writer at a time, and the source and the exporter share that store...
    let receiver = Receiver::start();
    let (scratch, path) = store_path();
    let (mut holder, release) = holding(&path, scratch.path());
    let said = export(&path, &configured(&receiver), &[]);
    std::fs::write(&release, "").expect("the holder is told to let go");
    holder.wait().expect("the holder exits");
    assert!(!said.ok, "a held store stops the run");
    assert!(
        said.stderr.contains("held by another process"),
        "{}",
        said.stderr
    );
    // ...and nothing was shipped: a run that cannot record what it delivered must not
    // deliver, or the next run duplicates the corpus.
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
}

#[test]
fn a_dry_run_counts_without_a_backend() {
    // If one compaction is planted on a recorded session — invented, because neither session
    // in this store compacted, and a compaction count of zero would prove nothing about the
    // line that reports it...
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    {
        let store = Store::open_for_write(&path).expect("the copy opens for writing");
        store
            .connection()
            .execute_batch(
                "INSERT INTO compactions \
                 SELECT 'planted-compaction', id, 'main', started_at, 'auto', 100, 10, 5 \
                 FROM sessions LIMIT 1",
            )
            .expect("the compaction plants");
    }
    // ...and the store is counted rather than shipped...
    let said = export(&path, &unconfigured(), &["--dry-run"]);
    assert!(said.ok, "{}", said.stderr);
    // ...then the printed count is the mapper's own, session for session and span for span,
    // down to the compactions among those spans — the one number no query reproduces, since
    // the replay rule that drops a fork's inherited copies lives in the mapper...
    let counted = counted(&path);
    assert_eq!(
        said.stdout.trim(),
        format!(
            "{} session(s) and {} span(s) would ship, {} of them compactions — nothing sent",
            counted.sessions, counted.spans, counted.compactions
        )
    );
    // ...which the corpus has some of, so the line is a number rather than a zero...
    assert!(counted.compactions > 0);
    // ...and the run reached no backend and refused nothing for want of a key: a dry run is
    // what an operator does *before* they have one. It leaves the store without even the
    // ledger table an export creates, and never takes the write lock.
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
    assert!(!has_ledger(&path));
}

/// What the mapper says the store would ship — the dry run's oracle, computed here.
fn counted(path: &Path) -> Census {
    let store = Store::open_read_only(path).expect("the store opens read only");
    let source = StoreSource::new(&store);
    let traces: Vec<_> = source
        .sessions(Path::new(MYCELIA))
        .expect("the project is in the store")
        .iter()
        .map(|session| source.extract(session).expect("the session extracts"))
        .collect();
    hyphae_export::census::census(&traces, &hyphae_export::otlp::METADATA_ONLY)
        .expect("the corpus counts")
}

#[test]
fn a_project_the_store_holds_nothing_under_stops_the_run() {
    // If the project names a repository the store holds nothing under — a typo, or a path
    // typed from the wrong directory — then the run says so and stops, rather than reporting
    // a clean delivery of nothing. However the command is run.
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    for arguments in [&[][..], &["--dry-run"][..]] {
        let mut argv = vec![
            "export-otlp",
            "/no/such/repo",
            "--db",
            path.to_str().expect("a utf-8 path"),
        ];
        argv.extend_from_slice(arguments);
        let said = hp_env(&argv, &configured(&receiver));
        assert!(
            !said.ok && said.stderr.contains("No session in this store"),
            "{arguments:?}: {said:?}"
        );
    }
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
}

#[test]
fn the_delivery_flags_reach_the_exporter() {
    // If a run names a rate and opts transcript text in...
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    let flags = [
        // A rate the fixture sessions cannot reach, so the leaf pays no real wait for it.
        "--rate",
        "100000",
        "--include-text",
        "--max-chars",
        "20",
    ];
    let mut argv = vec![
        "hp",
        "export-otlp",
        MYCELIA,
        "--db",
        path.to_str().expect("a utf-8 path"),
    ];
    argv.extend_from_slice(&flags);
    let hp::Command::ExportOtlp(args) = hp::Cli::try_parse_from(&argv)
        .expect("the flags parse")
        .command
    else {
        panic!("the command line named something other than an export");
    };
    // ...then the whole set arrives at what the exporter is handed, compared whole so a flag
    // the wiring drops fails here rather than silently doing nothing...
    let clock = SystemClock::new();
    let built = hp::export::shipping(&args, &clock);
    assert_eq!(
        (
            built.service_name,
            built.text,
            built.batch_spans,
            built.rate,
            built.timeout
        ),
        (
            None,
            TextPolicy {
                include: true,
                max_chars: 20
            },
            DEFAULT_BATCH_SPANS,
            100_000.0,
            DEFAULT_TIMEOUT
        )
    );
    // ...and the text policy is honored rather than merely passed: the excluded fields ship,
    // cut to the length the flag named.
    let said = export(&path, &configured(&receiver), &flags);
    assert!(said.ok, "{}", said.stderr);
    let prompts = values(&receiver, "claude_code.turn.prompt");
    assert!(!prompts.is_empty(), "no prompt shipped at all");
    for prompt in &prompts {
        let Value::Str(text) = prompt else {
            panic!("a prompt shipped as {prompt:?}");
        };
        assert!(text.chars().count() <= 20, "{text}");
    }
}

#[test]
fn a_named_backend_refuses_without_its_key() {
    // If a backend is named but nothing holds its key...
    let receiver = Receiver::start();
    let (_scratch, path) = store_path();
    let said = export(&path, &unconfigured(), &["--backend", "honeycomb"]);
    // ...then the run stops at the command, naming the variable, and nothing was read or sent.
    assert!(
        !said.ok && said.stderr.contains("HONEYCOMB_API_KEY"),
        "{said:?}"
    );
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
    // ...and a backend the registry does not hold is refused by the parser itself, so no run
    // ever reaches an endpoint we never verified.
    let said = export(&path, &configured(&receiver), &["--backend", "jaeger"]);
    assert!(!said.ok, "{said:?}");
    assert_eq!(receiver.bodies(), Vec::<Vec<u8>>::new());
}

#[test]
fn the_defaults_are_the_delivery_tier_s_own() {
    // Every default the command line carries is the constant the exporter reads, rather than
    // a second copy of the number that could drift from it.
    let hp::Command::ExportOtlp(args) = hp::Cli::try_parse_from(["hp", "export-otlp", MYCELIA])
        .expect("the bare command parses")
        .command
    else {
        panic!("the command line named something other than an export");
    };
    let clock = SystemClock::new();
    let built = hp::export::shipping(&args, &clock);
    assert_eq!(args.backend, GENERIC);
    assert_eq!(built.rate, hyphae_export::delivery::DEFAULT_RATE);
    assert_eq!(
        built.text,
        TextPolicy {
            include: false,
            max_chars: DEFAULT_MAX_CHARS
        }
    );
}
