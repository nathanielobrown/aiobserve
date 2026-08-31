//! The Claude Code extractor: which sessions a project has, and what each one holds.
//!
//! Assembly. It finds a project's sessions, sorts each one's files ([`session_files`]), reads
//! every transcript's lines into entities ([`transcript`]), and stamps the result with a
//! fingerprint that decides re-extraction.
//!
//! The reader below it is closed-world on purpose: every record type, every `system` subtype
//! and every tag a prompt can lead with is registered in [`record_types`], and anything else
//! stops the run. What each field means, and the session that proves it, is `docs/schema.md`.
//!
//! Ported from `src/hyphae/extract/claude_code.py`, which stays the authority.

pub mod pricing;
pub mod pyjson;
pub mod record;
pub mod record_types;
pub mod session_files;
pub mod sessions;
pub mod transcript;

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use hyphae_model::{MAIN_SOURCE, SessionTrace};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::transcript::Line;

type Result<T> = std::result::Result<T, ExtractError>;

pub const EXTRACTOR_NAME: &str = "claude_code";

/// This extractor's own version, deliberately not the Python one.
///
/// The version is folded into every fingerprint, so two extractors sharing a string would
/// each read the other's rows as current and neither would ever re-extract what the other
/// wrote. A distinct string makes the Rust extractor rewrite every session it meets, which is
/// what the prototype wants while the two implementations are being compared. It also means
/// `extract_state.fingerprint` differs from Python's by construction — the parity diff
/// excludes that column and says so.
pub const EXTRACTOR_VERSION: &str = "rust-1";

/// What went wrong reading a session.
///
/// `Schema` is `TranscriptSchemaError`: Claude Code wrote a shape this reader does not know,
/// and the run stops rather than guessing. It never carries a record's content — transcripts
/// are private and these messages reach logs — only ids, keys and line numbers.
#[derive(Debug, thiserror::Error)]
pub enum ExtractError {
    #[error("{0}")]
    Schema(String),
    #[error("{path}: {source}")]
    Io {
        path: String,
        source: std::io::Error,
    },
}

impl ExtractError {
    pub fn io(path: &Path, source: std::io::Error) -> Self {
        ExtractError::Io {
            path: path.display().to_string(),
            source,
        }
    }
}

/// One session as discovery found it: what to read, and what state it was in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionSource {
    pub id: String,
    /// Every file the session's records live in — the transcript, its subagent transcripts
    /// and their metas, workflow journals, and offloaded tool results.
    pub files: Vec<PathBuf>,
    /// Changes whenever any of those files does. Comparing it against the sink's copy is the
    /// only thing that decides whether a session is re-extracted.
    pub fingerprint: String,
}

/// Discovers and parses Claude Code sessions for one project.
pub struct Extractor {
    projects_root: PathBuf,
}

impl Extractor {
    /// No default root: the caller decides, since a test points this at a fixture tree and
    /// the CLI at [`sessions::default_projects_root`].
    pub fn new(projects_root: PathBuf) -> Self {
        Extractor { projects_root }
    }

    /// Every session recorded for `project`, with the fingerprint of its files.
    pub fn sessions(&self, project: &Path) -> Result<Vec<SessionSource>> {
        let project_dir = self
            .projects_root
            .join(sessions::encode_project_path(project));
        let mut sources = Vec::new();
        for session in sessions::find_sessions(project, &self.projects_root)? {
            let files = session.files()?;
            sources.push(SessionSource {
                id: session.id,
                fingerprint: fingerprint(&files, &project_dir, EXTRACTOR_VERSION)?,
                files,
            });
        }
        Ok(sources)
    }

