//! What each level sends the model: the rows it is built from, and the text they render to.
//!
//! Ported from `src/hyphae/enrich/prompts.py`; the row shapes are in [`crate::items`]. The
//! renders are pure — rows in, prompt text out — so their evidence is a real store built from
//! the recorded fixtures rather than a client and a network. Every size limit is a field on
//! [`Budgets`] rather than a constant, because a redacted fixture is two orders of magnitude
//! short of the real budgets and elision could not otherwise be tested at all.
//!
//! Every length here counts characters, not bytes, because the Python side counts code points
//! and the two renders have to hash alike.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::items::{AgentRunItem, ApiCallRow, SessionChild, SessionItem, ToolCallRow, TurnItem};
use crate::schema::Level;
use crate::taxonomy::{self, definition};

/// The output contract itself: passed to `--json-schema`, so the model cannot answer out of
/// vocabulary in the first place. An edit here is a `prompt_version` bump, since `input_hash`
/// cannot see it.
pub static OUTPUT_SCHEMA: LazyLock<Value> = LazyLock::new(|| {
    let vocabulary = taxonomy::enrichment();
    json!({
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "One or two sentences"},
            "category": {"type": "string", "enum": vocabulary.categories},
            "outcome": {"type": "string", "enum": vocabulary.outcomes},
            "friction": {
                "type": ["string", "null"],
                "description": "One line naming visible struggle, or null when there was none",
            },
        },
        // `friction` included: the model must decide there was none, not forget to say.
        "required": ["description", "category", "outcome", "friction"],
    })
});

/// The system prompt for one level. Versioned by [`Level::prompt_version`], not by
/// [`input_hash`].
///
/// Composed here from the material the generation bridge carries, rather than carried whole:
/// this side owns the order the blocks read in, so this side's leaves can hold it.
pub fn instructions(level: Level) -> String {
    let held = taxonomy::enrichment();
    let text = &held.prompt_text;
    let mut vocabulary = vec!["Categories:".to_owned()];
    vocabulary.extend(held.categories.iter().map(|member| {
        format!(
            "- {member}: {}",
            definition(&held.category_definitions, member)
        )
    }));
    vocabulary.push(String::new());
    vocabulary.push("Outcomes:".to_owned());
    vocabulary.extend(held.outcomes.iter().map(|member| {
        format!(
            "- {member}: {}",
            definition(&held.outcome_definitions, member)
        )
    }));
    let mut parts = vec![
        held.subject(level).to_owned(),
        text.answer.clone(),
        vocabulary.join("\n"),
        text.choosing.clone(),
    ];
    if level == Level::Session {
        parts.push(text.relaying.clone());
    }
    parts.join("\n\n")
}

/// Every size limit one render obeys, in characters.
///
/// Passed rather than read from a constant so the elision paths can be exercised: every string
/// in a redacted fixture is ten characters long, so no recorded row comes within two orders of
/// magnitude of `total`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Budgets {
    /// The whole rendered prompt. Differs per level, so there is no sensible default.
    pub total: usize,
    pub prompt: usize,
    /// The assistant's text per api call. Enough for the narration, not for a file dump.
    pub text: usize,
    /// The head of a tool's input — the file read, the command run, the URL fetched.
    pub input_head: usize,
    /// The tail of a *failed* tool result. No other result content travels at all.
    pub error_tail: usize,
    /// A slash command's own printed output — for most command turns, the whole of what
    /// happened. 315 of the 316 recorded bodies fit it; the median is 71 characters.
    pub command_result: usize,
}

impl Budgets {
    /// The defaults every level shares, around the one limit that differs.
    pub const fn new(total: usize) -> Self {
        Self {
            total,
            prompt: 4_000,
            text: 1_500,
            input_head: 120,
            error_tail: 300,
            command_result: 2_000,
        }
    }
}

pub const TURN_BUDGETS: Budgets = Budgets::new(30_000);
/// The same cap: a run holds the same kind of work a main turn does, and 209 of 2,458 recorded
/// runs reach it.
pub const RUN_BUDGETS: Budgets = Budgets::new(30_000);
/// Smaller: a session carries one line per child rather than a transcript. Sessions average 3.1
/// children and the longest recorded one has 92.
pub const SESSION_BUDGETS: Budgets = Budgets::new(24_000);

