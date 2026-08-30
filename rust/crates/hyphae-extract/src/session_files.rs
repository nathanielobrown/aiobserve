//! Which files make up one session, and what the set of them says.
//!
//! A session is a transcript plus everything written beside it: each subagent's transcript
//! and meta pair, each workflow journal, and the files Claude Code offloaded a tool result
//! to. This module sorts that set, reads each file into lines, decides which lines are a
//! replay of another transcript, and builds the agent runs the pairs describe.
//!
//! Ported guard for guard from `src/hyphae/extract/session_files.py`, which stays the
//! authority. What those lines mean is [`crate::transcript`].

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use hyphae_model::{AgentRun, MAIN_SOURCE, OffloadFile};
use serde_json::Value;

use crate::record::{opt_int, opt_text, text, truthy};
use crate::record_types::{block, record as record_type};
use crate::sessions::{
    AGENT_PREFIX, JOURNAL_NAME, META_SUFFIX, SUBAGENTS_DIR, TOOL_RESULTS_DIR, TRANSCRIPT_SUFFIX,
    WORKFLOW_PREFIX, WORKFLOWS_DIR,
};
use crate::transcript::{Line, check_type, message_content, timestamp};
use crate::{ExtractError, SessionSource};

type Result<T> = std::result::Result<T, ExtractError>;

/// The `source` a workflow journal records under, after its `wf_<id>/` directory.
pub const JOURNAL_SOURCE: &str = "journal";

/// Where a run whose meta names no spawn depth sorts among its siblings: after every run
/// that does, since the depths Claude Code writes are small.
const UNKNOWN_DEPTH: i64 = 1_000_000;

/// One subagent's pair of files, and where the pair sat.
#[derive(Debug, Clone)]
pub struct AgentFiles {
    /// The agentId: the file stem after `agent-`, and the `source` its records take.
    pub id: String,
    /// The `wf_<id>` fan-out directory it sat in, for the runs a workflow drove.
    pub workflow_id: Option<String>,
    pub transcript: PathBuf,
    pub meta: PathBuf,
}

/// One session's files, sorted by what reads them.
pub struct ClassifiedFiles {
    pub transcript: PathBuf,
    pub agents: Vec<AgentFiles>,
    /// Each workflow journal, paired with its `wf_<id>/journal` source. Archive only: the
    /// runs it logs write their own transcripts.
    pub journals: Vec<(String, PathBuf)>,
    pub offloads: Vec<PathBuf>,
}

/// Which lines of each transcript an earlier one already held.
///
/// A fork copies its parent's records verbatim, uuids included, so a uuid inside one session
/// can name several files. It belongs to the first transcript in the order below; every later
/// copy is a replay. Any other transcript repeating another's records means the order put the
/// wrong file first, and stops the run.
pub fn replays(
    kept: &[(String, Vec<Line>)],
    metas: &HashMap<String, Value>,
    session_id: &str,
) -> Result<HashMap<String, HashSet<i32>>> {
    let at_name: HashMap<&str, &Vec<Line>> = kept
        .iter()
        .map(|(name, rows)| (name.as_str(), rows))
        .collect();
    let mut owner: HashMap<String, String> = HashMap::new();
    let mut replays: HashMap<String, HashSet<i32>> = HashMap::new();
    for name in transcript_order(kept, metas)? {
        let rows = at_name[name.as_str()];
        let mut copies = HashSet::new();
        for line in rows {
            if let Some(uuid) = opt_text(&line.record, "uuid")?
                && owner.contains_key(uuid)
            {
                copies.insert(line.line_no);
            }
        }
        if !copies.is_empty() && !is_fork(metas.get(&name)) {
            let first = *copies.iter().min().expect("a non-empty set");
            let uuid = rows
                .iter()
                .find(|line| line.line_no == first)
                .and_then(|line| line.record.get("uuid"))
                .and_then(Value::as_str)
                .expect("the line that put its uuid in the set");
            return Err(ExtractError::Schema(format!(
                "Session {session_id}: transcript {name} repeats record {uuid} from {} without \
                 being a fork",
                owner[uuid]
            )));
        }
        replays.insert(name.clone(), copies);
        for line in rows {
            if let Some(uuid) = opt_text(&line.record, "uuid")? {
                owner.entry(uuid.to_owned()).or_insert_with(|| name.clone());
            }
        }
    }
    Ok(replays)
}

