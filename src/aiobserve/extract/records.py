"""Claude Code's raw record shapes, described field by field with the recording behind each claim.

These models describe; they do not parse. `claude_code.py` still reads records as dicts, and
nothing here runs during an extract. What they carry is the meaning of every field that document
ever stated, plus the fixture and Claude Code version that proves it — which `tools/gen_schema.py`
renders as the field tables in `docs/schema.md`.

Two rules hold the description honest:

- Every declared field carries a `description` and at least one `Cited`. A blank one crashes the
  generator rather than printing an empty cell
- Nothing is closed except the registries in `claude_code.py`. Every model allows extra keys,
  because Claude Code adds fields without notice and a validation error would be a worse answer
  than an undocumented field

Which records carry a field is derived from inheritance, not written down: a field lives on the
mixin that names the records that have it.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, ForwardRef, NamedTuple, get_args

from pydantic import BaseModel, ConfigDict, Field

from aiobserve.extract.claude_code import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    SystemSubtype,
)

# The fixtures the claims below cite, spelled the way a reader would type them.
COMPACTION = "tests/fixtures/compaction/"
DUP_UUID = "tests/fixtures/dup_uuid/"
FORK_BYREF = "tests/fixtures/fork_byref/"
FORK_ORIGIN = "tests/fixtures/fork_origin/"
LEGACY_ENTRYPOINT = "tests/fixtures/legacy_entrypoint/"
LEGACY_TITLE = "tests/fixtures/legacy_title/"
MODEL_ONLY = "tests/fixtures/model_only/"
OFFLOAD = "tests/fixtures/offload/"
PARALLEL_TOOLS = "tests/fixtures/parallel_tools/"
REGISTRY_ZOO = "tests/fixtures/registry_zoo/"
SERVER_TOOLS = "tests/fixtures/server_tools/"
SPINE = "tests/fixtures/spine/"
WORKFLOW = "tests/fixtures/workflow/"


@dataclass(frozen=True)
class Cited:
    """One recording behind one claim, or the corpus scan that stands in for a recording.

    `fixture` is a repository-relative fixture directory; its README names the session. `absent`
    inverts the claim — the fixture is evidence that the field is *missing* there, which is how
    a field Claude Code added later is dated.
    """

    fixture: str = ""
    # The Claude Code version that wrote the cited records. Bookkeeping records carry no
    # `version` field of their own, so for those this is the fixture README's version.
    version: str = ""
    # A named corpus scan, for a claim no fixture can hold — always with its date.
    scan: str = ""
    # What the fixture shows beyond holding the field, printed after the citation.
    note: str = ""
    absent: bool = False


@dataclass(frozen=True)
class Among:
    """A step into every block of one kind inside a `message.content` list."""

    kind: ContentBlock


# One step of a field's locator: a key to read, or a block kind to select within a content list.
type Step = str | Among


class Documentation(NamedTuple):
    """One row of a `docs/schema.md` field table, derived from the models."""

    # What the table prints in its Field column: the last container and the field, as
    # `usage.cache_creation` — the way the document has always spelled it.
    path: str
    meaning: str
    evidence: tuple[Cited, ...]
    # Every record model that reaches this field. The Records column is `spell(carriers)`.
    carriers: tuple[type["Record"], ...]
    # How to reach the field inside a record, which is what lets a test check the citation.
    locate: tuple[Step, ...]


class Described(BaseModel):
    """Base of everything here: extra keys ride along, and aliases work by field name."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


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


class Block(Described):
    """One block of a `message.content` list. The docstring is the row the tables print."""

    BLOCK: ClassVar[ContentBlock]
    EVIDENCE: ClassVar[tuple[Cited, ...]]


class TextBlock(Block):
    """Prose, under `text`: the model's answer, or a prompt written in block form."""

    BLOCK = ContentBlock.TEXT
    EVIDENCE = (Cited(SPINE, "2.1.221"),)


class ThinkingBlock(Block):
    """The model's reasoning, under `thinking`, beside the `signature` that lets it be replayed."""

    BLOCK = ContentBlock.THINKING
    EVIDENCE = (Cited(SPINE, "2.1.221"),)


