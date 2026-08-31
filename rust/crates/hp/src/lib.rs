//! `hp` as a library: the command line parsed, and each subcommand run over three channels.
//!
//! The port of `src/hyphae/cli.py`. A subcommand is a function taking two writers and
//! answering with `Result<(), CliError>`, because `capsys` carries three things a test reads
//! apart: rows on `out`, commentary and citations on `err`, and a refusal with an exit code.
//! `main.rs` supplies the process's own streams and maps [`CliError`] to a status; a test
//! supplies two `Vec<u8>` buffers and matches the error, at no process cost.
//!
//! One channel is still the process's own: the extractor warns about a damaged transcript
//! with `eprintln!` from inside `hyphae-extract`, so the two leaves that read a warning
//! spawn the binary. Everything else runs in process.

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use chrono::NaiveDate;
use clap::{Parser, Subcommand};
use hyphae_analyze::Request;
use hyphae_extract::{Extractor, sessions};
use hyphae_store::Store;

pub mod enrich;
pub mod export;
pub mod query;

pub use enrich::{ClaudeCli, Models};

/// Gitignored, so an extract never lands in a commit. The same default `hp` the Python
/// binary uses, so both write to the same place unless told otherwise.
pub const DEFAULT_DB: &str = "data/traces.duckdb";

/// A refusal: exactly what goes to stderr, and what the process should exit with.
///
/// The message is verbatim rather than decorated by the caller, because the three kinds do
/// not share a prefix: clap renders its own usage block, an operational failure is prefixed
/// `hp:` as it always was, and a query the library cannot run answers with the bare sentence
/// `cli.py` raises as `SystemExit`. The code is carried for the same reason — clap's parse
/// failures exit 2, as argparse's do, and a test matching only on text would not see it.
#[derive(Debug)]
pub struct CliError {
    pub code: u8,
    pub message: String,
}

impl CliError {
    /// A refusal the caller worded itself: exit 1, message as given.
    pub fn refusal(message: impl Into<String>) -> Self {
        Self {
            code: 1,
            message: message.into(),
        }
    }

    /// A refusal something else worded: its own sentence, undecorated, as Python's
    /// `raise SystemExit(str(error))` puts it.
    pub fn refusal_from(error: impl std::fmt::Display) -> Self {
        Self::refusal(error.to_string())
    }
}

impl From<std::io::Error> for CliError {
    /// A channel that would not take what was written to it: `hp` has nowhere else to say so.
    fn from(error: std::io::Error) -> Self {
        Self::refusal(format!("hp: {error}"))
    }
}

impl std::fmt::Display for CliError {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.write_str(&self.message)
    }
}

impl From<anyhow::Error> for CliError {
    /// `{:#}` prints the whole chain on one line: what failed, then why.
    fn from(error: anyhow::Error) -> Self {
        Self::refusal(format!("hp: {error:#}"))
    }
}

/// What a run reaches outside itself, in one place a test can build for itself.
///
/// Two doors: the model client `hp enrich` opens, and the environment `hp export-otlp` reads
/// its backend and its key from. Production takes both from the machine; a test hands over
/// its own, so no leaf is decided by a developer's shell or the `.env` beside the checkout.
pub struct Outside<'a> {
    pub models: &'a dyn Models,
    pub environ: HashMap<String, String>,
}

impl Outside<'static> {
    /// The real doors: a `claude` on the `PATH`, and the process environment under a `.env`.
    ///
    /// The `.env` is where the ingest key lives, so it is read before the environment is
    /// collected. A checkout without one is the normal case, not a failure.
    pub fn production() -> Self {
        dotenvy::dotenv().ok();
        Self {
            models: &ClaudeCli,
            environ: std::env::vars().collect(),
        }
    }
}

#[derive(Parser, Debug)]
#[command(name = "hp", about = "Analyze AI coding agents from their telemetry")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

/// `PartialEq` so one leaf can pin the whole surface: `tests/surface.rs` compares a parsed
/// command against a struct literal, which is what makes a flag added with no test a failure.
#[derive(Subcommand, Debug, PartialEq)]
pub enum Command {
    /// List the sessions recorded for a project
    Sessions {
        /// Path to the analyzed repository
        project: PathBuf,
        /// Where Claude Code keeps transcripts (default: ~/.claude/projects)
        #[arg(long)]
        projects_root: Option<PathBuf>,
    },
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
    /// Describe the extracted sessions with an AI model
    Enrich(enrich::Args),
    /// Run one library query against the trace store and print its rows and its citation
    Query {
        /// The query to run — a file in analyze/queries/
        name: String,
        /// Which trace store to read
        #[arg(long, default_value = DEFAULT_DB)]
        db: PathBuf,
        /// The analyzed repository — required by a corpus query
        #[arg(long)]
        project: Option<PathBuf>,
        /// Only count sessions started on or after this date (default: the whole corpus)
        #[arg(long)]
        since: Option<NaiveDate>,
        /// The date the trailing window is measured back from (default: today)
        #[arg(long)]
        as_of: Option<NaiveDate>,
        /// Bind one of the query's parameters, overriding its production default
        #[arg(long, value_name = "KEY=VALUE")]
        param: Vec<String>,
        /// Write CSV to stdout, commentary to stderr
        #[arg(long)]
        csv: bool,
    },
    /// Ship a project's sessions to an OTLP backend as spans
    ExportOtlp(export::Args),
    /// Open the trace store in a local browser
    View {
        /// Which trace store to read
        #[arg(long, default_value = DEFAULT_DB)]
        db: PathBuf,
        /// Which port to serve on
        #[arg(long, default_value_t = hyphae_view::app::PORT)]
        port: u16,
        /// Reload the open page when a stylesheet or a script under `view/static` is saved
        #[arg(long)]
        dev: bool,
    },
}

