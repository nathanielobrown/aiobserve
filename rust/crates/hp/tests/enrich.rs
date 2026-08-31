//! What `hp enrich` does around a run: dry runs, the price it quotes, the flags it forwards.
//!
//! Ported from `tests/enrich/test_enricher__cli.py`. Same fake model as the enricher's own
//! leaves (`hyphae_testsupport::passes`), driven through the command line rather than the
//! library — so a leaf here is about what a person typing the command sees and what reaches
//! the client, not about what a round writes.
//!
//! The store is the whole clean fixture corpus rather than the Python file's three sessions,
//! so every count was taken against it. Nothing here starts a process: [`Seam`] stands in for
//! the two doors `hp enrich` opens onto a real model, which is what Python monkeypatches
//! `cli.preflight` and `cli.build_client` for.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hp::{CliError, Models};
use hyphae_enrich::client::{Answer, BatchClient, DEFAULT_CONCURRENCY, EnrichRequest, RoundError};
use hyphae_enrich::cost::{Prompt, estimate};
use hyphae_enrich::{EnrichmentStore, Level, plan};
use hyphae_testsupport::fake_cli::MODEL;
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_testsupport::passes::{FakeClient, level_keys, rename_a_leaf_tool, written};
use hyphae_testsupport::{cache, corpus};
use tempfile::TempDir;

mod common;

use common::hp_with;

/// What the corpus hands out to describe, by level, counted against this store. The three
/// numbers are what an unenriched dry run quotes, and their sum is what it sends.
const RUNS: usize = 11;
const TURNS: usize = 17;
const SESSIONS: usize = 13;
const ITEMS: usize = RUNS + TURNS + SESSIONS;

/// The two doors, faked: which was opened, in what order, and how wide the client was made.
///
/// One client for the whole seam, handed out behind an `Arc`, so a leaf can read what the
/// command sent after the command has returned.
struct Seam {
    opened: Mutex<Vec<&'static str>>,
    widths: Mutex<Vec<usize>>,
    client: Arc<FakeClient>,
}

impl Seam {
    fn new() -> Self {
        Self {
            opened: Mutex::new(Vec::new()),
            widths: Mutex::new(Vec::new()),
            client: Arc::new(FakeClient::new()),
        }
    }

    /// Which doors were opened, in order — `preflight` before `client`, or neither.
    fn opened(&self) -> Vec<&'static str> {
        self.opened.lock().expect("the seam's lock").clone()
    }

    /// The concurrency every built client was given.
    fn widths(&self) -> Vec<usize> {
        self.widths.lock().expect("the seam's lock").clone()
    }
}

impl Models for Seam {
    fn preflight(&self) -> Result<(), CliError> {
        self.opened
            .lock()
            .expect("the seam's lock")
            .push("preflight");
        Ok(())
    }

    fn client(&self, _model: &str, concurrency: usize) -> Result<Box<dyn BatchClient>, CliError> {
        self.opened.lock().expect("the seam's lock").push("client");
        self.widths
            .lock()
            .expect("the seam's lock")
            .push(concurrency);
        Ok(Box::new(Shared(self.client.clone())))
    }
}

/// The one fake client, lent to the command while the leaf keeps its own handle.
struct Shared(Arc<FakeClient>);

impl BatchClient for Shared {
    fn model(&self) -> &str {
        self.0.model()
    }

    fn submit(&self, requests: &[EnrichRequest]) -> Result<Vec<Answer>, RoundError> {
        self.0.submit(requests)
    }
}

/// A private copy of the cached corpus, at a path `hp enrich` can be pointed at.
///
/// The `TempDir` comes back with it: dropping it deletes the file.
fn corpus_copy() -> (TempDir, PathBuf) {
    cache::writable_copy(&cache::corpus_store())
}

/// Read the store at `path`, and close it before the next `hp` run opens it again.
fn read<T>(path: &Path, reader: impl FnOnce(&EnrichmentStore) -> T) -> T {
    let store = EnrichmentStore::open(path).expect("the copy opens for enrichment");
    reader(&store)
}

