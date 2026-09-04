"""The two records that carry an API message: what the person sent, and what the model replied."""

from typing import Annotated, Any

from pydantic import Field

from hyphae.extract.records.base import MetaFlagged, SessionContext
from hyphae.extract.records.blocks import AssistantMessage, ToolUseResult, UserMessage
from hyphae.extract.records.evidence import (
    COMPACTION,
    DUP_UUID,
    FORK_ORIGIN,
    LEGACY_ENTRYPOINT,
    OFFLOAD,
    SERVER_TOOLS,
    SPINE,
    Cited,
)
from hyphae.extract.records.registry import RecordType

# What `message` says on both records that carry one: they narrow the type, not the meaning.
_MESSAGE = "The API message the record carried: a role and its content"
_MESSAGE_EVIDENCE = Cited(SPINE, "2.1.221")


class UserRecord(SessionContext, MetaFlagged):
    """A prompt, a tool result, or something Claude Code wrote on the operator's behalf."""

    RECORD_TYPE = RecordType.USER

    message: Annotated[
        UserMessage | None,
        Field(default=None, description=_MESSAGE),
        _MESSAGE_EVIDENCE,
    ]
    isCompactSummary: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "Claude Code wrote the record after compaction to replace the dropped context. "
                "It is not a prompt, and every one has a `compact_boundary` record beside it"
            ),
        ),
        Cited(DUP_UUID, "2.1.211"),
    ]
    toolUseResult: Annotated[
        ToolUseResult | str | list[Any] | None,
        Field(
            default=None,
            description=(
                "The tool's structured report beside the result block. Most are objects, but "
                "3,590 of 137,255 corpus values are strings and 795 are lists (scanned "
                "2026-08-07)"
            ),
        ),
        Cited(OFFLOAD, "2.1.220"),
        Cited(FORK_ORIGIN, "2.1.215", note="a string-valued one"),
    ]
    promptId: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "An id Claude Code gives the record's prompt. It is not the record's own "
                "`uuid` — the two differ on all 84 fixture records that carry both"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    promptSource: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Where the prompt came from. Redacted in every fixture, so no value is recorded"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    origin: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Where the record came from, as an object holding a `kind`. Nothing has opened "
                "it, so its interior is undeclared"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    permissionMode: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The permission mode in force when the record was written: `default`, `auto` "
                "and `bypassPermissions` in the fixtures"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    isVisibleInTranscriptOnly: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The record is shown when reading the transcript back and nowhere else. "
                "Recorded only as true, so the false shape is unrecorded"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    sourceToolAssistantUUID: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The assistant record this one answers, by uuid. Nothing reads it: a result is "
                "joined to its call through `tool_use_id`"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    interruptedMessageId: Annotated[
        str | None,
        Field(
            default=None,
            description="The reply an interruption stopped. One fixture record carries it",
        ),
        Cited(SPINE, "2.1.220"),
    ]
    thinkingMetadata: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "The thinking budget in force, as a `level`, a `disabled` flag and `triggers`. "
                "Nothing has opened it, so its interior is undeclared"
            ),
        ),
        Cited(LEGACY_ENTRYPOINT, "1.0.128"),
    ]


class AssistantRecord(SessionContext):
    """One content block of one model reply."""

    RECORD_TYPE = RecordType.ASSISTANT

    message: Annotated[
        AssistantMessage | None,
        Field(default=None, description=_MESSAGE),
        _MESSAGE_EVIDENCE,
    ]
    requestId: Annotated[
        str | None,
        Field(default=None, description="The API request id the reply came back on"),
        Cited(SPINE, "2.1.221"),
    ]
    attributionSkill: Annotated[
        str | None,
        Field(
            default=None,
            description="The skill loaded when the reply returned. Absent when none was loaded",
        ),
        Cited(SPINE, "2.1.221"),
    ]
    effort: Annotated[
        str | None,
        Field(
            default=None,
            description='The reasoning-effort setting as an opaque string, such as `"high"`',
        ),
        Cited(SPINE, "2.1.221"),
    ]
    attributionAgent: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The agent the reply is attributed to, beside `attributionSkill`. Redacted in "
                "the fixtures, so no value is recorded"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    advisorModel: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The model behind a server-side advisor call. Only `server_tools/` records one, "
                "which is also the only fixture holding a `server_tool_use` block"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]
    isApiErrorMessage: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The reply is Claude Code's own report of an API error rather than the model's. "
                "Recorded once, as false, so the true shape is unrecorded"
            ),
        ),
        Cited(SPINE, "2.1.201"),
    ]