class ToolUseBlock(Block):
    """A local tool request. Most records contain one, but 23 records in the mycelia corpus
    contain two or more, so counting records undercounts calls (scanned 2026-08-07)."""

    BLOCK = ContentBlock.TOOL_USE
    EVIDENCE = (
        Cited(SPINE, "2.1.221"),
        Cited(PARALLEL_TOOLS, "2.1.211", note="two calls in one record"),
    )

    id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The call id. A `tool_result` block names it in `tool_use_id`, and a subagent's "
                "meta names it in `toolUseId`. Unique within a session, not across the store"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    name: Annotated[
        str | None,
        Field(default=None, description="The tool asked for, such as `Bash` or `Agent`"),
        Cited(SPINE, "2.1.221"),
    ]
    input: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "The arguments, shaped by the tool. On a `Skill` call it names the invoked skill "
                "in `skill`, with `args` on 81 of 326 corpus calls; that records invocation, "
                "while `attributionSkill` records what was loaded when the reply returned. They "
                "can disagree, and a skill reached through a slash command creates no `Skill` "
                "call (57 sessions, CC 2.1.195–2.1.221; scanned 2026-08-08)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
        Cited(scan="57 sessions, CC 2.1.195–2.1.221, scanned 2026-08-08", note="the `Skill` shape"),
    ]


class ServerToolUseBlock(Block):
    """A tool request Anthropic ran server-side, with the same fields as `tool_use`. It shares the
    assistant stream but joins no batch, so its own timestamp is the call's start. All 45 corpus
    blocks, across five sessions, call `advisor` with empty `input` (scanned 2026-08-07)."""

    BLOCK = ContentBlock.SERVER_TOOL_USE
    EVIDENCE = (Cited(SERVER_TOOLS, "2.1.201"),)


class AdvisorContent(Described):
    """What an `advisor_tool_result` block returned."""

    type: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Either `advisor_tool_result_error` or `advisor_redacted_result`. Neither shape "
                "carries readable output"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.201", note="holds both"),
    ]
    error_code: Annotated[
        str | None,
        Field(default=None, description="Why the advisor failed, on the error shape"),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]
    encrypted_content: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The advisor's answer, unreadable: the transcript records that it answered and "
                "nothing of what it said"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]


class AdvisorToolResultBlock(Block):
    """The answer to a `server_tool_use`, stored in the same assistant message rather than in a
    `user` record. The corpus contains answers for 44 of 45 calls; one call has no answer
    (scanned 2026-08-07)."""

    BLOCK = ContentBlock.ADVISOR_TOOL_RESULT
    EVIDENCE = (Cited(SERVER_TOOLS, "2.1.201", note="both result shapes and the unanswered call"),)

    content: Annotated[
        AdvisorContent | None,
        Field(default=None, description="The result object, whose `type` says which shape it is"),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]


class FallbackEnd(Described):
    """One side of a retry on another model."""

    model: Annotated[
        str | None,
        Field(default=None, description="The model this side of the retry names"),
        Cited(SERVER_TOOLS, "2.1.206"),
    ]


class FallbackBlock(Block):
    """A retry on another model. The block also carries a `to`, but all three corpus blocks occur
    in one session and agree with `message.model` there, so only `from` adds information
    (scanned 2026-08-07). This is not a `model_consent_fallback`, which changes the whole
    session's model."""

    BLOCK = ContentBlock.FALLBACK
    EVIDENCE = (Cited(SERVER_TOOLS, "2.1.206"),)

    from_: Annotated[
        FallbackEnd | None,
        Field(default=None, alias="from", description="The model the request first went to"),
        Cited(SERVER_TOOLS, "2.1.206"),
    ]


