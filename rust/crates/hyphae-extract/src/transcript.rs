//! What one transcript's lines say: the readers that turn records into entities.
//!
//! One thread at a time — the session's own transcript or a subagent's — and no knowledge of
//! which files make up a session ([`crate::session_files`]) or of how a refresh is driven
//! ([`crate::lib`]). [`parse`] is the entry: lines in, turns, api calls, tool calls and
//! compactions out.
//!
//! Ported guard for guard from `src/hyphae/extract/transcript.py`, which stays the authority.
//! Every field name these readers reach for is Claude Code's own, and the meaning of each is
//! declared on a model in `src/hyphae/extract/records/` (`docs/schema.md`).

use std::collections::HashMap;
use std::collections::HashSet;
use std::path::Path;
use std::sync::LazyLock;

use chrono::{DateTime, Utc};
use hyphae_model::{ApiCall, Compaction, MAIN_SOURCE, PrLink, RawRecord, Session, ToolCall, Turn};
use regex::Regex;
use serde_json::Value;

use crate::ExtractError;
use crate::record::{
    array, as_text, field, int, nullable_text, opt_text, parse_timestamp, text, truthy,
};
use crate::record_types::{
    ARCHIVE_RECORD_TYPES, CONTENT_BLOCKS, MACHINE_TAGS, RECORD_TYPES, SYSTEM_SUBTYPES, TURN_TAGS,
    block, record as record_type, system,
};

type Result<T> = std::result::Result<T, ExtractError>;

mod calls;

use calls::{api_calls, tool_calls};

// A leading tag, with or without attributes: `<teammate-message teammate_id="...">` names
// who sent it, so the name ends at whitespace as well as at the closing bracket. Python
// writes the bracket as a lookahead; the two character classes are disjoint, so consuming it
// picks the same name.
static LEADING_TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^<([A-Za-z0-9_-]+)[\s>]").expect("a literal pattern"));
static COMMAND_NAME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)<command-name>(.*?)</command-name>").expect("a literal"));
static COMMAND_ARGS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)<command-args>(.*?)</command-args>").expect("a literal"));

/// One line of a transcript, parsed but not yet interpreted.
#[derive(Debug, Clone)]
pub struct Line {
    pub line_no: i32,
    pub record: Value,
    pub raw: String,
}

/// What one transcript — the session's own or a subagent's — yielded.
pub struct Parsed {
    pub turns: Vec<Turn>,
    pub api_calls: Vec<ApiCall>,
    pub tool_calls: Vec<ToolCall>,
    pub compactions: Vec<Compaction>,
}

/// What a `tool_result` record said about the call it answered.
pub(super) struct ResultOf {
    /// `None` when the answer carried nothing readable — an encrypted server-side result.
    text: Option<String>,
    is_error: bool,
    offload_file: Option<String>,
    ended_at: Option<DateTime<Utc>>,
}

/// A user record that opens a turn.
struct Prompt {
    text: String,
    command_name: Option<String>,
    command_args: Option<String>,
}

/// Turns, calls and tools from one transcript, keyed to the source that wrote it.
///
/// `replayed` holds the line numbers this transcript copied from an earlier one; a row
/// opened by such a line is a replay of that transcript's work.
pub fn parse(
    lines: &[Line],
    session_id: &str,
    source: &str,
    replayed: &HashSet<i32>,
) -> Result<Parsed> {
    let (turns, turn_by_line) = self::turns(lines, session_id, source, replayed)?;
    Ok(Parsed {
        turns,
        api_calls: api_calls(lines, &turn_by_line, session_id, source, replayed)?,
        tool_calls: tool_calls(lines, session_id, source, replayed)?,
        compactions: compactions(lines, session_id, source)?,
    })
}