/// The session's transcripts, first to record a uuid first.
///
/// Spawn depth leads, because a copied-history fork is spawned *by* the transcript it copies
/// and so always sits deeper. Time alone cannot separate them: the fork's opening record is
/// its parent's, timestamp and uuid alike, and 46 of the machine's 51 overlapping pairs tie
/// on it (scanned 2026-08-07). Ordering those ties by agentId instead hands 335 records of
/// six real transcripts' own work to a fork.
fn transcript_order(
    kept: &[(String, Vec<Line>)],
    metas: &HashMap<String, Value>,
) -> Result<Vec<String>> {
    // A meta that names no depth sorts last, and so does a transcript with no timestamps.
    let mut keyed: Vec<(i64, DateTime<Utc>, String)> = Vec::new();
    for (name, rows) in kept {
        if name == MAIN_SOURCE {
            continue;
        }
        let meta = metas.get(name).ok_or_else(|| {
            ExtractError::Schema(format!("transcript {name} has no meta beside it"))
        })?;
        let mut moments = Vec::new();
        for line in rows {
            if let Some(moment) = timestamp(&line.record)? {
                moments.push(moment);
            }
        }
        keyed.push((
            opt_int(meta, "spawnDepth")?.unwrap_or(UNKNOWN_DEPTH),
            moments
                .into_iter()
                .min()
                .unwrap_or(DateTime::<Utc>::MAX_UTC),
            name.clone(),
        ));
    }
    keyed.sort();
    let mut order = vec![MAIN_SOURCE.to_owned()];
    order.extend(keyed.into_iter().map(|(_, _, name)| name));
    Ok(order)
}

/// Whether a run continues another transcript's conversation.
///
/// `agentType: "fork"` agrees with the flag on all 52 fork metas on this machine (scanned
/// 2026-08-07), so the flag alone answers it.
fn is_fork(meta: Option<&Value>) -> bool {
    meta.is_some_and(|meta| truthy(meta, "isFork"))
}

/// Sort a session's files by what reads them.
///
/// The layout is closed-world like the record types: a file whose place we cannot name is a
/// Claude Code change we need to see, not a file to skip.
pub fn classify(source: &SessionSource) -> Result<ClassifiedFiles> {
    let transcript = transcript_of(source)?;
    let directory = transcript.with_extension("");
    // Each agent's two files arrive independently; they are paired once both are seen. The
    // transcripts keep their insertion order, which is what decides the row order downstream.
    let mut transcripts: Vec<(String, PathBuf)> = Vec::new();
    let mut at_agent: HashMap<String, usize> = HashMap::new();
    let mut metas: HashMap<String, PathBuf> = HashMap::new();
    let mut workflows: HashMap<String, Option<String>> = HashMap::new();
    let mut journals: Vec<(String, PathBuf)> = Vec::new();
    let mut offloads: Vec<PathBuf> = Vec::new();
    for path in &source.files {
        if *path == transcript {
            continue;
        }
        let parts: Vec<String> = path
            .strip_prefix(&directory)
            .map_err(|_| {
                ExtractError::Schema(format!(
                    "Session {}: {} sits outside the session directory",
                    source.id,
                    path.display()
                ))
            })?
            .components()
            .map(|part| part.as_os_str().to_string_lossy().into_owned())
            .collect();
        if parts.is_empty() {
            return Err(ExtractError::Schema(format!(
                "Session {}: {} sits outside the session directory",
                source.id,
                path.display()
            )));
        }
        if parts.len() == 2 && parts[0] == TOOL_RESULTS_DIR {
            offloads.push(path.clone());
            continue;
        }
        // A workflow's definition and the script that ran it, beside the runs they drove.
        if parts[0] == WORKFLOWS_DIR {
            continue;
        }
        let place = companion(&parts, &source.id)?;
        let Some(agent_id) = place.agent_id else {
            let workflow = place.workflow_id.expect("a journal names its workflow");
            journals.push((format!("{workflow}/{JOURNAL_SOURCE}"), path.clone()));
            continue;
        };
        if place.meta {
            metas.insert(agent_id.clone(), path.clone());
        } else {
            at_agent.insert(agent_id.clone(), transcripts.len());
            transcripts.push((agent_id.clone(), path.clone()));
        }
        workflows.insert(agent_id, place.workflow_id);
    }
    let transcript_ids: HashSet<&str> = at_agent.keys().map(String::as_str).collect();
    let meta_ids: HashSet<&str> = metas.keys().map(String::as_str).collect();
    if transcript_ids != meta_ids {
        let mut odd: Vec<&str> = transcript_ids
            .symmetric_difference(&meta_ids)
            .copied()
            .collect();
        odd.sort();
        return Err(ExtractError::Schema(format!(
            "Session {}: agent runs {odd:?} have a transcript or a meta, not both",
            source.id
        )));
    }
    let agents = transcripts
        .into_iter()
        .map(|(agent, path)| AgentFiles {
            workflow_id: workflows[&agent].clone(),
            meta: metas[&agent].clone(),
            transcript: path,
            id: agent,
        })
        .collect();
    Ok(ClassifiedFiles {
        transcript,
        agents,
        journals,
        offloads,
    })
}