/// The staleness hash: the rendered content and nothing else.
///
/// Not the instructions and not the output schema — the prompt version covers those, so an
/// instruction edit does not have to pretend the content changed.
pub fn input_hash(rendered: &str) -> String {
    format!("{:x}", Sha256::digest(rendered.as_bytes()))
}

/// One main turn as the model sees it: what was asked, and what the session then did.
pub fn render_turn(item: &TurnItem, budgets: &Budgets) -> String {
    let mut head = vec!["# Main turn".to_owned(), String::new()];
    if let Some(name) = &item.command_name {
        // The `prompt` column keeps the command tags; forwarding it would spend budget on
        // markup and read as content.
        let named = [Some(name.as_str()), item.command_args.as_deref()]
            .into_iter()
            .flatten()
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        head.push("## Command".to_owned());
        head.push(named);
        head.push(String::new());
        head.push(command_result_block(
            item.command_result.as_deref(),
            budgets,
        ));
    } else {
        head.push("## Prompt".to_owned());
        head.push(cap(&item.prompt, budgets.prompt));
    }
    let mut lines = Vec::new();
    for call in &item.api_calls {
        push_response(&mut lines, call, budgets);
    }
    // In the elidable sequence, not the head: `fit` protects both of its ends, so the line
    // survives elision without spending head budget a long turn needs.
    lines.push(String::new());
    lines.push(ended_line(&item.api_calls));
    fit(&head.join("\n"), &lines, budgets.total)
}

/// One agent run as the model sees it: every instruction it got, and the work each drove.
///
/// The title and the run's first section survive any budget; the sequence after them elides.
pub fn render_run(item: &AgentRunItem, budgets: &Budgets) -> String {
    let mut head = vec![format!("# Agent run: {}", item.agent_type)];
    let mut lines: Vec<String> = Vec::new();
    let mut task_seen = false;
    for (index, section) in item.sections.iter().enumerate() {
        let opening = match &section.prompt {
            None => vec!["## Continuation".to_owned(), CONTINUATION.to_owned()],
            Some(prompt) => {
                let label = if task_seen {
                    "## Instruction"
                } else {
                    "## Task"
                };
                task_seen = true;
                vec![
                    label.to_owned(),
                    cap(unwrap_teammate(prompt), budgets.prompt),
                ]
            }
        };
        // Only the opening of the first section is protected from elision; its calls, and
        // everything after it, are the sequence `fit` trims.
        let into = if index == 0 { &mut head } else { &mut lines };
        into.push(String::new());
        into.extend(opening);
        for call in &section.api_calls {
            push_response(&mut lines, call, budgets);
        }
    }
    // Once, after the last section — the run's last call, wherever it sat.
    let every: Vec<ApiCallRow> = item
        .sections
        .iter()
        .flat_map(|section| section.api_calls.iter().cloned())
        .collect();
    lines.push(String::new());
    lines.push(ended_line(&every));
    fit(&head.join("\n"), &lines, budgets.total)
}

/// One session as the model sees it: what it cost, and a line per thing it did.
pub fn render_session(item: &SessionItem, budgets: &Budgets) -> String {
    let head = [
        format!(
            "# Session: {}",
            non_empty(item.title.as_deref()).unwrap_or("untitled")
        ),
        String::new(),
        "## Metrics".to_owned(),
        format!(
            "branch {}",
            non_empty(item.git_branch.as_deref()).unwrap_or("unknown")
        ),
        format!(
            "wall {}, active {}",
            duration(item.wall_ms),
            duration(item.active_ms)
        ),
        format!(
            "tokens {} in, {} out, {} cache read, {} cache write",
            thousands(item.input_tokens),
            thousands(item.output_tokens),
            thousands(item.cache_read_tokens),
            thousands(item.cache_creation_tokens)
        ),
        format!("cost ${:.2}", item.cost_usd),
        String::new(),
        "## Work".to_owned(),
    ];
    let lines: Vec<String> = item.children.iter().map(child_line).collect();
    fit(&head.join("\n"), &lines, budgets.total)
}

/// What a run with no prompt of its own says in place of a task. All 41 zero-turn runs of the
/// corpus are forks, and the conversation they continue is not in their transcript.
const CONTINUATION: &str =
    "This run continues a conversation another transcript holds; its task is not here.";

/// The one turn opener that carries attributes. Its wrapper is markup — forwarding it would
/// spend budget on the tag and read as content, exactly as a slash command's tags would.
static TEAMMATE_MESSAGE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?is)\A<teammate-message\b[^>]*>(.*)</teammate-message>\z")
        .expect("the teammate wrapper compiles")
});