/// Quoting a run asks nothing about auth; a run that would spend asks before it renders.
///
/// Whoever decides whether to pay for a pass is not always whoever is logged in.
#[test]
fn a_dry_run_asks_no_auth_question() {
    let (_scratch, path) = corpus_copy();
    // If a store is priced...
    let seam = Seam::new();
    let quoting = hp_with(
        &["enrich", "--db", &path.display().to_string(), "--dry-run"],
        &seam,
    );
    assert!(quoting.ok, "{}", quoting.stderr);
    // ...then it quotes the plan, opens neither door, and writes no row...
    assert!(
        quoting
            .stdout
            .contains(&format!("at most {ITEMS} item(s) would be sent"))
    );
    assert_eq!(seam.opened(), Vec::<&str>::new());
    assert!(read(&path, written).is_empty());
    // ...and a real run over the same store asks first: the auth question comes before the
    // client that would spend, so a logged-out machine fails before it renders a prompt.
    let spending = hp_with(
        &[
            "enrich",
            "--db",
            &path.display().to_string(),
            "--limit",
            "1",
        ],
        &seam,
    );
    assert!(spending.ok, "{}", spending.stderr);
    assert_eq!(seam.opened(), ["preflight", "client"]);
}

/// `--no-batch` is gone: a script still passing it stops rather than silently batching.
///
/// There is one path now, and it is neither of the two the flag chose between.
#[test]
fn the_removed_batch_flag_is_rejected() {
    let (_scratch, path) = corpus_copy();
    let refused = hp_with(
        &["enrich", "--db", &path.display().to_string(), "--no-batch"],
        &Seam::new(),
    );
    assert!(!refused.ok);
    assert!(refused.stderr.contains("--no-batch"), "{}", refused.stderr);
}

/// A dry run over a store nothing has enriched leaves its three tables behind, empty.
///
/// Deliberate, and the one thing a dry run does write: opening a store is the single path that
/// creates the enrichment schema, and a read-only second path would be a second way to be
/// wrong about it. The tables are empty, and any run would have created them anyway.
#[test]
fn a_dry_run_creates_the_enrichment_tables_it_finds_missing() {
    // If a store the pipeline wrote and enrichment has never opened is priced...
    let (_scratch, path) = corpus_copy();
    let quoting = hp_with(
        &["enrich", "--db", &path.display().to_string(), "--dry-run"],
        &Seam::new(),
    );
    assert!(quoting.ok, "{}", quoting.stderr);
    assert!(
        quoting
            .stdout
            .contains(&format!("at most {ITEMS} item(s) would be sent"))
    );
    // ...then all three tables are there afterwards — the read would raise if one were
    // missing — and every one of them is empty.
    let store = hyphae_store::Store::open_read_only(&path).expect("the store reopens");
    for level in Level::ALL {
        let counted = store
            .fetch(
                &format!("SELECT count(*) AS held FROM {}", level.table()),
                &[],
            )
            .expect("the table reads");
        assert_eq!(counted[0].i64("held").expect("a count reads"), 0, "{level}");
    }
}

/// `--dry-run` says how much a run would send, broken down by level, and writes no row.
#[test]
fn a_dry_run_writes_nothing_and_sends_nothing() {
    let (_scratch, path) = corpus_copy();
    let seam = Seam::new();
    let quoting = hp_with(
        &["enrich", "--db", &path.display().to_string(), "--dry-run"],
        &seam,
    );
    // If a dry run is asked for, then it reports every stale item at every level...
    assert!(
        quoting
            .stdout
            .contains(&format!("at most {ITEMS} item(s) would be sent to {MODEL}"))
    );
    assert!(
        quoting.stdout.contains(&format!(
            "{RUNS} agent_run, {TURNS} turn, {SESSIONS} session"
        )),
        "{}",
        quoting.stdout
    );
    // ...and it sends nothing and writes nothing.
    assert_eq!(seam.opened(), Vec::<&str>::new());
    assert!(read(&path, written).is_empty());
    // A quote for a limited run is limited too, or the number a person reads before typing
    // the command again without `--dry-run` is not the number they would then be charged
    // for. The limit is spent from the deepest round outwards, so three items are three runs.
    let limited = hp_with(
        &[
            "enrich",
            "--db",
            &path.display().to_string(),
            "--dry-run",
            "--limit",
            "3",
        ],
        &seam,
    );
    assert!(
        limited.stdout.contains("at most 3 item(s) would be sent"),
        "{}",
        limited.stdout
    );
    assert!(
        limited.stdout.contains("3 agent_run, 0 turn, 0 session"),
        "{}",
        limited.stdout
    );
}