/// Reject a record type, `system` subtype or content block outside the registries.
pub fn check_type(record: &Value, session_id: &str, line_no: i32) -> Result<()> {
    let kind = text(record, "type")?;
    if kind == record_type::SYSTEM {
        let subtype = text(record, "subtype")?;
        if !SYSTEM_SUBTYPES.contains(&subtype) {
            return Err(ExtractError::Schema(format!(
                "Unknown system subtype {subtype:?} in session {session_id}, line {line_no}"
            )));
        }
        return Ok(());
    }
    if !RECORD_TYPES.contains(&kind) && !ARCHIVE_RECORD_TYPES.contains(&kind) {
        return Err(ExtractError::Schema(format!(
            "Unknown record type {kind:?} in session {session_id}, line {line_no}"
        )));
    }
    // A string content is a bare prompt, which has no blocks to register.
    let Some(Value::Array(blocks)) = message_content(record) else {
        return Ok(());
    };
    for one in blocks {
        let kind = text(one, "type")?;
        if !CONTENT_BLOCKS.contains(&kind) {
            return Err(ExtractError::Schema(format!(
                "Unknown content block {kind:?} in session {session_id}, line {line_no}"
            )));
        }
    }
    Ok(())
}

/// What the record said, if it said anything — the one field a duplicate may not change.
pub fn message_content(record: &Value) -> Option<&Value> {
    match record.get("message") {
        Some(message) if message.is_object() => message.get("content"),
        _ => None,
    }
}

/// A message's `content` list, which every assistant record carries.
pub(super) fn blocks_of(line: &Line) -> Result<&Vec<Value>> {
    array(field(&line.record, "message")?, "content")
}

pub fn raw_record(session_id: &str, source: &str, line: &Line) -> Result<RawRecord> {
    Ok(RawRecord {
        session_id: session_id.to_owned(),
        source: source.to_owned(),
        line_no: line.line_no,
        uuid: opt_text(&line.record, "uuid")?.map(str::to_owned),
        timestamp: timestamp(&line.record)?,
        r#type: text(&line.record, "type")?.to_owned(),
        raw: line.raw.clone(),
    })
}

/// A record's timestamp. Absent on the bookkeeping types, which carry no time at all.
pub fn timestamp(record: &Value) -> Result<Option<DateTime<Utc>>> {
    match opt_text(record, "timestamp")? {
        Some(moment) if !moment.is_empty() => parse_timestamp(moment).map(Some),
        _ => Ok(None),
    }
}

pub(super) fn required_timestamp(line: &Line, session_id: &str) -> Result<DateTime<Utc>> {
    timestamp(&line.record)?.ok_or_else(|| {
        ExtractError::Schema(format!(
            "Session {session_id}, line {}: a prompt record with no timestamp",
            line.line_no
        ))
    })
}

/// Session metadata, gathered from the records that carry it.
pub fn session(lines: &[Line], session_id: &str, transcript: &Path) -> Result<Session> {
    // The file opens on bookkeeping records with no `cwd`, so the first record that has
    // one is what says where this session ran.
    let context = lines
        .iter()
        .map(|line| &line.record)
        .find(|record| record.get("cwd").is_some());
    let mut moments = Vec::new();
    let mut active_ms = 0;
    for line in lines {
        if let Some(moment) = timestamp(&line.record)? {
            moments.push(moment);
        }
        if text(&line.record, "type")? == record_type::SYSTEM
            && text(&line.record, "subtype")? == system::TURN_DURATION
        {
            active_ms += int(&line.record, "durationMs")?;
        }
    }
    // All four are absent together, on a transcript holding only bookkeeping records. `cwd`
    // and `version` are keys the record must carry; the other two are keys it may not.
    let project_dir = context
        .map(|it| nullable_text(it, "cwd"))
        .transpose()?
        .flatten();
    let version = context
        .map(|it| nullable_text(it, "version"))
        .transpose()?
        .flatten();
    // Absent when the project is not a git repository.
    let git_branch = context
        .and_then(|it| opt_text(it, "gitBranch").transpose())
        .transpose()?;
    // Absent on sessions older than the field — the corpus has 1.0.128 sessions.
    let entrypoint = context
        .and_then(|it| opt_text(it, "entrypoint").transpose())
        .transpose()?;
    Ok(Session {
        id: session_id.to_owned(),
        project_dir: project_dir.map(str::to_owned),
        git_branch: git_branch.map(str::to_owned),
        version: version.map(str::to_owned),
        entrypoint: entrypoint.map(str::to_owned),
        started_at: moments.iter().min().copied(),
        ended_at: moments.iter().max().copied(),
        active_ms,
        transcript_path: transcript.to_string_lossy().into_owned(),
        // Claude Code appends a fresh title record on every rename, so the last one is the
        // name the session ended up with. Both spellings are current, and the operator's wins.
        title: last_field(lines, record_type::CUSTOM_TITLE, "customTitle")?
            .filter(|title| !title.is_empty())
            .or(last_field(lines, record_type::AI_TITLE, "aiTitle")?),
        agent_name: last_field(lines, record_type::AGENT_NAME, "agentName")?,
    })
}