    /// Parse every file of one session into a trace.
    ///
    /// Every transcript the session wrote — its own and each subagent's — runs through the
    /// same parser, distinguished only by the `source` its rows carry.
    pub fn extract(&self, source: &SessionSource) -> Result<SessionTrace> {
        let files = session_files::classify(source)?;
        let mut transcripts = vec![(MAIN_SOURCE.to_owned(), files.transcript.clone())];
        transcripts.extend(
            files
                .agents
                .iter()
                .map(|agent| (agent.id.clone(), agent.transcript.clone())),
        );
        let mut lines: Vec<(String, Vec<Line>)> = Vec::new();
        for (name, path) in &transcripts {
            lines.push((name.clone(), session_files::read(path, &source.id)?));
        }
        let mut journals: Vec<(String, Vec<Line>)> = Vec::new();
        for (name, path) in &files.journals {
            journals.push((name.clone(), session_files::read(path, &source.id)?));
        }
        let mut metas: HashMap<String, Value> = HashMap::new();
        for agent in &files.agents {
            let text = std::fs::read_to_string(&agent.meta)
                .map_err(|error| ExtractError::io(&agent.meta, error))?;
            let meta: Value = serde_json::from_str(&text).map_err(|error| {
                ExtractError::Schema(format!(
                    "Session {}: unparseable meta for agent run {}: {error}",
                    source.id, agent.id
                ))
            })?;
            metas.insert(agent.id.clone(), meta);
        }
        // The archive keeps every line of every file, duplicates included; the normalized
        // tables below read each transcript's deduplicated view.
        let mut raw_records = Vec::new();
        for (name, rows) in lines.iter().chain(journals.iter()) {
            for line in rows {
                raw_records.push(transcript::raw_record(&source.id, name, line)?);
            }
        }
        let mut kept: Vec<(String, Vec<Line>)> = Vec::with_capacity(lines.len());
        for (name, rows) in lines {
            kept.push((name, session_files::resolve_duplicates(rows, &source.id)?));
        }
        let replays = session_files::replays(&kept, &metas, &source.id)?;
        let mut parsed = Vec::with_capacity(kept.len());
        for (name, rows) in &kept {
            parsed.push(transcript::parse(rows, &source.id, name, &replays[name])?);
        }
        let main = &kept[0].1;
        let launches = session_files::workflow_launches(main)?;
        let mut offload_files = Vec::with_capacity(files.offloads.len());
        for path in &files.offloads {
            offload_files.push(session_files::offload_file(path, &source.id)?);
        }
        Ok(SessionTrace {
            extractor: EXTRACTOR_NAME.to_owned(),
            extractor_version: EXTRACTOR_VERSION.to_owned(),
            session: transcript::session(main, &source.id, &files.transcript)?,
            turns: parsed.iter().flat_map(|one| one.turns.clone()).collect(),
            api_calls: parsed
                .iter()
                .flat_map(|one| one.api_calls.clone())
                .collect(),
            tool_calls: parsed
                .iter()
                .flat_map(|one| one.tool_calls.clone())
                .collect(),
            agent_runs: session_files::agent_runs(
                &files.agents,
                &kept,
                &metas,
                &replays,
                &launches,
                &source.id,
            )?,
            compactions: parsed
                .iter()
                .flat_map(|one| one.compactions.clone())
                .collect(),
            // Main-transcript only: no subagent in the corpus records one (2026-08-07).
            pr_links: transcript::pr_links(main, &source.id)?,
            offload_files,
            raw_records,
        })
    }
}

/// A session's state, as one digest over the files that hold it.
///
/// Covers every file, not just the main transcript: a subagent transcript or an offloaded
/// tool result changes without the transcript changing. Folds in the extractor version so a
/// parser upgrade re-extracts the corpus rather than leaving old rows parsed by old logic.
/// Uses mtime, so copying the tree re-extracts everything — idempotent, just slow.
///
/// `version` is a parameter with no default so a test can hand it the Python string and
/// compare digests over one tree; [`Extractor`] passes [`EXTRACTOR_VERSION`].
///
/// The sort is by path components, not by the path spelled as a string, because that is what
/// `sorted()` over `Path` objects does — the two disagree wherever a separator meets a
/// character that sorts below it.
pub fn fingerprint(files: &[PathBuf], relative_to: &Path, version: &str) -> Result<String> {
    let mut sorted: Vec<&PathBuf> = files.iter().collect();
    sorted.sort();
    let mut digest = Sha256::new();
    digest.update(version.as_bytes());
    for path in sorted {
        let stat = std::fs::metadata(path).map_err(|error| ExtractError::io(path, error))?;
        let relative = path.strip_prefix(relative_to).map_err(|_| {
            ExtractError::Schema(format!(
                "{} is not under {}",
                path.display(),
                relative_to.display()
            ))
        })?;
        let mtime_ns = {
            use std::os::unix::fs::MetadataExt;
            stat.mtime() as i128 * 1_000_000_000 + i128::from(stat.mtime_nsec())
        };
        digest.update(format!("{}\0{}\0{}\0", relative.display(), stat.len(), mtime_ns).as_bytes());
    }
    Ok(format!("{:x}", digest.finalize()))
}