/// `--project` names a repository from any working directory, relative spelling included, and
/// scopes what a run spends as well as what a quote reports.
#[test]
fn a_project_named_relatively_scopes_the_run() {
    let (_scratch, path) = corpus_copy();
    let db = path.display().to_string();
    // If the project is named the way a shell in its parent directory would name it — and a
    // recorded `project_dir` is absolute, so the root is the one such directory here...
    std::env::set_current_dir("/").expect("the root is a directory");
    let relative = MYCELIA.trim_start_matches('/').to_owned();
    let scoped = hp_with(
        &["enrich", "--db", &db, "--dry-run", "--project", &relative],
        &Seam::new(),
    );
    assert!(scoped.ok, "{}", scoped.stderr);
    // ...then it prices what the absolute spelling prices, rather than the nothing an
    // unresolved path finds...
    let absolute = hp_with(
        &["enrich", "--db", &db, "--dry-run", "--project", MYCELIA],
        &Seam::new(),
    );
    assert_eq!(scoped.stdout, absolute.stdout);
    // ...and what it prices is some of the corpus rather than all of it, so a filter that
    // resolved the path and then ignored it would not read as a pass.
    let whole = hp_with(&["enrich", "--db", &db, "--dry-run"], &Seam::new());
    assert_ne!(scoped.stdout, whole.stdout);
    let quoted = quoted_items(&scoped.stdout);
    assert!((1..ITEMS).contains(&quoted), "{}", scoped.stdout);
    // ...and the pass that follows spends on the same set: on a store nothing has enriched
    // every item is stale, so the quote is exact and the rows can be counted against it.
    let seam = Seam::new();
    let spending = hp_with(&["enrich", "--db", &db, "--project", &relative], &seam);
    assert!(spending.ok, "{}", spending.stderr);
    assert_eq!(seam.client.keys().len(), quoted);
    assert_eq!(read(&path, written).len(), quoted);
}

/// The item count out of the first line a quote prints.
fn quoted_items(printed: &str) -> usize {
    printed
        .split_once("at most ")
        .and_then(|(_, rest)| rest.split_once(" item(s)"))
        .map(|(count, _)| count.parse().expect("the quote counts in digits"))
        .unwrap_or_else(|| panic!("no quote in {printed:?}"))
}

/// One stale leaf is quoted as four items: itself and everything that embeds it.
///
/// Whether the cascade really reaches that far is unknowable before the answers come back,
/// which is why the report says "at most" rather than naming a price.
#[test]
fn a_dry_run_counts_the_ancestors_of_what_is_stale() {
    let (_scratch, path) = corpus_copy();
    let db = path.display().to_string();
    let seam = Seam::new();
    assert!(hp_with(&["enrich", "--db", &db], &seam).ok);
    // If a fully enriched store has one leaf run made stale — by renaming a tool call only
    // that run's prompt renders...
    read(&path, rename_a_leaf_tool);
    // ...then a dry run quotes the leaf, the run that spawned it, the main turn that spawned
    // *that*, and the session holding the turn — one per level of the chain above it.
    let quoting = hp_with(&["enrich", "--db", &db, "--dry-run"], &Seam::new());
    assert!(
        quoting.stdout.contains("at most 4 item(s) would be sent"),
        "{}",
        quoting.stdout
    );
    assert!(
        quoting.stdout.contains("2 agent_run, 1 turn, 1 session"),
        "{}",
        quoting.stdout
    );
}

/// The quoted dollars are arithmetic over the prompts, checkable without a network.
#[test]
fn a_dry_run_quotes_a_price_it_computed_itself() {
    let (_scratch, path) = corpus_copy();
    // If a dry run reports on a store nothing has enriched...
    let quoting = hp_with(
        &["enrich", "--db", &path.display().to_string(), "--dry-run"],
        &Seam::new(),
    );
    // ...then the price it printed is the one `estimate` derives from the same prompts —
    // one figure, because there is one way to send an item...
    let quote = read(&path, |store| {
        let planned = plan(store, MODEL, None, None).expect("the plan reads");
        let prompts: Vec<Prompt> = planned
            .iter()
            .map(|entry| Prompt {
                level: entry.item.level(),
                content: entry.rendered.clone(),
            })
            .collect();
        estimate(&prompts, MODEL).expect("the corpus model is priced")
    });
    assert!(
        quoting
            .stdout
            .contains(&format!("at most ${:.2}", quote.usd)),
        "{}",
        quoting.stdout
    );
    // ...and short fixture prompts cost a fraction of a cent, so the report has to carry the
    // token counts to be worth reading at all.
    assert!(
        quoting.stdout.contains(&format!(
            "~{} input",
            hyphae_enrich::prompts::thousands(quote.input_tokens)
        )),
        "{}",
        quoting.stdout
    );
}

