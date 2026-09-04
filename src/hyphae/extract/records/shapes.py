"""One model per record type Claude Code writes, and the mixins that say who carries what.

Which records carry a field is derived from inheritance rather than written down: a shared
field lives on the mixin whose subclasses have it, so nothing states the set twice.
"""

from typing import Annotated, Any, ClassVar

from pydantic import Field

from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.records.blocks import AssistantMessage, ToolUseResult, UserMessage
from hyphae.extract.records.evidence import (
    COMPACTION,
    DUP_UUID,
    FORK_BYREF,
    FORK_ORIGIN,
    LEGACY_ENTRYPOINT,
    LEGACY_TITLE,
    MODEL_ONLY,
    OFFLOAD,
    REGISTRY_ZOO,
    RESUME_PAIR,
    SERVER_TOOLS,
    SPINE,
    Cited,
    Described,
)
from hyphae.extract.records.registry import (
    ArchiveRecordType,
    RecordType,
    SystemSubtype,
)


class Record(Described):
    """Any transcript record."""

    RECORD_TYPE: ClassVar[RecordType]
    # The `system` subtype this model describes, for the subtypes that carry their own fields.
    SUBTYPE: ClassVar[SystemSubtype | None] = None

    type: Annotated[
        str,
        Field(
            description=(
                "The record shape. Known values include `user`, `assistant`, `system`, "
                "`attachment`, `summary`, and about a dozen bookkeeping types"
            )
        ),
        Cited(REGISTRY_ZOO, note="holds one record of every registered type"),
    ]


class SessionScoped(Record):
    """A record that names the session it belongs to."""

    sessionId: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The session id Claude Code wrote into the record. Nothing reads it: the "
                "extractor takes the session id from the file name"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class Timestamped(SessionScoped):
    """A record placed in time."""

    timestamp: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "A UTC ISO-8601 timestamp with a `Z` suffix. File order is not timestamp order; "
                "adjacent records can move backward by one millisecond"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class Identified(Timestamped):
    """A conversation record: it has an id, and it answers another record."""

    uuid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The record id within its file. It is not unique: rewinding can write new "
                "records under existing uuids, and the extractor keeps the last"
            ),
        ),
        Cited(DUP_UUID, "2.1.211", note="five uuids twice each"),
    ]
    parentUuid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The record this one answers, or null at the start of a thread. A "
                "`<local-command-stdout>` record points at the command turn whose output it is"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


# What `agentId` says on both records that carry one, which sit on either side of `Record`.
_AGENT_ID = (
    "The agent run the record belongs to. A subagent's transcript is "
    "`<session>/subagents/agent-<agentId>.jsonl`, so the id is its file name without the prefix"
)
_AGENT_ID_EVIDENCE = Cited(SPINE, "2.1.221", note="every record of each subagent thread")


class SessionContext(Identified):
    """A record carrying where and how the session was running when it was written."""

    cwd: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The project directory, absolute and symlink-free. Resolve a command-line path "
                "before matching it — `hyphae.projects.resolve_project` does. Early "
                "bookkeeping records omit it, so reading only the first record yields nulls"
            ),
        ),
        Cited(SPINE, "2.1.221", note="the first three records have none"),
    ]
    gitBranch: Annotated[
        str | None,
        Field(default=None, description="The branch checked out when the record was written"),
        Cited(SPINE, "2.1.221"),
    ]
    version: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The Claude Code version that wrote the record, and the version every schema "
                "claim here is dated by"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    entrypoint: Annotated[
        str | None,
        Field(default=None, description="How the session was launched, such as `cli`"),
        Cited(SPINE, "2.1.221"),
        Cited(LEGACY_ENTRYPOINT, "1.0.128", absent=True, note="the oldest corpus transcripts"),
    ]
    isSidechain: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "The record belongs to a subagent stream. On a main thread, skip it because "
                "the subagent's own file records the work better. On a subagent thread every "
                "record carries it, and skipping those would remove every turn"
            ),
        ),
        Cited(SPINE, "2.1.221", note="holds both main and subagent records"),
    ]
    agentId: Annotated[
        str | None,
        Field(default=None, description=_AGENT_ID),
        _AGENT_ID_EVIDENCE,
    ]
    userType: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Who the record is attributed to. Every fixture record says `external`, so no "
                "other value is recorded"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    slug: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "A short name Claude Code gives the session. The fixtures redact it, so its "
                "presence is what is recorded and not how it is derived"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    sessionKind: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "What kind of session Claude Code was recording. Redacted in the one fixture "
                "that carries it, so no value is recorded"
            ),
        ),
        Cited(RESUME_PAIR, "2.1.205"),
    ]
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "A second session id in snake_case, which does not always agree with "
                "`sessionId`: a resumed transcript copies the original id here while `sessionId` "
                "follows the file, and 58 of 99 fixture records disagree. Nothing reads either"
            ),
        ),
        Cited(RESUME_PAIR, "2.1.205", note="52 of 54 disagree with `sessionId`"),
    ]