/// The last value of a single-field record type, or None when the file holds none.
fn last_field(lines: &[Line], kind: &str, name: &str) -> Result<Option<String>> {
    let mut last = None;
    for line in lines {
        if text(&line.record, "type")? == kind {
            last = Some(text(&line.record, name)?.to_owned());
        }
    }
    Ok(last)
}

/// Every point this transcript summarised itself to free context.
///
/// Each boundary is written alongside the summary that replaced the history, so one row
/// here is one `isCompactSummary` user record in the same file.
fn compactions(lines: &[Line], session_id: &str, source: &str) -> Result<Vec<Compaction>> {
    let mut rows = Vec::new();
    for line in lines {
        if text(&line.record, "type")? != record_type::SYSTEM
            || text(&line.record, "subtype")? != system::COMPACT_BOUNDARY
        {
            continue;
        }
        let meta = field(&line.record, "compactMetadata")?;
        rows.push(Compaction {
            id: text(&line.record, "uuid")?.to_owned(),
            session_id: session_id.to_owned(),
            source: source.to_owned(),
            timestamp: required_timestamp(line, session_id)?,
            trigger: text(meta, "trigger")?.to_owned(),
            pre_tokens: int(meta, "preTokens")?,
            post_tokens: int(meta, "postTokens")?,
            duration_ms: int(meta, "durationMs")?,
        });
    }
    Ok(rows)
}

/// Every pull request the session recorded touching.
///
/// These records carry no uuid, and a session that pushes repeatedly links the same PR
/// once per push, so the line number is what separates two of them.
pub fn pr_links(lines: &[Line], session_id: &str) -> Result<Vec<PrLink>> {
    let mut rows = Vec::new();
    for line in lines {
        if text(&line.record, "type")? != record_type::PR_LINK {
            continue;
        }
        rows.push(PrLink {
            session_id: session_id.to_owned(),
            line_no: line.line_no,
            pr_number: int(&line.record, "prNumber")? as i32,
            pr_url: text(&line.record, "prUrl")?.to_owned(),
            pr_repository: text(&line.record, "prRepository")?.to_owned(),
            timestamp: required_timestamp(line, session_id)?,
        });
    }
    Ok(rows)
}

/// Which turn each line of a transcript belongs to, by line number. `None` for a line that
/// precedes the first prompt.
pub(super) type TurnByLine = HashMap<i32, Option<String>>;

