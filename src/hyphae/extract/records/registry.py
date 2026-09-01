"""The closed world of a Claude Code transcript: every record type, subtype, block and tag.

These registries are what makes the reader closed-world. Claude Code owns these shapes and
changes them without notice, so anything not registered here raises `TranscriptSchemaError`
(`extract/errors.py`): a type we quietly skip today is a wrong count months from now.

Names only. The field-by-field models that describe what each shape holds, and the recording
that proves each claim, are the modules beside this one; `docs/schema.md` prints them.
"""

from enum import StrEnum


class RecordType(StrEnum):
    """Record types this parser reads. Anything outside both registries crashes."""

    ASSISTANT = "assistant"
    USER = "user"
    SYSTEM = "system"
    CUSTOM_TITLE = "custom-title"
    # The title Claude Code wrote for the session itself, still current beside
    # `custom-title` — which is the one the operator typed.
    AI_TITLE = "ai-title"
    AGENT_NAME = "agent-name"
    PR_LINK = "pr-link"
    # Opens a by-reference fork's transcript, naming the conversation it continues.
    FORK_CONTEXT_REF = "fork-context-ref"


class ArchiveRecordType(StrEnum):
    """Record types kept verbatim in `raw_records` and read by nothing.

    Each is either bookkeeping (editor state, file backups) or content the subagent's own
    transcript states better. Archiving rather than parsing keeps them recoverable.
    """

    ATTACHMENT = "attachment"
    LAST_PROMPT = "last-prompt"
    MODE = "mode"
    PERMISSION_MODE = "permission-mode"
    BRIDGE_SESSION = "bridge-session"
    FILE_HISTORY_SNAPSHOT = "file-history-snapshot"
    FILE_HISTORY_DELTA = "file-history-delta"
    AGENT_SETTING = "agent-setting"
    # The session's running cost and duration totals, rewritten as the session goes; the
    # store computes its own from the api calls.
    COST_STATE = "cost-state"
    # An opaque latch id Claude Code writes to itself, repeated unchanged all session.
    ATIS_LATCH = "atis-latch"
    QUEUE_OPERATION = "queue-operation"
    SUMMARY = "summary"
    # Worktree sessions only.
    WORKTREE_STATE = "worktree-state"
    RELOCATED = "relocated"
    # Workflow journals only (`subagents/workflows/wf_<id>/journal.jsonl`).
    STARTED = "started"
    RESULT = "result"


class SystemSubtype(StrEnum):
    """Every `system` subtype the corpus holds. An unregistered one crashes."""

    TURN_DURATION = "turn_duration"
    COMPACT_BOUNDARY = "compact_boundary"
    AWAY_SUMMARY = "away_summary"
    LOCAL_COMMAND = "local_command"
    INFORMATIONAL = "informational"
    SCHEDULED_TASK_FIRE = "scheduled_task_fire"
    API_ERROR = "api_error"
    AGENTS_KILLED = "agents_killed"
    STOP_HOOK_SUMMARY = "stop_hook_summary"
    # The harness ran the session on another model because the one asked for was unavailable.
    MODEL_CONSENT_FALLBACK = "model_consent_fallback"


class ContentBlock(StrEnum):
    """Every kind of block a `message.content` list holds. An unregistered one crashes.

    Assistant and user records are the only ones carrying such a list. Leaving the set
    open is how server-side tool calls stayed invisible: they produced no row, no text and
    no crash, so a session that used one looked like a session that had not.
    """

    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    # A tool Anthropic runs server-side. Recorded like `tool_use`, but answered by an
    # `advisor_tool_result` block in the same message rather than by a user record.
    SERVER_TOOL_USE = "server_tool_use"
    ADVISOR_TOOL_RESULT = "advisor_tool_result"
    # Claude Code retried the request on another model; `from`/`to` name both.
    FALLBACK = "fallback"
    IMAGE = "image"
    TOOL_RESULT = "tool_result"


class AdvisorResult(StrEnum):
    """What an `advisor_tool_result` block's own content says. An unregistered one crashes.

    Neither shape carries readable output: an error names its code, and a completed call
    comes back encrypted, so the transcript records that the advisor answered and nothing
    of what it said.
    """

    ERROR = "advisor_tool_result_error"
    REDACTED = "advisor_redacted_result"


class ResultBlock(StrEnum):
    """Block kinds a block-form `tool_result` is built from. An unregistered one crashes.

    Only text carries into `ToolCall.result`: an image has none, and a tool reference names
    a tool the result pointed at rather than saying anything.
    """

    TEXT = "text"
    IMAGE = "image"
    TOOL_REFERENCE = "tool_reference"


class TurnTag(StrEnum):
    """Leading tags on a prompt string that still make it a prompt."""

    COMMAND_NAME = "command-name"
    COMMAND_MESSAGE = "command-message"
    # An instruction from another agent — drives work exactly as a user prompt does.
    # Subagent transcripts only.
    TEAMMATE_MESSAGE = "teammate-message"


class MachineTag(StrEnum):
    """Leading tags Claude Code writes to itself. Archived, never turns.

    Counting these as prompts inflates the turn count roughly 3.6x on this corpus.
    """

    TASK_NOTIFICATION = "task-notification"
    LOCAL_COMMAND_STDOUT = "local-command-stdout"
    BASH_STDOUT = "bash-stdout"
    BASH_INPUT = "bash-input"