class MetaFlagged(Described):
    """A record Claude Code can write on the operator's behalf.

    Its own mixin because `user` and `system` records carry the flag and `assistant` records
    never do.
    """

    isMeta: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "Claude Code wrote the record on the user's behalf, such as a caveat or a hook "
                "echo. It is not a prompt"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


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


class SystemRecord(SessionContext, MetaFlagged):
    """Something the harness did, named by its `subtype`."""

    RECORD_TYPE = RecordType.SYSTEM

    subtype: Annotated[
        str,
        Field(
            description=(
                "The system event. The registry zoo holds ten, including `turn_duration`, "
                "`compact_boundary`, and `api_error`"
            )
        ),
        Cited(REGISTRY_ZOO, note="one record of every registered subtype"),
    ]
    level: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "How loud the event is: `info`, `warning`, `error` and `suggestion` in the fixtures"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    content: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The event's own text. On a `local_command` it is the `<local-command-stdout>` "
                "body, which Claude Code writes here rather than on a `user` record for 37 of "
                "316 corpus outputs; the body can span lines and can be empty"
            ),
        ),
        Cited(MODEL_ONLY, "2.1.215", note="an empty `/clear` body"),
    ]


class TurnDurationRecord(SystemRecord):
    """How long a turn took."""

    SUBTYPE = SystemSubtype.TURN_DURATION

    durationMs: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "The turn's wall-clock duration in milliseconds. Sum these to measure active "
                "session time; the transcript's timestamp span includes idle hours"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    messageCount: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "A message count Claude Code writes beside the duration. It reaches 466 in one "
                "fixture turn, so it counts more than the turn's own records; nothing reads it"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    pendingBackgroundAgentCount: Annotated[
        int | None,
        Field(
            default=None,
            description="How many background agent runs were still going when the turn ended",
        ),
        Cited(SPINE, "2.1.221"),
    ]


class CompactMetadata(Described):
    """What one compaction dropped, and what it cost."""

    trigger: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "`auto` when Claude Code hit the context limit, `manual` when the operator asked: "
                "933 and 93 of 1,026 corpus boundaries (scanned 2026-08-07)"
            ),
        ),
        Cited(COMPACTION, "2.1.198", note="one of each"),
    ]
    preTokens: Annotated[
        int | None,
        Field(default=None, description="Context size before the compaction"),
        Cited(COMPACTION, "2.1.198"),
    ]
    postTokens: Annotated[
        int | None,
        Field(default=None, description="Context size after it"),
        Cited(COMPACTION, "2.1.198"),
    ]
    durationMs: Annotated[
        int | None,
        Field(default=None, description="How long the compaction itself took"),
        Cited(COMPACTION, "2.1.198"),
    ]
    cumulativeDroppedTokens: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Tokens every compaction in the thread has dropped so far, this one included, "
                "so it does not reduce to `preTokens` minus `postTokens`"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    preCompactDiscoveredTools: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="The tools the thread had discovered before compacting, by name",
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    preservedMessages: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Which records survived, as an anchor uuid and the uuids kept. Nothing has "
                "opened it, so its interior is undeclared"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    preservedSegment: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "The span of records the compaction kept, by head, anchor and tail uuid. "
                "Nothing has opened it, so its interior is undeclared"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]