/// Where one file under the session directory sits, and what it is.
struct Companion {
    /// The `wf_<id>` directory it sat in, when a fan-out wrote it.
    workflow_id: Option<String>,
    /// The agentId its name carries, or None for a workflow's journal.
    agent_id: Option<String>,
    /// The `.meta.json` beside a subagent's transcript rather than the transcript.
    meta: bool,
}

/// Place one file under `subagents/`. A file we cannot place stops the run.
///
/// The layout is closed-world like the record types: an unplaceable file is a Claude Code
/// change we need to see, not a file to skip.
fn companion(parts: &[String], session_id: &str) -> Result<Companion> {
    let unknown = || {
        ExtractError::Schema(format!(
            "Session {session_id}: unknown file {} in its directory",
            parts.join("/")
        ))
    };
    let mut workflow = None;
    if parts.len() >= 2 && parts[0] == SUBAGENTS_DIR && parts[1] == WORKFLOWS_DIR {
        if parts.len() != 4 || !parts[2].starts_with(WORKFLOW_PREFIX) {
            return Err(unknown());
        }
        workflow = Some(parts[2].clone());
    } else if parts[0] != SUBAGENTS_DIR || parts.len() != 2 {
        return Err(unknown());
    }
    let name = parts.last().expect("a path with at least one part");
    if workflow.is_some() && name == JOURNAL_NAME {
        return Ok(Companion {
            workflow_id: workflow,
            agent_id: None,
            meta: false,
        });
    }
    let Some(stem) = name.strip_prefix(AGENT_PREFIX) else {
        return Err(unknown());
    };
    // `.meta.json` first: it is the longer suffix, and both end in "json".
    if let Some(agent) = stem.strip_suffix(META_SUFFIX) {
        return Ok(Companion {
            workflow_id: workflow,
            agent_id: Some(agent.to_owned()),
            meta: true,
        });
    }
    if let Some(agent) = stem.strip_suffix(TRANSCRIPT_SUFFIX) {
        return Ok(Companion {
            workflow_id: workflow,
            agent_id: Some(agent.to_owned()),
            meta: false,
        });
    }
    Err(unknown())
}

