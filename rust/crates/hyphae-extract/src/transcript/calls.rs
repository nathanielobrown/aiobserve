//! Reassembling a message and its tools: the api-call half of the transcript walk.
//!
//! Split out of [`super`] for length alone — these readers are the second half of
//! `src/hyphae/extract/transcript.py`, and the module doc there covers both.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use chrono::{DateTime, Utc};
use hyphae_model::{ApiCall, ToolCall};
use serde_json::Value;

use super::{Line, Result, ResultOf, TurnByLine, blocks_of, required_timestamp, timestamp};
use crate::ExtractError;
use crate::pricing::{SYNTHETIC_MODEL, TokenUsage, compute_cost};
use crate::pyjson;
use crate::record::{as_text, field, int, nullable_text, opt, opt_text, text, truthy};
use crate::record_types::{ADVISOR_RESULTS, RESULT_BLOCKS, advisor, block, record as record_type};

/// One `ApiCall` per assistant message, however many records it was written across.
///
/// Claude Code writes one record per content block, all sharing `message.id` and chained
/// by `parentUuid`, interleaved with the tool results they triggered. Two thirds of the
/// messages in the corpus span several records, so grouping is not optional.
pub(super) fn api_calls(
    lines: &[Line],
    turn_by_line: &TurnByLine,
    session_id: &str,
    source: &str,
    replayed: &HashSet<i32>,
) -> Result<Vec<ApiCall>> {
    let mut at_uuid: HashMap<&str, &Line> = HashMap::new();
    for line in lines {
        if let Some(uuid) = opt_text(&line.record, "uuid")?
            && !uuid.is_empty()
        {
            at_uuid.insert(uuid, line);
        }
    }
    // The recorded order of first appearance, which is the order Python's dict keeps and
    // therefore what `index` counts along.
    let mut order: Vec<(&str, Vec<&Line>)> = Vec::new();
    let mut at_message: HashMap<&str, usize> = HashMap::new();
    for line in lines {
        if text(&line.record, "type")? != record_type::ASSISTANT {
            continue;
        }
        let message_id = text(field(&line.record, "message")?, "id")?;
        match at_message.get(message_id) {
            Some(&at) => order[at].1.push(line),
            None => {
                at_message.insert(message_id, order.len());
                order.push((message_id, vec![line]));
            }
        }
    }
    let mut calls = Vec::with_capacity(order.len());
    for (index, (message_id, group)) in order.into_iter().enumerate() {
        let (first, last) = (group[0], group[group.len() - 1]);
        // The chunks of one message repeat the same usage; the last is the file's final word.
        let message = field(&last.record, "message")?;
        let usage = field(message, "usage")?;
        // None when the record this call answers is not in this file — after a compaction,
        // or in a fork that opens mid-conversation.
        let answered = opt(&first.record, "parentUuid")
            .map(|parent| as_text(parent, "parentUuid"))
            .transpose()?
            .and_then(|parent| at_uuid.get(parent).copied());
        let split = opt(usage, "cache_creation");
        let tokens = TokenUsage {
            input: int(usage, "input_tokens")?,
            output: int(usage, "output_tokens")?,
            cache_read: int(usage, "cache_read_input_tokens")?,
            cache_creation: int(usage, "cache_creation_input_tokens")?,
            cache_5m: split
                .map(|it| int(it, "ephemeral_5m_input_tokens"))
                .transpose()?,
            cache_1h: split
                .map(|it| int(it, "ephemeral_1h_input_tokens"))
                .transpose()?,
        };
        let model = text(message, "model")?;
        calls.push(ApiCall {
            id: message_id.to_owned(),
            session_id: session_id.to_owned(),
            source: source.to_owned(),
            turn_id: turn_by_line[&first.line_no].clone(),
            index: index as i32,
            model: model.to_owned(),
            fallback_from: fallback_from(&group)?,
            effort: opt_text(&last.record, "effort")?.map(str::to_owned),
            stop_reason: nullable_text(message, "stop_reason")?.map(str::to_owned),
            // Present only while a skill was driving.
            attribution_skill: opt_text(&last.record, "attributionSkill")?.map(str::to_owned),
            request_id: opt_text(&last.record, "requestId")?.map(str::to_owned),
            started_at: required_timestamp(answered.unwrap_or(first), session_id)?,
            ended_at: required_timestamp(last, session_id)?,
            input_tokens: tokens.input,
            output_tokens: tokens.output,
            cache_read_tokens: tokens.cache_read,
            cache_creation_tokens: tokens.cache_creation,
            cache_5m_tokens: tokens.cache_5m,
            cache_1h_tokens: tokens.cache_1h,
            text: joined_blocks(&group, block::TEXT, "text")?,
            thinking: joined_blocks(&group, block::THINKING, "thinking")?,
            cost_usd: compute_cost(model, &tokens),
            synthetic: model == SYNTHETIC_MODEL,
            // The message belongs to whichever transcript wrote it first, so its first
            // chunk decides — a fork copies a message whole.
            replayed: replayed.contains(&first.line_no),
        });
    }
    Ok(calls)
}

