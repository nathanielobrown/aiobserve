//! `hp export-otlp`: ship a project's sessions to an OTLP backend, or count what a send would.
//!
//! The port of `cli.py:_export_otlp`. Everything that can refuse a run refuses before the
//! store is opened, so a misconfigured operator pays no read and takes no write lock.
//!
//! The environment arrives as a map rather than being read here, so a test can hand over its
//! own — see [`crate::Outside`].

use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;

use clap::Args as ClapArgs;
use hyphae_export::census::Census;
use hyphae_export::delivery::{
    Clock, DEFAULT_RATE, GENERIC, OtlpExporter, Shipping, SystemClock, backend_names,
    named_backend, refresh,
};
use hyphae_export::otlp::{DEFAULT_MAX_CHARS, TextPolicy};
use hyphae_store::Store;
use hyphae_store::source::StoreSource;

use crate::{CliError, DEFAULT_DB};

/// What `hp export-otlp` takes. Every flag that shapes or paces a send is here, and
/// [`shipping`] is the one place they become what the exporter reads.
#[derive(ClapArgs, Debug, PartialEq)]
pub struct Args {
    /// Path to the analyzed repository
    pub project: PathBuf,
    /// Which trace store to read
    #[arg(long, default_value = DEFAULT_DB)]
    pub db: PathBuf,
    /// Where to ship, and whose delivery ledger this run reads and writes. A named backend
    /// reads its own key variable, and OTLP_ENDPOINT overrides its endpoint
    #[arg(long, default_value = GENERIC, value_parser = clap::builder::PossibleValuesParser::new(backend_names()))]
    pub backend: String,
    /// Send every session to this service instead of one named for its project directory
    #[arg(long)]
    pub service_name: Option<String>,
    /// Spans per second, across the whole run
    #[arg(long, default_value_t = DEFAULT_RATE)]
    pub rate: f64,
    /// Also send prompts, model text, tool arguments and results — untrusted transcript
    /// content, published to a third party
    #[arg(long)]
    pub include_text: bool,
    /// Characters kept per included text field
    #[arg(long, default_value_t = DEFAULT_MAX_CHARS)]
    pub max_chars: usize,
    /// Count what a send would ship and send nothing. Needs no backend and no key
    #[arg(long)]
    pub dry_run: bool,
}

/// What the flags ask the exporter for.
///
/// Public and separate from the run so a test can read the whole set at once: Python's twin
/// captures the keyword arguments the CLI hands `OtlpExporter`, and a flag the wiring drops
/// has to fail somewhere.
pub fn shipping<'a>(args: &Args, clock: &'a dyn Clock) -> Shipping<'a> {
    Shipping {
        service_name: args.service_name.clone(),
        text: TextPolicy {
            include: args.include_text,
            max_chars: args.max_chars,
        },
        rate: args.rate,
        ..Shipping::new(clock)
    }
}

/// Ship every session of a project that this backend has not already confirmed.
///
/// # Errors
/// A backend with no endpoint or no key, a project the store holds nothing under, a store
/// another writer holds, or anything the delivery itself refused.
pub fn export_otlp(
    args: &Args,
    environ: &HashMap<String, String>,
    out: &mut dyn Write,
) -> Result<(), CliError> {
    if args.dry_run {
        let counted = counted(args)?;
        // The compaction count is broken out because it is the one number the store cannot be
        // queried for: `live_compactions` keeps the copies a fork inherited and the mapper's
        // replay rule drops them.
        writeln!(
            out,
            "{} session(s) and {} span(s) would ship, {} of them compactions — nothing sent",
            counted.sessions, counted.spans, counted.compactions
        )?;
        return Ok(());
    }
    // Before the store is opened: a run with nowhere to ship refuses now rather than after
    // reading a corpus.
    let backend = named_backend(&args.backend, environ).map_err(CliError::refusal_from)?;
    let clock = SystemClock::new();
    // One store for both halves — DuckDB admits a single writer, and the exporter needs to
    // write its ledger into the store the source is reading.
    let store = Store::open_for_write(&args.db).map_err(CliError::refusal_from)?;
    let exporter = OtlpExporter::new(backend, &store, shipping(args, &clock))
        .map_err(CliError::refusal_from)?;
    let result = refresh(&args.project, &StoreSource::new(&store), &exporter)
        .map_err(CliError::refusal_from)?;
    writeln!(
        out,
        "{} session(s) exported, {} unchanged",
        result.extracted.len(),
        result.skipped.len()
    )?;
    Ok(())
}

/// Say what a send would ship, without a backend, a key, or the store's write lock.
fn counted(args: &Args) -> Result<Census, CliError> {
    let store = Store::open_read_only(&args.db).map_err(CliError::refusal_from)?;
    let source = StoreSource::new(&store);
    let mut counted = Census::default();
    let text = TextPolicy {
        include: args.include_text,
        max_chars: args.max_chars,
    };
    // Folded one session at a time, so counting a corpus never holds every trace at once.
    for session in source
        .sessions(&args.project)
        .map_err(CliError::refusal_from)?
    {
        counted
            .add(
                &source.extract(&session).map_err(CliError::refusal_from)?,
                &text,
            )
            .map_err(CliError::refusal_from)?;
    }
    Ok(counted)
}