class CompactBoundaryRecord(SystemRecord):
    """Where Claude Code summarized the conversation to free context."""

    SUBTYPE = SystemSubtype.COMPACT_BOUNDARY

    compactMetadata: Annotated[
        CompactMetadata | None,
        Field(
            default=None,
            description=(
                "The compaction's own numbers. Read compaction from this object rather than "
                "inferring it from the nearest assistant call; all 1,026 corpus boundaries carry "
                "it (scanned 2026-08-07)"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]
    logicalParentUuid: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The record the boundary answers in the conversation, beside `parentUuid`, "
                "which answers the file. Nothing reads it"
            ),
        ),
        Cited(COMPACTION, "2.1.198"),
    ]


class LocalCommandRecord(SystemRecord):
    """What a slash command printed, in the shape Claude Code uses less often.

    It adds no field of its own: the output sits in `SystemRecord.content`, which every `system`
    subtype that says anything writes to.
    """

    SUBTYPE = SystemSubtype.LOCAL_COMMAND


class ModelConsentFallbackRecord(SystemRecord):
    """The session ran on another model because the one asked for needed credits the account
    lacked. Not a `fallback` block, which retries one request."""

    SUBTYPE = SystemSubtype.MODEL_CONSENT_FALLBACK

    originalModel: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The model the session asked for and did not get: it needed credits the "
                "account lacked"
            ),
        ),
        Cited(REGISTRY_ZOO, "2.1.221"),
    ]
    fallbackModel: Annotated[
        str | None,
        Field(default=None, description="The model it ran on instead"),
        Cited(REGISTRY_ZOO, "2.1.221"),
    ]
    choice: Annotated[
        str | None,
        Field(default=None, description="What the operator answered, such as `cancelled`"),
        Cited(REGISTRY_ZOO, "2.1.221"),
    ]
    persistedAsDefault: Annotated[
        bool | None,
        Field(default=None, description="Whether the change outlived the session"),
        Cited(REGISTRY_ZOO, "2.1.221"),
    ]


class CustomTitleRecord(SessionScoped):
    """The title the operator typed."""

    RECORD_TYPE = RecordType.CUSTOM_TITLE

    customTitle: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The session title the operator set. It stays current beside `aiTitle`, and 13 "
                "of 398 titled mycelia sessions carry both (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class AiTitleRecord(SessionScoped):
    """The title Claude Code wrote, and rewrites."""

    RECORD_TYPE = RecordType.AI_TITLE

    aiTitle: Annotated[
        str | None,
        Field(
            default=None,
            description="The session title Claude Code wrote for itself, revised as work goes on",
        ),
        Cited(LEGACY_TITLE, "2.1.196"),
        Cited(SPINE, "2.1.221"),
    ]


class AgentNameRecord(SessionScoped):
    """The persona a session ran under."""

    RECORD_TYPE = RecordType.AGENT_NAME

    agentName: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Claude Code rewrites this with the title, so it holds no name of its own to "
                "show: all 84 of the canonical store's 596 sessions that carry one hold exactly "
                "that session's title (scanned 2026-08-25)"
            ),
        ),
        Cited(SPINE, "2.1.201", note="the record's shape"),
        Cited(scan="the canonical store, every version it holds, scanned 2026-08-25"),
    ]


class PrLinkRecord(Timestamped):
    """A pull request the session mentioned. One record per mention, and no uuid."""

    RECORD_TYPE = RecordType.PR_LINK

    prNumber: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "The pull request number. The same PR can recur within a session, so key each "
                "link by its line: all 2,885 corpus records carry these three fields plus `type`, "
                "`sessionId`, and `timestamp` (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    prUrl: Annotated[
        str | None,
        Field(default=None, description="The pull request's URL"),
        Cited(SPINE, "2.1.221"),
    ]
    prRepository: Annotated[
        str | None,
        Field(default=None, description="The `owner/name` repository it belongs to"),
        Cited(SPINE, "2.1.221"),
    ]