/// The model this message was first asked of, when Claude Code retried on another.
///
/// A `fallback` block names both ends; the one that answered is already `message.model`,
/// which every recorded fallback agrees with, so only the model asked for first is new.
fn fallback_from(group: &[&Line]) -> Result<Option<String>> {
    for line in group {
        for one in blocks_of(line)? {
            if text(one, "type")? == block::FALLBACK {
                return Ok(Some(text(field(one, "from")?, "model")?.to_owned()));
            }
        }
    }
    Ok(None)
}

/// Every block of one kind across a message's records, concatenated in order.
///
/// `name` is the block's own key for the text it carries, which is not the kind: a
/// `thinking` block holds its words at `thinking`, a `text` block at `text`.
fn joined_blocks(group: &[&Line], kind: &str, name: &str) -> Result<String> {
    let mut joined = String::new();
    for line in group {
        for one in blocks_of(line)? {
            if text(one, "type")? == kind {
                joined.push_str(text(one, name)?);
            }
        }
    }
    Ok(joined)
}

/// Every tool the transcript asked for, paired with the record that answered it.
///
/// A message issuing several calls usually writes one record per call, in the order Claude
/// Code got round to running them — so the batch shares the earliest of those timestamps
/// and says the start is synthetic, rather than reporting an execution order as a duration.
///
/// A server-side call sits in that same stream but is not part of that batch: its record
/// is the request itself, so it keeps its own start.
pub(super) fn tool_calls(
    lines: &[Line],
    session_id: &str,
    source: &str,
    replayed: &HashSet<i32>,
) -> Result<Vec<ToolCall>> {
    let mut results = tool_results(lines, session_id)?;
    results.extend(advisor_results(lines, session_id)?);
    let mut issued: Vec<(&Line, &Value)> = Vec::new();
    for line in lines {
        if text(&line.record, "type")? != record_type::ASSISTANT {
            continue;
        }
        for one in blocks_of(line)? {
            let kind = text(one, "type")?;
            if kind == block::TOOL_USE || kind == block::SERVER_TOOL_USE {
                issued.push((line, one));
            }
        }
    }
    // A batch is the *records* a message issued its calls from, not the calls: several
    // `tool_use` blocks in one record were issued together and share that record's real
    // timestamp, so counting blocks would call a measured start synthetic (both shapes are
    // recorded — `docs/schema.md`, `tool_use block`).
    let mut batches: HashMap<&str, HashMap<i32, DateTime<Utc>>> = HashMap::new();
    for (line, one) in &issued {
        if text(one, "type")? == block::TOOL_USE {
            let message_id = text(field(&line.record, "message")?, "id")?;
            batches
                .entry(message_id)
                .or_default()
                .insert(line.line_no, required_timestamp(line, session_id)?);
        }
    }
    let mut calls = Vec::with_capacity(issued.len());
    for (index, (line, one)) in issued.iter().enumerate() {
        let server_side = text(one, "type")? == block::SERVER_TOOL_USE;
        let message_id = text(field(&line.record, "message")?, "id")?;
        let batch: Vec<DateTime<Utc>> = if server_side {
            Vec::new()
        } else {
            batches[message_id].values().copied().collect()
        };
        let result = results.get(text(one, "id")?);
        calls.push(ToolCall {
            id: text(one, "id")?.to_owned(),
            session_id: session_id.to_owned(),
            source: source.to_owned(),
            api_call_id: message_id.to_owned(),
            index: index as i32,
            name: text(one, "name")?.to_owned(),
            server_side,
            input: pyjson::dumps(field(one, "input")?),
            result: result.and_then(|it| it.text.clone()),
            offload_file: result.and_then(|it| it.offload_file.clone()),
            is_error: result.is_some_and(|it| it.is_error),
            incomplete: result.is_none(),
            started_at: if server_side {
                required_timestamp(line, session_id)?
            } else {
                batch
                    .iter()
                    .min()
                    .copied()
                    .expect("the issuing record's own moment")
            },
            ended_at: result.and_then(|it| it.ended_at),
            duration_synthetic: batch.len() > 1,
            // The issuing record decides: a fork copies the call and its answer together.
            replayed: replayed.contains(&line.line_no),
        });
    }
    Ok(calls)
}