/// The session's turns, and which turn each line belongs to.
///
/// A turn runs from its prompt until the next one, so its end is the latest timestamp
/// among the records in between — records are not written in timestamp order.
fn turns(
    lines: &[Line],
    session_id: &str,
    source: &str,
    replayed: &HashSet<i32>,
) -> Result<(Vec<Turn>, TurnByLine)> {
    let mut turn_by_line = HashMap::new();
    let mut open_turn: Option<String> = None;
    let mut spans: HashMap<String, Vec<DateTime<Utc>>> = HashMap::new();
    let mut turns: Vec<Turn> = Vec::new();
    for line in lines {
        if let Some(prompt) = self::prompt(line, session_id, source)? {
            let id = text(&line.record, "uuid")?.to_owned();
            spans.insert(id.clone(), Vec::new());
            let opened = required_timestamp(line, session_id)?;
            turns.push(Turn {
                id: id.clone(),
                session_id: session_id.to_owned(),
                source: source.to_owned(),
                index: turns.len() as i32,
                prompt: prompt.text,
                command_name: prompt.command_name,
                command_args: prompt.command_args,
                started_at: opened,
                // Replaced below, once the turn's span is known.
                ended_at: opened,
                replayed: replayed.contains(&line.line_no),
            });
            open_turn = Some(id);
        }
        turn_by_line.insert(line.line_no, open_turn.clone());
        if let (Some(open), Some(moment)) = (open_turn.as_ref(), timestamp(&line.record)?) {
            spans.entry(open.clone()).or_default().push(moment);
        }
    }
    // Every span holds at least the prompt's own timestamp, which the loop above required.
    for turn in &mut turns {
        turn.ended_at = spans[&turn.id]
            .iter()
            .max()
            .copied()
            .expect("the prompt's own moment");
    }
    Ok((turns, turn_by_line))
}

/// Whether this record opens a turn, and the prompt if it does.
///
/// The filters run before the tag registry: `isMeta` records carry tags of their own that
/// the registry deliberately does not list.
fn prompt(line: &Line, session_id: &str, source: &str) -> Result<Option<Prompt>> {
    let record = &line.record;
    if text(record, "type")? != record_type::USER {
        return Ok(None);
    }
    // These flags are absent on ordinary prompts — absence means "no". `isSidechain` marks
    // delegated work, and excludes it only in the main transcript, where the subagent's own
    // file states it better; inside that file every record is sidechain.
    if truthy(record, "isMeta") || truthy(record, "isCompactSummary") {
        return Ok(None);
    }
    if source == MAIN_SOURCE && truthy(record, "isSidechain") {
        return Ok(None);
    }
    let content = field(field(record, "message")?, "content")?;
    if let Value::Array(blocks) = content {
        return block_prompt(blocks);
    }
    let content = as_text(content, "content")?;
    let trimmed = content.trim();
    if trimmed.is_empty() {
        return Ok(None);
    }
    if !trimmed.starts_with('<') {
        return Ok(Some(Prompt {
            text: content.to_owned(),
            command_name: None,
            command_args: None,
        }));
    }
    let tag = match LEADING_TAG.captures(trimmed) {
        Some(found) => found[1].to_owned(),
        None => trimmed.chars().take(40).collect(),
    };
    if MACHINE_TAGS.contains(&tag.as_str()) {
        return Ok(None);
    }
    if !TURN_TAGS.contains(&tag.as_str()) {
        return Err(ExtractError::Schema(format!(
            "Unknown leading prompt tag <{tag}> in session {session_id}, line {}",
            line.line_no
        )));
    }
    Ok(Some(Prompt {
        text: content.to_owned(),
        command_name: captured(&COMMAND_NAME, content),
        command_args: captured(&COMMAND_ARGS, content),
    }))
}

/// A block-content user record is a prompt unless it is carrying a tool result back.
fn block_prompt(blocks: &[Value]) -> Result<Option<Prompt>> {
    let mut kinds = HashSet::new();
    for one in blocks {
        kinds.insert(text(one, "type")?);
    }
    if kinds.contains(block::TOOL_RESULT) {
        return Ok(None);
    }
    if !kinds.contains(block::TEXT) && !kinds.contains(block::IMAGE) {
        return Ok(None);
    }
    let mut said = String::new();
    for one in blocks {
        if text(one, "type")? == block::TEXT {
            said.push_str(text(one, "text")?);
        }
    }
    Ok(Some(Prompt {
        text: said,
        command_name: None,
        command_args: None,
    }))
}

fn captured(pattern: &Regex, content: &str) -> Option<String> {
    pattern
        .captures(content)
        .map(|found| found[1].trim().to_owned())
}