class ToolResultBlock(Block):
    """A local tool's reply, written in the `user` record that answers the call."""

    BLOCK = ContentBlock.TOOL_RESULT
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    tool_use_id: Annotated[
        str | None,
        Field(default=None, description="The `tool_use` block this answers"),
        Cited(SPINE, "2.1.221"),
    ]
    content: Annotated[
        str | list[Any] | None,
        Field(
            default=None,
            description=(
                "A string, or a list of `text`, `image`, and `tool_reference` blocks. Only text "
                "carries into `ToolCall.result`"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    is_error: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "Present when the tool failed. Success omits it: 66,653 of 154,169 corpus result "
                "blocks have no `is_error` (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class CacheCreation(Described):
    """Cache-creation tokens split by how long the cache entry lives."""

    ephemeral_5m_input_tokens: Annotated[
        int | None,
        Field(default=None, description="Tokens written to the five-minute cache"),
        Cited(SPINE, "2.1.221"),
    ]
    ephemeral_1h_input_tokens: Annotated[
        int | None,
        Field(default=None, description="Tokens written to the one-hour cache"),
        Cited(SPINE, "2.1.221"),
    ]


class Usage(Described):
    """One reply's token counts, and what the cost of a call is computed from."""

    input_tokens: Annotated[
        int | None,
        Field(default=None, description="Tokens sent that neither hit nor filled the cache"),
        Cited(SPINE, "2.1.221"),
    ]
    output_tokens: Annotated[
        int | None,
        Field(default=None, description="Tokens the model generated"),
        Cited(SPINE, "2.1.221"),
    ]
    cache_read_input_tokens: Annotated[
        int | None,
        Field(default=None, description="Tokens served from the cache"),
        Cited(SPINE, "2.1.221"),
    ]
    cache_creation_input_tokens: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Tokens written to the cache. It should equal the sum of the two `cache_creation` "
                "splits, but 53 of about 290,000 mycelia assistant records disagree, and cost "
                "uses the split (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    cache_creation: Annotated[
        CacheCreation | None,
        Field(
            default=None,
            description=(
                "Cache-creation tokens split by TTL. Every assistant record in the mycelia corpus "
                "has this object, so the absent shape remains unrecorded (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


# What every message says about its content list, which is one field on one base class.
_CONTENT = (
    "Either a string or a list of the blocks below. A `user` record whose list holds a "
    "`tool_result` is plumbing, not a prompt"
)


class Message(Described):
    """The API message a `user` or `assistant` record carried."""

    BLOCKS: ClassVar[tuple[type[Block], ...]]

    content: Annotated[
        str | list[Any] | None,
        Field(default=None, description=_CONTENT),
        Cited(SPINE, "2.1.220", note="for the block form"),
    ]


class UserMessage(Message):
    """What the operator, or Claude Code on their behalf, sent."""

    BLOCKS = (TextBlock, ToolResultBlock)


class AssistantMessage(Message):
    """One model reply, spread over as many records as it has content blocks."""

    BLOCKS = (
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        ServerToolUseBlock,
        AdvisorToolResultBlock,
        FallbackBlock,
    )

    id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The API reply id, and the key for merging records. One reply can span several "
                "records, one per content block; counting lines triples the API-call count"
            ),
        ),
        Cited(SPINE, "2.1.221", note="eight records for two replies"),
    ]
    model: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The model that answered. `<synthetic>` marks Claude Code's placeholder for an "
                "interrupt or a cancelled request: of about 290,000 corpus assistant records, "
                "205 are synthetic, all reporting zero tokens (scanned 2026-08-07)"
            ),
        ),
        Cited(SPINE, "2.1.201", note="holds a `<synthetic>` reply"),
    ]
    stop_reason: Annotated[
        str | None,
        Field(default=None, description="Why generation stopped, such as `tool_use` or `end_turn`"),
        Cited(SPINE, "2.1.221"),
    ]
    usage: Annotated[
        Usage | None,
        Field(
            default=None,
            description=(
                "Token usage for the whole reply. Every record sharing a `message.id` repeats the "
                "totals, so summing records multiplies usage by the number of chunks"
            ),
        ),
        Cited(SPINE, "2.1.221", note="five identical copies under one id"),
    ]


class ToolUseResult(Described):
    """The structured report Claude Code wrote beside a tool's result block."""

    persistedOutputPath: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The path to output too large for the transcript. Claude Code writes the full "
                "output to `<session>/tool-results/<name>.txt` and leaves a preview in `content`. "
                "The path is absolute, so only its file name travels; the corpus holds 321 such "
                "results (scanned 2026-08-07)"
            ),
        ),
        Cited(OFFLOAD, "2.1.220"),
    ]
    runId: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The fan-out id a `Workflow` call returns, matching the `wf_<id>` directory that "
                "holds its agents' transcripts. It is the only link from those transcripts to the "
                "call that launched them"
            ),
        ),
        Cited(WORKFLOW, "2.1.207"),
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