/// What each tool said back, keyed by the call it answered.
///
/// A rewind can record an answer twice under one call id; the later record wins, as it
/// does everywhere else in the file.
fn tool_results(lines: &[Line], session_id: &str) -> Result<HashMap<String, ResultOf>> {
    let mut results = HashMap::new();
    for line in lines {
        let record = &line.record;
        if text(record, "type")? != record_type::USER {
            continue;
        }
        let Value::Array(blocks) = field(field(record, "message")?, "content")? else {
            continue;
        };
        // Present on tool-result records only, and a string on a few older ones, which
        // carry no offload pointer.
        let path = match record.get("toolUseResult") {
            Some(details) if details.is_object() => opt_text(details, "persistedOutputPath")?,
            _ => None,
        };
        let ended_at = timestamp(record)?;
        for one in blocks {
            if text(one, "type")? != block::TOOL_RESULT {
                continue;
            }
            results.insert(
                text(one, "tool_use_id")?.to_owned(),
                ResultOf {
                    text: Some(result_text(
                        field(one, "content")?,
                        session_id,
                        line.line_no,
                    )?),
                    // Absent on most results — absence means the tool succeeded.
                    is_error: truthy(one, "is_error"),
                    offload_file: path
                        .and_then(|it| Path::new(it).file_name())
                        .map(|it| it.to_string_lossy().into_owned()),
                    ended_at,
                },
            );
        }
    }
    Ok(results)
}

/// What each server-side tool said back, keyed by the call it answered.
///
/// Unlike a local tool, the answer rides inside the same assistant message as the request,
/// and it is never readable: a refusal names its error code, and a completed call comes
/// back encrypted. So the row records that the advisor answered, and what it cost in time.
fn advisor_results(lines: &[Line], session_id: &str) -> Result<HashMap<String, ResultOf>> {
    let mut results = HashMap::new();
    for line in lines {
        if text(&line.record, "type")? != record_type::ASSISTANT {
            continue;
        }
        for one in blocks_of(line)? {
            if text(one, "type")? != block::ADVISOR_TOOL_RESULT {
                continue;
            }
            let content = field(one, "content")?;
            let kind = text(content, "type")?;
            if !ADVISOR_RESULTS.contains(&kind) {
                return Err(ExtractError::Schema(format!(
                    "Unknown advisor result {kind:?} in session {session_id}, line {}",
                    line.line_no
                )));
            }
            let error = kind == advisor::ERROR;
            results.insert(
                text(one, "tool_use_id")?.to_owned(),
                ResultOf {
                    text: if error {
                        Some(text(content, "error_code")?.to_owned())
                    } else {
                        None
                    },
                    is_error: error,
                    offload_file: None,
                    ended_at: timestamp(&line.record)?,
                },
            );
        }
    }
    Ok(results)
}

/// A result flattened to text, whether it was recorded as a string or as blocks.
fn result_text(content: &Value, session_id: &str, line_no: i32) -> Result<String> {
    if let Value::String(said) = content {
        return Ok(said.clone());
    }
    let mut parts = String::new();
    for one in content.as_array().ok_or_else(|| {
        ExtractError::Schema(format!(
            "Session {session_id}, line {line_no}: a tool result that is neither text nor blocks"
        ))
    })? {
        let kind = text(one, "type")?;
        if !RESULT_BLOCKS.contains(&kind) {
            return Err(ExtractError::Schema(format!(
                "Unknown tool result block {kind:?} in session {session_id}, line {line_no}"
            )));
        }
        if kind == block::TEXT {
            parts.push_str(text(one, "text")?);
        }
    }
    Ok(parts)
}
