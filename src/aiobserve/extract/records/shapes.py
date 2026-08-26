"""One model per record type Claude Code writes, and the mixins that say who carries what.

Which records carry a field is derived from inheritance rather than written down: a shared
field lives on the mixin whose subclasses have it, so nothing states the set twice.
"""

from typing import Annotated, Any, ClassVar

from pydantic import Field

from aiobserve.extract.claude_code import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    SystemSubtype,
)
from aiobserve.extract.records.blocks import AssistantMessage, ToolUseResult, UserMessage
from aiobserve.extract.records.evidence import (
    COMPACTION,
    DUP_UUID,
    FORK_BYREF,
    FORK_ORIGIN,
    LEGACY_ENTRYPOINT,
    LEGACY_TITLE,
    MODEL_ONLY,
    OFFLOAD,
    REGISTRY_ZOO,
    SPINE,
    Cited,
    Described,
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


class SessionContext(Identified):
    """A record carrying where and how the session was running when it was written."""

    cwd: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The project directory, absolute and symlink-free. Resolve a command-line path "
                "before matching it — `aiobserve.sessions.resolve_project` does. Early "
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
                "The record belongs to a subagent stream. In a main transcript, skip it because "
                "the subagent's own file records the work better. In a subagent transcript every "
                "record carries it, and skipping those would remove every turn"
            ),
        ),
        Cited(SPINE, "2.1.221", note="holds both main and subagent records"),
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


class LocalCommandRecord(SystemRecord):
    """What a slash command printed, in the shape Claude Code uses less often."""

    SUBTYPE = SystemSubtype.LOCAL_COMMAND

    content: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The `<local-command-stdout>` text, when Claude Code recorded the output as a "
                "`system` record rather than a `user` one: 37 of 316 corpus outputs. The body can "
                "span lines and can be empty"
            ),
        ),
        Cited(MODEL_ONLY, "2.1.215", note="an empty `/clear` body"),
    ]


class ModelConsentFallbackRecord(SystemRecord):
    """The session ran on another model because the one asked for needed credits the account
    lacked. Not a `fallback` block, which retries one request."""

    SUBTYPE = SystemSubtype.MODEL_CONSENT_FALLBACK

    originalModel: Annotated[
        str | None,
        Field(default=None, description="The model the session asked for"),
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


# Registered shapes no model describes, each with the reason. The drift tier holds this set to
# the registries in `claude_code.py`, so a new record type lands here or gets a model.
UNMODELLED: dict[str, str] = {
    **{
        kind.value: "archived verbatim and read by nothing, so there is no field to describe"
        for kind in ArchiveRecordType
    },
    SystemSubtype.AWAY_SUMMARY.value: (
        "carries only the common system fields and a `content` string"
    ),
    SystemSubtype.INFORMATIONAL.value: (
        "carries only the common system fields and a `content` string"
    ),
    SystemSubtype.SCHEDULED_TASK_FIRE.value: (
        "carries only the common system fields and a `content` string"
    ),
    SystemSubtype.API_ERROR.value: (
        "its retry fields are read by nothing, and one recorded error is thin evidence for them"
    ),
    SystemSubtype.AGENTS_KILLED.value: "carries only the common system fields",
    SystemSubtype.STOP_HOOK_SUMMARY.value: (
        "its hook fields are read by nothing, and one recorded summary is thin evidence for them"
    ),
    ContentBlock.IMAGE.value: "no fixture holds one, so there is nothing to cite",
}

# Documented fields the parser never reads. Every other documented name appears in
# `claude_code.py`, which the drift tier checks.
OBSERVED_UNREAD: dict[str, str] = {
    "sessionId": "the extractor takes the session id from the file name",
    "originalModel": "the model_consent_fallback record is archived, not parsed",
    "fallbackModel": "the model_consent_fallback record is archived, not parsed",
    "choice": "the model_consent_fallback record is archived, not parsed",
    "persistedAsDefault": "the model_consent_fallback record is archived, not parsed",
    "parentSessionId": "a by-reference fork's parent is not followed",
    "contextLength": "nothing measures what a fork carried over",
    "encrypted_content": "unreadable by construction",
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


def model_for(record: dict[str, Any]) -> type[Record] | None:
    """The most specific model describing one raw record, or `None` for a shape none describes."""
    kind = record.get("type", "")
    if kind == RecordType.SYSTEM:
        return _SUBTYPE_MODELS.get(record.get("subtype", ""), SystemRecord)
    return _TYPE_MODELS.get(kind)