/// An instruction from another agent, without the XML the transcript stores it in.
fn unwrap_teammate(prompt: &str) -> &str {
    let trimmed = prompt.trim();
    match TEAMMATE_MESSAGE.captures(trimmed) {
        Some(found) => found
            .get(1)
            .expect("the wrapper has one group")
            .as_str()
            .trim(),
        None => prompt,
    }
}

/// One response and the tools it asked for, appended to the elidable sequence.
fn push_response(lines: &mut Vec<String>, call: &ApiCallRow, budgets: &Budgets) {
    lines.push(String::new());
    lines.push("## Response".to_owned());
    let text = cap(call.text.trim(), budgets.text);
    if !text.is_empty() {
        lines.push(text);
    }
    lines.extend(call.tool_calls.iter().map(|tool| tool_line(tool, budgets)));
}

/// How an item ended, in the one line that keeps the model from inferring it.
///
/// Last, once, and never per response: `tool_use` is what a call requesting a tool always says,
/// so 51 of 69 recorded values would be noise beside every response.
fn ended_line(calls: &[ApiCallRow]) -> String {
    match calls.last() {
        None => "## Ended: no model response".to_owned(),
        Some(call) => format!(
            "## Ended: {}",
            call.stop_reason.as_deref().unwrap_or("not recorded")
        ),
    }
}

/// What the CLI printed, or which of the two ways it printed nothing.
///
/// Three deliberately distinguished states: an unsaid one reads as an unanswered command, which
/// is the inference this block exists to remove.
fn command_result_block(result: Option<&str>, budgets: &Budgets) -> String {
    match result {
        None => "## Command result: not recorded".to_owned(),
        Some("") => "## Command result: the command printed nothing".to_owned(),
        Some(held) => format!("## Command result\n{}", cap(held, budgets.command_result)),
    }
}

/// One tool call on one line: what ran, on what, and how big the answer was.
fn tool_line(tool: &ToolCallRow, budgets: &Budgets) -> String {
    let result = if tool.incomplete {
        // Not "0 chars": the session ended or was interrupted mid-call.
        "unanswered".to_owned()
    } else {
        match &tool.result {
            None => "no result".to_owned(),
            Some(held) => format!(
                "result {} chars{}",
                width(held),
                if tool.is_error { ", ERROR" } else { "" }
            ),
        }
    };
    let mut line = format!(
        "- {} (input {} chars, {result}) {}",
        tool.name,
        width(&tool.input),
        one_line(&cap(&tool.input, budgets.input_head))
    );
    if tool.is_error && tool.result.as_deref().is_some_and(|held| !held.is_empty()) {
        let held = tool.result.as_deref().expect("the result is present");
        line += &format!(
            " | error tail: {}",
            one_line(&tail(held, budgets.error_tail))
        );
    }
    if let Some(spawned) = &tool.spawned {
        line += &format!(" | subagent: {}", one_line(spawned));
    }
    line
}

/// One child of a session on one line: what kind of thing it was, and what it did.
fn child_line(child: &SessionChild) -> String {
    let label = match &child.agent_type {
        None => "Main turn".to_owned(),
        Some(kind) => format!("Agent run ({kind})"),
    };
    match &child.description {
        None => format!("- {label} [not described yet]"),
        Some(said) => format!(
            "- {label} [{}/{}] {}",
            child.category.as_deref().unwrap_or("None"),
            child.outcome.as_deref().unwrap_or("None"),
            one_line(said)
        ),
    }
}

/// A span of time in the two largest units that carry it — what a reader compares.
fn duration(ms: Option<i64>) -> String {
    let Some(ms) = ms else {
        return "unknown".to_owned();
    };
    let (seconds, minutes) = (ms.div_euclid(1_000), ms.div_euclid(60_000));
    if seconds < 60 {
        format!("{seconds}s")
    } else if minutes < 60 {
        format!("{minutes}m {}s", seconds.rem_euclid(60))
    } else if minutes < 1_440 {
        format!("{}h {}m", minutes / 60, minutes % 60)
    } else {
        format!("{}d {}h", minutes / 1_440, minutes % 1_440 / 60)
    }
}

