"""Every block a `content` list can hold, one model per registered kind.

A block model's docstring is the meaning its own table row prints, and `BLOCK` names the
registered kind it describes, which is what ties this file to the registry beside it. The
messages whose lists dispatch to these models are in `messages.py`.
"""

from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field

from hyphae.extract.records.evidence import (
    CENSUS,
    PARALLEL_TOOLS,
    SERVER_TOOLS,
    SPINE,
    Cited,
    Described,
)
from hyphae.extract.records.registry import ContentBlock, ResultBlock

# What every discriminator says. It is the field pydantic dispatches a content list on, and the
# tables name a member's fields from the kind it carries — `tool_use.id` — so the kind is the
# row's own name and gets no row of its own.
_KIND = "The block kind, which is what dispatches the block to the model below"

# What a picture is recorded as, on both members that hold one.
_SOURCE = (
    "The picture itself, as a `type`, a `media_type` and base64 `data`. Nothing has opened it, "
    "so its interior is undeclared — and its `data` is the largest value a transcript holds"
)
# Neither form of picture is in a fixture: a redacted excerpt would carry the image bytes whole.
_IMAGE_EVIDENCE = (
    Cited(
        scan=CENSUS,
        note="3 blocks in a `user` content list, 633 inside a `tool_result`",
    ),
)


class Kinded(Described):
    """One member of a `content` list, reached by its own `type` rather than by position.

    Pydantic dispatches the list on that field, so every member declares it as a `Literal` of the
    registered kind and the union does the rest.
    """

    BLOCK: ClassVar[ContentBlock | ResultBlock]
    EVIDENCE: ClassVar[tuple[Cited, ...]]


class Block(Kinded):
    """One block of a `message.content` list. The docstring is the row the tables print."""

    BLOCK: ClassVar[ContentBlock]


class ResultPart(Kinded):
    """One block of a block-form `tool_result`'s own content list."""

    BLOCK: ClassVar[ResultBlock]


class TextBlock(Block):
    """Prose, under `text`: the model's answer, or a prompt written in block form."""

    BLOCK = ContentBlock.TEXT
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    type: Annotated[Literal[ContentBlock.TEXT], Field(description=_KIND)]
    text: Annotated[
        str | None,
        Field(default=None, description="The prose itself, which can be empty"),
        Cited(SPINE, "2.1.221"),
    ]


class ThinkingBlock(Block):
    """The model's reasoning, under `thinking`, beside the `signature` that lets it be replayed."""

    BLOCK = ContentBlock.THINKING
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    type: Annotated[Literal[ContentBlock.THINKING], Field(description=_KIND)]
    thinking: Annotated[
        str | None,
        Field(default=None, description="The reasoning text, which no store column carries"),
        Cited(SPINE, "2.1.221"),
    ]
    signature: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The opaque token that lets the reasoning be replayed to the model. Every "
                "fixture `thinking` block carries one"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class ToolUseBlock(Block):
    """A local tool request. Most records contain one, but 23 records in the mycelia corpus
    contain two or more, so counting records undercounts calls (scanned 2026-08-07)."""

    BLOCK = ContentBlock.TOOL_USE
    EVIDENCE = (
        Cited(SPINE, "2.1.221"),
        Cited(PARALLEL_TOOLS, "2.1.211", note="two calls in one record"),
    )

    type: Annotated[Literal[ContentBlock.TOOL_USE], Field(description=_KIND)]
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
    caller: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Who asked for the call, as an object holding a `kind`. Every one of the 214,583 "
                "corpus blocks says `direct` (scanned 2026-09-04), and nothing has opened it, so "
                "its interior is undeclared"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


