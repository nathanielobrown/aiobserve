//! `hp` — the prototype's command line. One subcommand so far: `extract`.
//!
//! Stage 4 of `plans/rust-prototype/design.md` owns the rest of the surface
//! `src/hyphae/cli.py` offers. What is here exists so the parity oracle can run the two
//! extractors over one corpus and diff the stores they write.

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
    }
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
