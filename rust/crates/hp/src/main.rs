//! `hp` — the prototype's command line: `extract` writes the trace store, `view` serves it.
//!
//! These are the two of `src/hyphae/cli.py`'s subcommands the prototype ports. The flags they
//! share carry that CLI's names and defaults, so a parity oracle can drive either implementation
//! from one command line; `--no-browser` and `--dev` are out of scope and absent, not ignored
//! (`plans/rust-prototype/design.md`).
//!
//! Both refuse rather than degrade: `extract` on a project with nothing recorded under the
//! projects root, `view` on a missing store, a store at another schema version, or a held
//! port — the store checks run before the bind, so the message names the store, not the socket.

use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use hyphae_extract::{Extractor, sessions};
use hyphae_store::Store;

/// Gitignored, so an extract never lands in a commit. The same default `hp` the Python
/// binary uses, so both write to the same place unless told otherwise.
const DEFAULT_DB: &str = "data/traces.duckdb";

#[derive(Parser)]
#[command(name = "hp", about = "Analyze AI coding agents from their telemetry")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Parse a project's transcripts into the trace store, skipping what has not changed
    Extract {
        /// Path to the analyzed repository
        project: PathBuf,
        /// Where Claude Code keeps transcripts (default: ~/.claude/projects)
        #[arg(long)]
        projects_root: Option<PathBuf>,
        /// Where to write the trace store
        #[arg(long, default_value = DEFAULT_DB)]
        db: PathBuf,
    },
    /// Open the trace store in a local browser
    View {
        /// Which trace store to read
        #[arg(long, default_value = DEFAULT_DB)]
        db: PathBuf,
        /// Which port to serve on
        #[arg(long, default_value_t = hyphae_view::app::PORT)]
        port: u16,
    },
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        // `{:#}` prints the whole chain on one line: what failed, then why.
        Err(error) => {
            eprintln!("hp: {error:#}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<()> {
    match Cli::parse().command {
        Command::Extract {
            project,
            projects_root,
            db,
        } => extract(&project, projects_root, &db),
        Command::View { db, port } => view(&db, port),
    }
}

/// `hp view`: the viewer over one store, until interrupted.
///
/// The runtime is built here rather than by a `#[tokio::main]` on `main`, so `hp extract` — which
/// is wholly synchronous — starts no reactor it never uses.
fn view(db: &std::path::Path, port: u16) -> Result<()> {
    let runtime = tokio::runtime::Runtime::new().context("starting the server runtime")?;
    runtime
        .block_on(hyphae_view::app::serve(db, port))
        .map_err(|error| anyhow::anyhow!("{error}"))
}

/// The `refresh` loop of `src/hyphae/pipeline.py`: ask what is on disk, skip what the store
/// already holds at the same fingerprint, and replace the rest.
fn extract(
    project: &std::path::Path,
    projects_root: Option<PathBuf>,
    db: &std::path::Path,
) -> Result<()> {
    let projects_root = projects_root.unwrap_or_else(sessions::default_projects_root);
    let extractor = Extractor::new(projects_root);
    let store = Store::create(db).with_context(|| format!("opening {}", db.display()))?;
    let held = store.fingerprints()?;
    let (mut extracted, mut skipped) = (0usize, 0usize);
    for source in extractor.sessions(project)? {
        if held
            .get(&source.id)
            .is_some_and(|known| *known == source.fingerprint)
        {
            skipped += 1;
            continue;
        }
        let trace = extractor
            .extract(&source)
            .with_context(|| format!("session {}", source.id))?;
        store.export(&trace, &source.fingerprint)?;
        extracted += 1;
    }
    println!("{extracted} session(s) extracted, {skipped} unchanged");
    Ok(())
}