class ForkContextRefRecord(Record):
    """Opens a by-reference fork: the file copies no records and names what it continues.

    The other fork shape copies its parent's records verbatim and opens with a `user` or `system`
    record instead, which is why this record has neither a session id nor a timestamp.
    """

    RECORD_TYPE = RecordType.FORK_CONTEXT_REF

    parentSessionId: Annotated[
        str | None,
        Field(default=None, description="The conversation this transcript continues"),
        Cited(FORK_BYREF, "2.1.202"),
    ]
    parentLastUuid: Annotated[
        str | None,
        Field(default=None, description="The parent record work resumes after"),
        Cited(FORK_BYREF, "2.1.202"),
    ]
    contextLength: Annotated[
        int | None,
        Field(default=None, description="How much of the parent's context the fork carried over"),
        Cited(FORK_BYREF, "2.1.202"),
    ]
    # Redeclared rather than lifted: this record sits outside `SessionContext`, and the two
    # declarations share one meaning and one citation so the tables print them as one row.
    agentId: Annotated[
        str | None,
        Field(default=None, description=_AGENT_ID),
        _AGENT_ID_EVIDENCE,
    ]


class ArchivedRecord(Identified):
    """A kind the store keeps verbatim and no reader opens.

    It extends `Identified` for the four envelope fields `raw_record` and the run-time bounds
    read — `uuid` and `timestamp` above all, which 24k `attachment` records carry — and claims
    nothing else: the rest of its keys are the archive's, kept whole rather than described.
    It is outside `RECORD_MODELS`, so it prints no row in `docs/schema.md`.
    """

    OPAQUE = "archived verbatim; its fields are the archive's, not a claim"


# Every record model, in the order the tables name them.
RECORD_MODELS: tuple[type[Record], ...] = (
    UserRecord,
    AssistantRecord,
    SystemRecord,
    TurnDurationRecord,
    CompactBoundaryRecord,
    LocalCommandRecord,
    ModelConsentFallbackRecord,
    CustomTitleRecord,
    AiTitleRecord,
    AgentNameRecord,
    PrLinkRecord,
    ForkContextRefRecord,
)


# Registered kinds `ArchivedRecord` takes, each with the reason nothing opens it. Every archive
# type is here by construction; a `system` subtype is here when it is too thin to model, which
# also says `SystemRecord` is the base of the four modelled subtypes and no longer a fallback.
ARCHIVED_UNREAD: dict[ArchiveRecordType | SystemSubtype, str] = {
    **dict.fromkeys(
        ArchiveRecordType, "archived verbatim and read by nothing, so there is no field to describe"
    ),
    SystemSubtype.AWAY_SUMMARY: "carries only the common system fields and a `content` string",
    SystemSubtype.INFORMATIONAL: "carries only the common system fields and a `content` string",
    SystemSubtype.SCHEDULED_TASK_FIRE: (
        "carries only the common system fields and a `content` string"
    ),
    SystemSubtype.API_ERROR: (
        "its retry fields are read by nothing, and one recorded error is thin evidence for them"
    ),
    SystemSubtype.AGENTS_KILLED: "carries only the common system fields",
    SystemSubtype.STOP_HOOK_SUMMARY: (
        "its hook fields are read by nothing, and one recorded summary is thin evidence for them"
    ),
}

# What `model_for` dispatches on: the record type, and the subtype for the `system` records
# that carry fields of their own.
_TYPE_MODELS: dict[str, type[Record]] = {}
_SUBTYPE_MODELS: dict[str, type[Record]] = {}
for _model in RECORD_MODELS:
    _subtype = _model.SUBTYPE
    if _subtype is None:
        _TYPE_MODELS[_model.RECORD_TYPE.value] = _model
    else:
        _SUBTYPE_MODELS[_subtype.value] = _model


def model_for(record: dict[str, Any]) -> type[Record]:
    """The model describing one raw record. Total over both registries: a kind outside them
    raises, because a record type we quietly skip is a wrong count months from now.

    The caller adds the session and the line, which are what a reader needs to find the record.
    """
    kind = record.get("type", "")
    if kind == RecordType.SYSTEM:
        subtype = record.get("subtype", "")
        modelled = _SUBTYPE_MODELS.get(subtype)
        if modelled is not None:
            return modelled
        if subtype in ARCHIVED_UNREAD:
            return ArchivedRecord
        raise TranscriptSchemaError(f"Unknown system subtype {subtype!r}")
    modelled = _TYPE_MODELS.get(kind)
    if modelled is not None:
        return modelled
    if kind in ARCHIVED_UNREAD:
        return ArchivedRecord
    raise TranscriptSchemaError(f"Unknown record type {kind!r}")