/// One row per subagent the session ran, from the meta Claude Code wrote beside it.
pub fn agent_runs(
    agents: &[AgentFiles],
    kept: &[(String, Vec<Line>)],
    metas: &HashMap<String, Value>,
    replays: &HashMap<String, HashSet<i32>>,
    launches: &HashMap<String, String>,
    session_id: &str,
) -> Result<Vec<AgentRun>> {
    let at_name: HashMap<&str, &Vec<Line>> = kept
        .iter()
        .map(|(name, rows)| (name.as_str(), rows))
        .collect();
    let mut runs = Vec::with_capacity(agents.len());
    for agent in agents {
        let meta = &metas[&agent.id];
        // A fan-out's agents are not spawned one by one, so their metas name no call. The
        // call that launched the whole fan-out stands in — it is what asked for the work.
        let tool_use_id = opt_text(meta, "toolUseId")?
            .filter(|it| !it.is_empty())
            .map(str::to_owned)
            .or_else(|| {
                launches
                    .get(agent.workflow_id.as_deref().unwrap_or(""))
                    .cloned()
            });
        if tool_use_id.is_none() {
            // Real and expected: a teammate is started by the team mechanism, not by a tool
            // call. Said out loud because a silently dropped run hides a whole delegated
            // workload.
            eprintln!(
                "warning: session {session_id}: agent run {} has no spawning tool call",
                agent.id
            );
        }
        let lines = at_name[agent.id.as_str()];
        let replayed = &replays[&agent.id];
        let mut moments = Vec::new();
        // A fork's file opens with the conversation it inherited, so its own work starts
        // where the copying stops.
        let mut own = Vec::new();
        for line in lines {
            let Some(moment) = timestamp(&line.record)? else {
                continue;
            };
            moments.push(moment);
            if !replayed.contains(&line.line_no) {
                own.push(moment);
            }
        }
        runs.push(AgentRun {
            id: agent.id.clone(),
            session_id: session_id.to_owned(),
            // Absent for a run the session itself spawned.
            parent_agent_id: opt_text(meta, "parentAgentId")?.map(str::to_owned),
            tool_use_id,
            agent_type: text(meta, "agentType")?.to_owned(),
            // Both absent when the caller named none.
            brief: opt_text(meta, "description")?.map(str::to_owned),
            model: opt_text(meta, "model")?.map(str::to_owned),
            workflow_id: agent.workflow_id.clone(),
            // Absent on one meta of the 2764 on this machine, a 2.1.186 session.
            spawn_depth: opt_int(meta, "spawnDepth")?.map(|depth| depth as i32),
            is_fork: is_fork(Some(meta)),
            fork_context_uuid: fork_context(lines)?,
            started_at: own.into_iter().min(),
            ended_at: moments.into_iter().max(),
        });
    }
    Ok(runs)
}

/// The record a by-reference fork continues from, when its file opens on one.
///
/// Only that variant carries it: a fork that copied its history states the same thing by
/// holding the records themselves.
fn fork_context(lines: &[Line]) -> Result<Option<String>> {
    for line in lines {
        if text(&line.record, "type")? == record_type::FORK_CONTEXT_REF {
            return Ok(Some(text(&line.record, "parentLastUuid")?.to_owned()));
        }
    }
    Ok(None)
}

/// Which tool call launched each fan-out: `runId` from the result, to its call's id.
///
/// A `Workflow` call answers with the run it started, and the run id is the name of the
/// directory its agents write into — the only join between a fan-out's transcripts and the
/// call that asked for them.
pub fn workflow_launches(lines: &[Line]) -> Result<HashMap<String, String>> {
    let mut launches = HashMap::new();
    for line in lines {
        let Some(details) = line.record.get("toolUseResult").filter(|it| it.is_object()) else {
            continue;
        };
        let Some(run_id) = details.get("runId") else {
            continue;
        };
        let run_id = crate::record::as_text(run_id, "runId")?;
        for one in crate::record::array(crate::record::field(&line.record, "message")?, "content")?
        {
            if text(one, "type")? == block::TOOL_RESULT {
                launches.insert(run_id.to_owned(), text(one, "tool_use_id")?.to_owned());
            }
        }
    }
    Ok(launches)
}

