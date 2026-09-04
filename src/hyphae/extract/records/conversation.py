"""The two records that carry an API message: what the person sent, and what the model replied."""

from typing import Annotated, Any

from pydantic import Field

from hyphae.extract.records.base import MetaFlagged, SessionContext
from hyphae.extract.records.evidence import (
    CENSUS,
    COMPACTION,
    DUP_UUID,
    FORK_ORIGIN,
    LEGACY_ENTRYPOINT,
    OFFLOAD,
    SERVER_TOOLS,
    SPINE,
    Cited,
)
from hyphae.extract.records.messages import AssistantMessage, ToolUseResult, UserMessage
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
    sourceToolUseID: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The tool call this record answers, by `tool_use` id. It names the same link as "
                "`sourceToolAssistantUUID` and never appears beside it — 613 corpus records "
                "carry one and none carries both. Nothing reads either"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    toolDenialKind: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Why a tool call was refused. The 306 corpus records carrying one say "
                "`automode-blocked`, `permission-rule`, `automode-unavailable`, `user-rejected` "
                "or `automode-parsing-error`"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    userFeedback: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "What the operator said when refusing a tool call. Both corpus records carrying "
                "it also say `toolDenialKind: user-rejected`"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    toolEndsTurn: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The tool result ends the turn rather than feeding another reply. Recorded only "
                "as true, on 108 records in 2 corpus sessions, so the false shape is unrecorded"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    turnCompanion: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The record rides along with a turn rather than opening one. Recorded only as "
                "true, on 5 records written by `2.1.259`, so the false shape is unrecorded"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
    ]
    queuePriority: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Where a queued prompt sits in the queue. All 89 corpus values are `later`, so "
                "no other is recorded"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    queueSkipAttachments: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The queued prompt went in without its attachments. Recorded only as true, on 3 "
                "records written by `2.1.259`, so the false shape is unrecorded"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
    ]
    classifierMetaLines: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "What a classifier noted about the prompt, as a JSON document held in a string "
                "rather than an object — 952 of the 960 corpus values parse and 8 do not. "
                "Nothing has opened it, so its interior is undeclared"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    imagePasteIds: Annotated[
        list[int] | None,
        Field(
            default=None,
            description=(
                "The images pasted into the prompt, by id. Two corpus records carry the list"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    mcpMeta: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "What an MCP tool returned beside its result, as an object holding `_meta` and "
                "`structuredContent`. One corpus record carries it, which is too thin to declare "
                "an interior on"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    scheduledTaskId: Annotated[
        str | None,
        Field(
            default=None,
            description="The scheduled task that wrote the record. One corpus record carries it",
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
    ]
    scheduledFireId: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The one firing of that task, beside `scheduledTaskId`. The same corpus record "
                "carries both"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
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
    error: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "What failed, when the reply is Claude Code's error report. Every one of the 222 "
                "corpus records carrying it also says `isApiErrorMessage`, and the values are "
                "`rate_limit`, `server_error`, `oauth_org_not_allowed`, `authentication_failed` "
                "and `model_not_found`"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    errorDetails: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "More about that failure, as a free string. Six corpus records carry it, each "
                "beside an `error`"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    apiErrorStatus: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "The HTTP status behind the failure. The 181 corpus records carrying one say "
                "429, 403, 529, 404 or 500"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    apiBlockIndex: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Which block of the reply this record holds, counting from zero within the API "
                "message. Claude Code added it late: 220 corpus records over 90 message ids, "
                "all written by `2.1.259`, running 0 to 5"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
    ]
    attributionMcpServer: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The MCP server the reply is attributed to, beside `attributionSkill`. Always "
                "written with `attributionMcpTool`: 4,732 corpus records in 27 sessions carry "
                "both and none carries one alone"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    attributionMcpTool: Annotated[
        str | None,
        Field(
            default=None,
            description="The tool on that server, beside `attributionMcpServer`",
        ),
        Cited(scan=CENSUS),
    ]