class ServerToolUseBlock(Block):
    """A tool request Anthropic ran server-side, with the same fields as `tool_use`. It shares the
    assistant stream but joins no batch, so its own timestamp is the call's start. All 45 corpus
    blocks, across five sessions, call `advisor` with empty `input` (scanned 2026-08-07)."""

    BLOCK = ContentBlock.SERVER_TOOL_USE
    EVIDENCE = (Cited(SERVER_TOOLS, "2.1.201"),)

    type: Annotated[Literal[ContentBlock.SERVER_TOOL_USE], Field(description=_KIND)]
    id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The call id, which the `advisor_tool_result` block answering it repeats in "
                "`tool_use_id`"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]
    name: Annotated[
        str | None,
        Field(
            default=None,
            description="The server-side tool asked for; every corpus block says `advisor`",
        ),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]
    input: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "The arguments, empty on every corpus block, so no argument shape is recorded"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]


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

    type: Annotated[Literal[ContentBlock.ADVISOR_TOOL_RESULT], Field(description=_KIND)]
    tool_use_id: Annotated[
        str | None,
        Field(default=None, description="The `server_tool_use` block this answers"),
        Cited(SERVER_TOOLS, "2.1.201"),
    ]
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

    type: Annotated[Literal[ContentBlock.FALLBACK], Field(description=_KIND)]
    from_: Annotated[
        FallbackEnd | None,
        Field(default=None, alias="from", description="The model the request first went to"),
        Cited(SERVER_TOOLS, "2.1.206"),
    ]
    to: Annotated[
        FallbackEnd | None,
        Field(
            default=None,
            description=(
                "The model it retried on. All three corpus blocks agree with `message.model` "
                "here, so nothing reads it (scanned 2026-08-07)"
            ),
        ),
        Cited(SERVER_TOOLS, "2.1.206"),
    ]


class TextResult(ResultPart):
    """Prose a tool returned, inside a block-form `tool_result`. The only part that carries into
    `ToolCall.result`."""

    BLOCK = ResultBlock.TEXT
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    type: Annotated[Literal[ResultBlock.TEXT], Field(description=_KIND)]
    text: Annotated[
        str | None,
        Field(default=None, description="What the tool printed"),
        Cited(SPINE, "2.1.221"),
    ]


class ImageResult(ResultPart):
    """A picture a tool returned, inside a block-form `tool_result`. It carries no text, so
    nothing of it reaches `ToolCall.result`."""

    BLOCK = ResultBlock.IMAGE
    EVIDENCE = _IMAGE_EVIDENCE

    type: Annotated[Literal[ResultBlock.IMAGE], Field(description=_KIND)]
    source: Annotated[
        dict[str, Any] | None,
        Field(default=None, description=_SOURCE),
        *_IMAGE_EVIDENCE,
    ]


class ToolReferenceResult(ResultPart):
    """A tool the result pointed at rather than anything the tool said."""

    BLOCK = ResultBlock.TOOL_REFERENCE
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    type: Annotated[Literal[ResultBlock.TOOL_REFERENCE], Field(description=_KIND)]
    tool_name: Annotated[
        str | None,
        Field(default=None, description="The tool the result named"),
        Cited(SPINE, "2.1.221"),
    ]


# One part of a block-form `tool_result`, dispatched on its own `type` the way a block is.
type ResultPartUnion = Annotated[
    TextResult | ImageResult | ToolReferenceResult, Field(discriminator="type")
]


class ToolResultBlock(Block):
    """A local tool's reply, written in the `user` record that answers the call."""

    BLOCK = ContentBlock.TOOL_RESULT
    EVIDENCE = (Cited(SPINE, "2.1.221"),)

    type: Annotated[Literal[ContentBlock.TOOL_RESULT], Field(description=_KIND)]
    tool_use_id: Annotated[
        str | None,
        Field(default=None, description="The `tool_use` block this answers"),
        Cited(SPINE, "2.1.221"),
    ]
    content: Annotated[
        str | list[ResultPartUnion] | None,
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


class ImageBlock(Block):
    """A picture in a message's own content list, pasted by the operator rather than returned by
    a tool. Three records in the canonical store hold one (scanned 2026-09-04)."""

    BLOCK = ContentBlock.IMAGE
    EVIDENCE = _IMAGE_EVIDENCE

    type: Annotated[Literal[ContentBlock.IMAGE], Field(description=_KIND)]
    source: Annotated[
        dict[str, Any] | None,
        Field(default=None, description=_SOURCE),
        *_IMAGE_EVIDENCE,
    ]


# Registered block kinds no model describes, each with the reason. Empty: every kind the
# registry holds is now a member of one of the two unions above, which is what makes a list
# dispatch total.
UNCITED_BLOCKS: dict[ContentBlock, str] = {}

# Every block model. Order follows `ContentBlock`.
BLOCK_MODELS: tuple[type[Block], ...] = (
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ServerToolUseBlock,
    AdvisorToolResultBlock,
    FallbackBlock,
    ImageBlock,
    ToolResultBlock,
)

# Every model a block-form `tool_result`'s own content list dispatches to. Order follows
# `ResultBlock`.
RESULT_MODELS: tuple[type[ResultPart], ...] = (TextResult, ImageResult, ToolReferenceResult)