/// One `tool-results/` file, read whole — it is the only copy once Claude Code prunes.
pub fn offload_file(path: &Path, session_id: &str) -> Result<OffloadFile> {
    let data = std::fs::read(path).map_err(|error| ExtractError::io(path, error))?;
    let size_bytes = data.len() as i64;
    let name = path
        .file_name()
        .expect("a file has a name")
        .to_string_lossy()
        .into_owned();
    // Not text at all — a fetched PDF — or text cut mid-character. Archived anyway: the file
    // is gone in a few weeks, and its size and name still say what ran.
    let (content, lossy_decode) = match String::from_utf8(data) {
        Ok(content) => (content, false),
        Err(error) => (String::from_utf8_lossy(error.as_bytes()).into_owned(), true),
    };
    Ok(OffloadFile {
        session_id: session_id.to_owned(),
        name,
        content,
        lossy_decode,
        size_bytes,
    })
}

/// Every line of a transcript, parsed as JSON.
///
/// Split on `\n` rather than by lines: real records contain U+2028 and U+2029 inside string
/// values, which a line iterator that honours them cuts records in half. The text is read
/// with the newline translation Python's text mode applies, so both readers see the same
/// bytes as the same lines.
///
/// A transcript read while Claude Code is writing it can end mid-record. That last line is
/// dropped with a warning, because the session is live rather than corrupt and the next
/// refresh will pick it up whole. Anywhere earlier, unparseable JSON is real damage and stops
/// the run.
pub fn read(path: &Path, session_id: &str) -> Result<Vec<Line>> {
    let text = std::fs::read_to_string(path).map_err(|error| ExtractError::io(path, error))?;
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let raws: Vec<&str> = text.split('\n').collect();
    let mut lines = Vec::new();
    for (at, raw) in raws.iter().enumerate() {
        let line_no = at as i32 + 1;
        if raw.trim().is_empty() {
            continue;
        }
        let record: Value = match serde_json::from_str(raw) {
            Ok(record) => record,
            Err(_) => {
                if raws[at + 1..].iter().any(|later| !later.trim().is_empty()) {
                    return Err(ExtractError::Schema(format!(
                        "Unparseable record in session {session_id}, line {line_no}"
                    )));
                }
                eprintln!(
                    "warning: session {session_id}: dropped an incomplete final line \
                     ({line_no}), still being written"
                );
                continue;
            }
        };
        check_type(&record, session_id, line_no)?;
        lines.push(Line {
            line_no,
            record,
            raw: (*raw).to_owned(),
        });
    }
    Ok(lines)
}

/// The session's own transcript, among the files discovery collected.
fn transcript_of(source: &SessionSource) -> Result<PathBuf> {
    let name = format!("{}{TRANSCRIPT_SUFFIX}", source.id);
    source
        .files
        .iter()
        .find(|path| path.file_name().is_some_and(|it| it == name.as_str()))
        .cloned()
        .ok_or_else(|| {
            ExtractError::Schema(format!("Session {}: no {name} among its files", source.id))
        })
}

/// Collapse repeated uuids to their last occurrence.
///
/// A rewind or an in-file fork rewrites a record's envelope under the uuid it already used.
/// The last write is the state the session continued from. A rewrite that changes what was
/// *said* is a different animal and stops the run.
pub fn resolve_duplicates(lines: Vec<Line>, session_id: &str) -> Result<Vec<Line>> {
    let mut last_at: HashMap<String, usize> = HashMap::new();
    for (index, line) in lines.iter().enumerate() {
        // Bookkeeping types carry no uuid — a documented absence, and nothing to dedup.
        let Some(uuid) = opt_text(&line.record, "uuid")? else {
            continue;
        };
        if let Some(&earlier) = last_at.get(uuid)
            && message_content(&lines[earlier].record) != message_content(&line.record)
        {
            return Err(ExtractError::Schema(format!(
                "Duplicate uuid {uuid} with differing message content in session {session_id}, \
                 lines {} and {}",
                lines[earlier].line_no, line.line_no
            )));
        }
        last_at.insert(uuid.to_owned(), index);
    }
    let survivors: HashSet<usize> = last_at.into_values().collect();
    Ok(lines
        .into_iter()
        .enumerate()
        .filter(|(index, line)| {
            line.record.get("uuid").is_none_or(Value::is_null) || survivors.contains(index)
        })
        .map(|(_, line)| line)
        .collect())
}