# Every block model. Order follows `ContentBlock`.
BLOCK_MODELS: tuple[type[Block], ...] = (
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ServerToolUseBlock,
    AdvisorToolResultBlock,
    FallbackBlock,
    ToolResultBlock,
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


# What the Records column says instead of naming all twelve.
EVERY_RECORD = "every record"


def spell(carriers: tuple[type[Record], ...]) -> tuple[str, ...]:
    """How the Records column names one field's carriers.

    Every record is said once; the system subtypes collapse to `system` when they all carry the
    field, and name themselves when only some do.
    """
    if set(carriers) == set(RECORD_MODELS):
        return (EVERY_RECORD,)
    system = {m for m in RECORD_MODELS if m.RECORD_TYPE is RecordType.SYSTEM}
    whole_system = system <= set(carriers)
    said: list[str] = []
    for model in RECORD_MODELS:
        if model not in carriers:
            continue
        if model in system and not whole_system and model.SUBTYPE is not None:
            name = f"{model.RECORD_TYPE.value} / {model.SUBTYPE.value}"
        else:
            name = model.RECORD_TYPE.value
        if name not in said:
            said.append(name)
    return tuple(said)


def _nested(annotation: Any) -> Iterator[type[Described]]:
    """Every described model an annotation can hold, unions and containers included."""
    if isinstance(annotation, type) and issubclass(annotation, Described):
        yield annotation
        return
    if isinstance(annotation, ForwardRef):
        raise TypeError(f"{annotation} was never resolved, so its fields would go undocumented")
    for argument in get_args(annotation):
        yield from _nested(argument)


def _prose(text: str | None) -> str:
    """One table cell: lines joined, indentation dropped, and no closing period.

    A block's meaning is its docstring, which ends in a period the way a docstring should; a
    field's is a `description`, which does not. The cells read the same either way.
    """
    joined = " ".join(line.strip() for line in (text or "").split("\n") if line.strip())
    return joined.removesuffix(".")


def _describe(
    model: type[Described], locate: tuple[Step, ...]
) -> Iterator[tuple[tuple[Step, ...], str, tuple[Cited, ...]]]:
    """Every documented field reachable from one model, with where it sits and what it claims."""
    for name, info in model.model_fields.items():
        here = (*locate, info.alias or name)
        evidence = tuple(item for item in info.metadata if isinstance(item, Cited))
        yield here, _prose(info.description), evidence
        for nested in _nested(info.annotation):
            yield from _describe(nested, here)
        if issubclass(model, Message) and name == "content":
            for block in model.BLOCKS:
                inside = (*here, Among(block.BLOCK))
                yield inside, _prose(block.__doc__), block.EVIDENCE
                yield from _describe(block, inside)


def _name(locate: tuple[Step, ...]) -> str:
    """The Field column's spelling: the last container and the field, as `usage.cache_creation`."""
    last = locate[-1]
    if isinstance(last, Among):
        return last.kind.value
    if len(locate) == 1:
        return last
    parent = locate[-2]
    return f"{parent.kind.value if isinstance(parent, Among) else parent}.{last}"


def documentation(models: tuple[type[Record], ...] = RECORD_MODELS) -> tuple[Documentation, ...]:
    """Every field the models document, in walk order, each with the records that carry it.

    `models` defaults to the registry, which is the only answer a document wants; naming other
    models is how a test asks what a model that is not registered would print.

    One field reached from several records is one row: the meaning and the evidence must be the
    same object of thought, so a second declaration that says something different crashes here
    rather than printing two rows with one name.
    """
    rows: dict[str, Documentation] = {}
    for model in models:
        for locate, meaning, evidence in _describe(model, ()):
            path = _name(locate)
            row = rows.get(path)
            if row is None:
                rows[path] = Documentation(path, meaning, evidence, (model,), locate)
                continue
            if (row.meaning, row.evidence, row.locate) != (meaning, evidence, locate):
                raise ValueError(f"`{path}` is documented twice, and the two disagree")
            rows[path] = row._replace(carriers=(*row.carriers, model))
    return tuple(rows.values())