/// `hp enrich` leaves the same rows as calling `enrich` directly.
///
/// The command is a thin wrapper by intent; a check on the library alone would miss an
/// argument the CLI forgets to pass through.
#[test]
fn the_cli_writes_what_the_library_writes() {
    // If the same store is enriched twice — once through the command, once through the
    // function — with the same fake answering both...
    let (_through_scratch, through_cli) = corpus_copy();
    let (_direct_scratch, direct) = corpus_copy();
    let run = hp_with(
        &["enrich", "--db", &through_cli.display().to_string()],
        &Seam::new(),
    );
    assert!(run.ok, "{}", run.stderr);
    assert_eq!(
        run.stdout,
        format!("{ITEMS} item(s) enriched, 0 orphaned row(s) swept\n")
    );
    let expected = read(&direct, |store| {
        hyphae_enrich::enrich(store, &FakeClient::new(), None, None).expect("the pass runs");
        written(store)
    });
    // ...then both stores hold the same rows at every level, `enriched_at` aside — the one
    // column a second run cannot reproduce.
    assert_eq!(read(&through_cli, written), expected);
    assert_eq!(expected.len(), ITEMS);
}

/// `--limit N` sends at most N items, which is what makes a dev run cheap.
#[test]
fn the_cli_limits_what_it_sends() {
    let (_scratch, path) = corpus_copy();
    let seam = Seam::new();
    let run = hp_with(
        &[
            "enrich",
            "--db",
            &path.display().to_string(),
            "--limit",
            "2",
        ],
        &seam,
    );
    assert!(run.ok, "{}", run.stderr);
    assert_eq!(seam.client.keys().len(), 2);
    // The limit is spent from the deepest round outwards, so it buys two agent runs before it
    // reaches a turn.
    let (runs, others) = read(&path, |store| {
        let rows = written(store);
        let runs = level_keys(store, Level::AgentRun);
        let held = rows.len();
        (
            rows.keys()
                .filter(|id| runs.iter().any(|key| key.ends_with(&format!("|{id}"))))
                .count(),
            held,
        )
    });
    assert_eq!((runs, others), (2, 2));
}

/// `--concurrency N` sets how many `claude` processes a round runs at once, defaulting to 4.
#[test]
fn the_concurrency_flag_reaches_the_client() {
    let (_scratch, path) = corpus_copy();
    let db = path.display().to_string();
    let seam = Seam::new();
    // If the flag is left off and then given, then it is what decides the width the client is
    // built with, with a default a bare run can afford.
    assert!(hp_with(&["enrich", "--db", &db, "--limit", "1"], &seam).ok);
    assert!(
        hp_with(
            &["enrich", "--db", &db, "--limit", "1", "--concurrency", "2"],
            &seam
        )
        .ok
    );
    assert_eq!(seam.widths(), [DEFAULT_CONCURRENCY, 2]);
    assert_eq!(DEFAULT_CONCURRENCY, 4);
}

/// One place in the workspace can start a real `claude`, and no test names it.
///
/// Python shuts the door at runtime: an autouse fixture makes `subprocess.run` raise, so a
/// test that forgot to fake the CLI cannot spend the allowance
/// (`tests/enrich/test_no_live_api.py`). This port has no door to shut, because the runner is
/// a constructor argument rather than an import — reaching a process means naming the real
/// runner. So the guard is a census of who names it in code, prose aside.
#[test]
fn only_one_place_in_the_workspace_starts_a_real_claude() {
    // Spelled in halves so this file is not its own finding: a guard that named its subject
    // outright would report itself and have to be excused, and an excused guard is one that
    // would still pass with the real one deleted.
    let real_runner = concat!("Process", "Runner");
    let mut naming: Vec<String> = Vec::new();
    sources(&corpus::repo().join("rust/crates"), &mut |path, text| {
        // Comments aside: a line of prose starts no process, and this file's own docstring
        // has to be able to say what it is guarding.
        let in_code = text
            .lines()
            .any(|line| !line.trim_start().starts_with("//") && line.contains(real_runner));
        if in_code {
            naming.push(
                path.strip_prefix(corpus::repo())
                    .expect("every source sits under the repository root")
                    .display()
                    .to_string(),
            );
        }
    });
    naming.sort();
    // The type is declared in one file and constructed in one other — the production seam
    // `hp enrich` reaches for when no test replaced it. Nothing under a `tests/` directory
    // is in the list, which is the half Python's autouse guard exists for.
    assert_eq!(
        naming,
        [
            "rust/crates/hp/src/enrich.rs",
            "rust/crates/hyphae-enrich/src/runner.rs",
        ]
    );
}

/// Every `.rs` file under `root`, handed to `visit` with its text.
fn sources(root: &Path, visit: &mut impl FnMut(&Path, &str)) {
    for entry in std::fs::read_dir(root).expect("the directory reads") {
        let path = entry.expect("the entry reads").path();
        if path.is_dir() {
            sources(&path, visit);
        } else if path.extension().is_some_and(|kind| kind == "rs") {
            let text = std::fs::read_to_string(&path).expect("a source file reads");
            visit(&path, &text);
        }
    }
}