/// Parse a command line and run it, writing to the two channels given.
///
/// The entry point both `main` and the tests use. A parse failure is a `CliError` like any
/// other refusal, so a caller never has to decide whether clap already printed. `--help` and
/// `--version` are not failures: clap answers them with the text, which goes out on `out`.
pub fn main<I, T>(argv: I, out: &mut dyn Write, err: &mut dyn Write) -> Result<(), CliError>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
{
    main_with(argv, &Outside::production(), out, err)
}

/// The same, against doors the caller supplies — the seam every test in this tier runs on.
///
/// # Errors
/// As [`main`] does.
pub fn main_with<I, T>(
    argv: I,
    outside: &Outside<'_>,
    out: &mut dyn Write,
    err: &mut dyn Write,
) -> Result<(), CliError>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
{
    let parsed = match Cli::try_parse_from(argv) {
        Ok(parsed) => parsed,
        Err(answer) if !answer.use_stderr() => {
            write!(out, "{answer}").map_err(|error| CliError::refusal(error.to_string()))?;
            return Ok(());
        }
        Err(refusal) => {
            return Err(CliError {
                code: u8::try_from(refusal.exit_code()).unwrap_or(2),
                message: refusal.to_string(),
            });
        }
    };
    run_with(parsed, outside, out, err)
}

/// Run one already-parsed command line.
///
/// # Errors
/// As [`main`] does.
pub fn run(cli: Cli, out: &mut dyn Write, err: &mut dyn Write) -> Result<(), CliError> {
    run_with(cli, &Outside::production(), out, err)
}

/// The same, against doors the caller supplies — the seam every test in this tier runs on.
///
/// # Errors
/// As [`main`] does.
pub fn run_with(
    cli: Cli,
    outside: &Outside<'_>,
    out: &mut dyn Write,
    err: &mut dyn Write,
) -> Result<(), CliError> {
    match cli.command {
        Command::Sessions {
            project,
            projects_root,
        } => Ok(sessions(&project, projects_root, out)?),
        Command::Extract {
            project,
            projects_root,
            db,
        } => Ok(extract(&project, projects_root, &db, out, err)?),
        Command::Query {
            name,
            db,
            project,
            since,
            as_of,
            param,
            csv,
        } => {
            let request = Request {
                project,
                since,
                as_of: as_of.unwrap_or_else(query::today),
                params: query::params(&param)?,
            };
            query::query(&db, &name, &request, csv, out, err)
        }
        Command::Enrich(args) => enrich::enrich(&args, outside.models, out),
        Command::ExportOtlp(args) => export::export_otlp(&args, &outside.environ, out),
        Command::View { db, port, dev } => Ok(view(&db, port, dev, out)?),
    }
}

/// `hp view`: the viewer over one store, until interrupted.
///
/// The runtime is built here rather than by a `#[tokio::main]` on `main`, so `hp extract` —
/// which is wholly synchronous — starts no reactor it never uses. Every refusal this can
/// answer with happens before the bind, which is what the process-level leaves check.
fn view(db: &Path, port: u16, dev: bool, _out: &mut dyn Write) -> Result<()> {
    let runtime = tokio::runtime::Runtime::new().context("starting the server runtime")?;
    runtime
        .block_on(hyphae_view::app::serve(db, port, dev))
        .map_err(|error| anyhow::anyhow!("{error}"))
}

/// `hp sessions`: a project's transcripts on disk, with the subagents each session spawned.
///
/// The one subcommand that opens no store — it walks the projects root instead, so it answers
/// before anything has been extracted.
fn sessions(project: &Path, projects_root: Option<PathBuf>, out: &mut dyn Write) -> Result<()> {
    let projects_root = projects_root.unwrap_or_else(sessions::default_projects_root);
    for session in sessions::find_sessions(project, &projects_root)? {
        let subagents = session.subagent_transcripts()?.len();
        writeln!(
            out,
            "{}\t{subagents} subagent(s)\t{}",
            session.id,
            session.transcript.display()
        )?;
    }
    Ok(())
}

/// The `refresh` loop of `src/hyphae/pipeline.py`: ask what is on disk, skip what the store
/// already holds at the same fingerprint, and replace the rest.
fn extract(
    project: &Path,
    projects_root: Option<PathBuf>,
    db: &Path,
    out: &mut dyn Write,
    _err: &mut dyn Write,
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
    writeln!(out, "{extracted} session(s) extracted, {skipped} unchanged")?;
    Ok(())
}
