"""The blocks a `message.content` list holds, and the messages that hold them.

A block model's docstring is the meaning its own table row prints, and `BLOCK` names the
registered kind it describes, which is what ties this file to `claude_code.py`'s registry.
"""

from typing import Annotated, Any, ClassVar

from pydantic import Field

from aiobserve.extract.claude_code import ContentBlock
from aiobserve.extract.records.evidence import (
    OFFLOAD,
    PARALLEL_TOOLS,
    SERVER_TOOLS,
    SPINE,
    WORKFLOW,
    Cited,
    Described,
)


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
                "205 are synthetic, all reporting zero tokens and omitting `usage.inference_geo` "
                "(scanned 2026-08-07)"
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