/// A count with the separators Python's `:,` writes, so the two renders hash alike.
fn thousands(count: i64) -> String {
    let digits = count.unsigned_abs().to_string();
    let grouped: Vec<String> = digits
        .as_bytes()
        .rchunks(3)
        .rev()
        .map(|chunk| String::from_utf8_lossy(chunk).into_owned())
        .collect();
    format!("{}{}", if count < 0 { "-" } else { "" }, grouped.join(","))
}

/// None where Python's `or` falls through: an absent title and an empty one read alike.
fn non_empty(value: Option<&str>) -> Option<&str> {
    value.filter(|held| !held.is_empty())
}

/// How long a string is to every limit here — code points, as the Python side counts them.
///
/// Public because a budget is a claim about the same unit: a caller checking a render against
/// `total`, or pricing one, has to count what the cap counted.
pub fn width(text: &str) -> usize {
    text.chars().count()
}

/// The first `count` characters, whole.
fn head_of(text: &str, count: usize) -> &str {
    match text.char_indices().nth(count) {
        Some((at, _)) => &text[..at],
        None => text,
    }
}

/// The last `count` characters, whole.
fn tail_of(text: &str, count: usize) -> &str {
    let length = width(text);
    match length
        .checked_sub(count)
        .and_then(|skip| text.char_indices().nth(skip))
    {
        Some((at, _)) => &text[at..],
        None => text,
    }
}

fn dropped(count: usize) -> String {
    format!("[+{count} chars]")
}

/// How much text a cap keeps once room for its marker is paid for.
///
/// Reserved against the whole length, so the marker finally written — which counts something
/// shorter — always fits. Zero or less means the limit holds no marker at all, and the count is
/// then what goes: a reader who cannot have both wants the text.
fn room(length: usize, limit: usize) -> isize {
    limit as isize - width(&dropped(length)) as isize
}

/// The head of `text` and how much was left behind, in `limit` characters or fewer.
fn cap(text: &str, limit: usize) -> String {
    let length = width(text);
    if length <= limit {
        return text.to_owned();
    }
    match room(length, limit) {
        kept if kept > 0 => {
            let kept = kept as usize;
            format!("{}{}", head_of(text, kept), dropped(length - kept))
        }
        _ => head_of(text, limit).to_owned(),
    }
}

/// The end of `text` — where an error message says what failed — within the same limit.
fn tail(text: &str, limit: usize) -> String {
    let length = width(text);
    if length <= limit {
        return text.to_owned();
    }
    match room(length, limit) {
        kept if kept > 0 => {
            let kept = kept as usize;
            format!("{}{}", dropped(length - kept), tail_of(text, kept))
        }
        _ => tail_of(text, limit).to_owned(),
    }
}

fn one_line(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn elision(elided: isize, total: usize) -> String {
    format!("[… {elided} of {total} lines elided …]")
}

/// `head` plus as many of `lines` as fit, dropping the middle and saying how many.
///
/// The head and tail of the sequence are what a description is built from — how the work
/// started and how it ended. The middle is where a long grind repeats itself.
fn fit(head: &str, lines: &[String], budget: usize) -> String {
    let mut whole = vec![head.to_owned()];
    whole.extend(lines.iter().cloned());
    let whole = whole.join("\n");
    if width(&whole) <= budget {
        return whole;
    }
    if width(head) >= budget {
        return head_of(head, budget).to_owned();
    }
    // The longest the marker can be, so the room reserved for it is always enough.
    let total = lines.len();
    let mut room = budget as isize
        - width(head) as isize
        - 1
        - width(&elision(total as isize, total)) as isize
        - 1;
    let mut kept_head: Vec<String> = Vec::new();
    let mut kept_tail: Vec<String> = Vec::new();
    let (mut low, mut high, mut from_head) = (0isize, total as isize - 1, true);
    while low <= high {
        let index = if from_head { low } else { high };
        if width(&lines[index as usize]) as isize + 1 > room {
            // One end no longer fits; try the other before giving up.
            let other = if from_head { high } else { low };
            if low == high || width(&lines[other as usize]) as isize + 1 > room {
                break;
            }
            from_head = !from_head;
            continue;
        }
        room -= width(&lines[index as usize]) as isize + 1;
        if from_head {
            kept_head.push(lines[low as usize].clone());
            low += 1;
        } else {
            kept_tail.insert(0, lines[high as usize].clone());
            high -= 1;
        }
        from_head = !from_head;
    }
    let mut out = vec![head.to_owned()];
    out.extend(kept_head);
    out.push(elision(high - low + 1, total));
    out.extend(kept_tail);
    out.join("\n")
}
