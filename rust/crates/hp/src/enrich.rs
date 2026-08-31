//! `hp enrich`: describe the store's stale items, or say what a run would send and stop.
//!
//! The port of `cli.py:_enrich`, `_report_plan` and `build_client`. Python's tests reach the
//! two things that spend money by monkeypatching the module — `cli.preflight` and
//! `cli.build_client` — so the port names them together as [`Models`] and threads one
//! implementation through `run_with`. Production is [`ClaudeCli`], which starts real
//! processes; a test passes a fake and no process starts.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::PathBuf;

use hyphae_enrich::client::{BatchClient, CliClient};
use hyphae_enrich::cost::{Prompt, estimate};
use hyphae_enrich::prompts::thousands;
use hyphae_enrich::runner::ProcessRunner;
use hyphae_enrich::{EnrichmentStore, Level, PlannedItem, ROUND_ORDER, plan};
use hyphae_extract::sessions::resolve_project;

use crate::CliError;

/// The two doors `hp enrich` opens onto a real model: the auth question, and the client that
/// spends. One trait rather than two seams, because a leaf that cares about either usually
/// cares about the order they are opened in.
pub trait Models {
    /// Refuse the run now if the CLI cannot spend the subscription.
    ///
    /// # Errors
    /// When the CLI is missing, silent, logged out, or logged in with nothing behind it.
    fn preflight(&self) -> Result<(), CliError>;

    /// The client a round submits to.
    ///
    /// # Errors
    /// When the width it was given is one no pool can honour.
    fn client(&self, model: &str, concurrency: usize) -> Result<Box<dyn BatchClient>, CliError>;
}

/// The production seam: `claude auth status` once, then `claude -p`, that many at a time.
pub struct ClaudeCli;

impl Models for ClaudeCli {
    fn preflight(&self) -> Result<(), CliError> {
        hyphae_enrich::preflight(&ProcessRunner).map_err(CliError::refusal_from)
    }

    fn client(&self, model: &str, concurrency: usize) -> Result<Box<dyn BatchClient>, CliError> {
        let client = CliClient::new(model, concurrency, Box::new(ProcessRunner))
            .map_err(CliError::refusal_from)?;
        Ok(Box::new(client))
    }
}

/// What `hp enrich` takes, in the flag names and defaults `cli.py:_enrich_arguments` gives
/// them — declared here rather than beside the other subcommands so the six live next to the
/// one function that reads them.
#[derive(clap::Args, Debug)]
pub struct Args {
    /// The trace store
    #[arg(long, default_value = crate::DEFAULT_DB)]
    pub db: PathBuf,
    /// Only enrich the sessions recorded for this repository
    #[arg(long)]
    pub project: Option<PathBuf>,
    /// The model to describe with
    #[arg(long, default_value = hyphae_enrich::client::DEFAULT_MODEL)]
    pub model: String,
    /// Say what would be sent and stop, spending nothing (creates the empty enrichment tables
    /// if absent)
    #[arg(long)]
    pub dry_run: bool,
    /// Send at most this many items
    #[arg(long)]
    pub limit: Option<usize>,
    /// How many `claude` processes run at once. They spend the same 5-hour allowance this
    /// machine's own agents do
    #[arg(long, default_value_t = hyphae_enrich::client::DEFAULT_CONCURRENCY)]
    pub concurrency: usize,
}

/// One `hp enrich` run: a quote, or a pass that writes rows.
pub fn enrich(args: &Args, models: &dyn Models, out: &mut dyn Write) -> Result<(), CliError> {
    // Before anything reads the store or renders a prompt: a run whose CLI cannot spend the
    // subscription fails now instead of on its first item. A dry run asks nothing — whoever
    // decides whether to pay for a pass is not always whoever is logged in.
    if !args.dry_run {
        models.preflight()?;
    }
    let project = args
        .project
        .as_ref()
        .map(|named| resolve_project(named).to_string_lossy().into_owned());
    let store = EnrichmentStore::open(&args.db).map_err(CliError::refusal_from)?;
    if args.dry_run {
        let planned = plan(&store, &args.model, project.as_deref(), args.limit)
            .map_err(CliError::refusal_from)?;
        return report_plan(&planned, &args.model, out);
    }
    let client = models.client(&args.model, args.concurrency)?;
    let report = hyphae_enrich::enrich(&store, client.as_ref(), project.as_deref(), args.limit)
        .map_err(CliError::refusal_from)?;
    writeln!(
        out,
        "{} item(s) enriched, {} orphaned row(s) swept",
        report.enriched, report.swept
    )?;
    Ok(())
}

/// Say what a run would send and what it would cost, per level and in total.
///
/// Every count here is an upper bound: the plan holds each stale item and every item whose
/// prompt embeds one, and a child re-described in the same words stops that cascade.
fn report_plan(planned: &[PlannedItem], model: &str, out: &mut dyn Write) -> Result<(), CliError> {
    let prompts: Vec<Prompt> = planned
        .iter()
        .map(|entry| Prompt {
            level: entry.item.level(),
            content: entry.rendered.clone(),
        })
        .collect();
    let quote = estimate(&prompts, model).map_err(CliError::refusal_from)?;
    let mut counts: BTreeMap<Level, usize> = BTreeMap::new();
    for entry in planned {
        *counts.entry(entry.item.level()).or_default() += 1;
    }
    let breakdown = ROUND_ORDER
        .iter()
        .map(|level| format!("{} {}", counts.get(level).unwrap_or(&0), level.word()))
        .collect::<Vec<_>>()
        .join(", ");
    writeln!(
        out,
        "at most {} item(s) would be sent to {model} — {breakdown}",
        quote.items
    )?;
    writeln!(
        out,
        "at most ${:.2}: ~{} input and ~{} output tokens, counting no prompt caching",
        quote.usd,
        thousands(quote.input_tokens),
        thousands(quote.output_tokens)
    )?;
    Ok(())
}
