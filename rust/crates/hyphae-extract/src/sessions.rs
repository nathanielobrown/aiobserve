//! Finding the Claude Code sessions recorded for a project.
//!
//! Claude Code writes one JSON-lines transcript per session, under a directory named for the
//! session's working directory:
//!
//! ```text
//! <projects_root>/<encoded-cwd>/<session-id>.jsonl
//! <projects_root>/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl
//! ```
//!
//! This module locates those files. It does not read them — parsing owns the records, and the
//! two rot on different schedules: the layout is stable, the record shapes are not.
//!
//! Ported from `src/hyphae/sessions.py`, minus the SQL half, which no Rust caller needs yet.

use std::path::{Path, PathBuf};

use crate::ExtractError;

type Result<T> = std::result::Result<T, ExtractError>;

// The names Claude Code gives the files inside a session's directory. Parsing reads these
// too — a file's place in the tree says which transcript it is — so they live here with the
// walk rather than being spelled out twice.
pub const TRANSCRIPT_SUFFIX: &str = ".jsonl";
pub const SUBAGENTS_DIR: &str = "subagents";
/// Under `subagents/`, one directory per parallel fan-out. At the top of the session
/// directory, the definitions and scripts of those workflows.
pub const WORKFLOWS_DIR: &str = "workflows";
pub const WORKFLOW_PREFIX: &str = "wf_";
pub const TOOL_RESULTS_DIR: &str = "tool-results";
pub const AGENT_PREFIX: &str = "agent-";
pub const META_SUFFIX: &str = ".meta.json";
pub const JOURNAL_NAME: &str = "journal.jsonl";

/// Where Claude Code keeps transcripts. The tree is shared across accounts, so a
/// transcript's path says nothing about which account produced it.
pub fn default_projects_root() -> PathBuf {
    let home = std::env::var("HOME").expect("HOME names the home directory");
    Path::new(&home).join(".claude").join("projects")
}

/// The absolute path everything that names a project matches on.
///
/// Claude Code records a session's `cwd` absolute and symlink-free (`docs/schema.md`), so a
/// project typed at a command line has to be resolved before it is compared against one.
pub fn resolve_project(project: &Path) -> PathBuf {
    let expanded = expanduser(project);
    let resolved = std::fs::canonicalize(&expanded)
        .unwrap_or_else(|_| std::path::absolute(&expanded).unwrap_or(expanded));
    actual_case(&resolved)
}

/// `~` and `~/…`, which reach us unexpanded when the path was quoted.
fn expanduser(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    let Some(rest) = text.strip_prefix('~') else {
        return path.to_owned();
    };
    if !rest.is_empty() && !rest.starts_with('/') {
        return path.to_owned();
    }
    let home = std::env::var("HOME").expect("HOME names the home directory");
    Path::new(&home).join(rest.trim_start_matches('/'))
}

/// The path as the filesystem spells it, not as it was typed.
///
/// macOS's default filesystem is case-insensitive but case-preserving: two spellings open the
/// same directory yet are different strings. Claude Code records the directory's real
/// spelling, so a typed path with the wrong case would resolve to real files on disk and
/// match nothing in the store — silently, since every read would find zero rows.
fn actual_case(path: &Path) -> PathBuf {
    let mut corrected = PathBuf::from("/");
    for part in path.strip_prefix("/").unwrap_or(path).components() {
        let part = part.as_os_str().to_string_lossy().into_owned();
        // Nothing on disk to correct against past this point — keep the rest as typed.
        let spelled = std::fs::read_dir(&corrected)
            .ok()
            .and_then(|entries| {
                entries.flatten().find(|entry| {
                    entry.file_name().to_string_lossy().to_lowercase() == part.to_lowercase()
                })
            })
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .unwrap_or(part);
        corrected.push(spelled);
    }
    corrected
}

/// Claude Code's directory name for a project: its absolute path, each `/` replaced by `-`.
///
/// So `/Users/nob/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The leading dash is the
/// encoded root separator, not a prefix.
pub fn encode_project_path(project: &Path) -> String {
    resolve_project(project).to_string_lossy().replace('/', "-")
}

/// One recorded Claude Code session, as the files that hold it.
#[derive(Debug, Clone)]
pub struct SessionFiles {
    /// The session UUID, taken from the transcript's filename.
    pub id: String,
    /// The session's own JSONL transcript. Subagent runs are NOT in here.
    pub transcript: PathBuf,
}

impl SessionFiles {
    /// Where Claude Code keeps everything else this session wrote. May not exist.
    pub fn directory(&self) -> PathBuf {
        self.transcript.with_extension("")
    }

    /// Every file this session's records live in, sorted by path.
    ///
    /// A whole-directory walk rather than a list of known names: subagent transcripts, their
    /// metas, workflow journals, and offloaded tool results all sit under here, and Claude
    /// Code adds shapes we have not seen. Missing one would leave a session looking
    /// unchanged after it changed.
    pub fn files(&self) -> Result<Vec<PathBuf>> {
        let mut walked = Vec::new();
        walk(&self.directory(), &mut walked)?;
        walked.sort();
        let mut files = vec![self.transcript.clone()];
        files.extend(walked);
        Ok(files)
    }
}

/// Every file under `directory`, however deep. A missing directory contributes nothing.
///
/// Symlinks are not followed into: `rglob` stopped following them too, and a session
/// directory holding one would otherwise be walked twice.
fn walk(directory: &Path, found: &mut Vec<PathBuf>) -> Result<()> {
    let entries = match std::fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(ExtractError::io(directory, error)),
    };
    for entry in entries {
        let entry = entry.map_err(|error| ExtractError::io(directory, error))?;
        let path = entry.path();
        let kind = entry
            .file_type()
            .map_err(|error| ExtractError::io(&path, error))?;
        if kind.is_dir() {
            walk(&path, found)?;
        } else {
            found.push(path);
        }
    }
    Ok(())
}

/// Every session recorded for `project`, sorted by session id.
///
/// Fails when the project has no directory under `projects_root` — that means it was never
/// opened in Claude Code, which is a typo in the path far more often than a real empty
/// corpus, and an empty list would hide it.
pub fn find_sessions(project: &Path, projects_root: &Path) -> Result<Vec<SessionFiles>> {
    let encoded = encode_project_path(project);
    let project_dir = projects_root.join(&encoded);
    if !project_dir.is_dir() {
        return Err(ExtractError::Schema(format!(
            "No Claude Code sessions for {} — expected {encoded:?} under {}",
            project.display(),
            projects_root.display()
        )));
    }
    // Non-recursive on purpose: the per-session subdirectories hold subagent runs and tool
    // results, which belong to a session rather than being one.
    let mut transcripts: Vec<PathBuf> = std::fs::read_dir(&project_dir)
        .map_err(|error| ExtractError::io(&project_dir, error))?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().ends_with(TRANSCRIPT_SUFFIX))
        })
        .collect();
    transcripts.sort();
    Ok(transcripts
        .into_iter()
        .map(|transcript| SessionFiles {
            id: transcript
                .file_stem()
                .expect("a name ending in .jsonl has a stem")
                .to_string_lossy()
                .into_owned(),
            transcript,
        })
        .collect())
}
