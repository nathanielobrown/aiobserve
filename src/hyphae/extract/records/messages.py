"""The API message a conversation record carried, and the token counts beside it.

`content` is the list of blocks in `blocks.py`; the two subclasses differ only in which union
that list dispatches to, which is the one thing the reader is here for.
"""

from typing import Annotated, Any

from pydantic import Field

from hyphae.extract.records.blocks import (
    AdvisorToolResultBlock,
    FallbackBlock,
    ImageBlock,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from hyphae.extract.records.evidence import (
    CENSUS,
    OFFLOAD,
    PARALLEL_TOOLS,
    SPINE,
    WORKFLOW,
    Cited,
    Described,
)


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


class OutputTokensDetails(Described):
    """What the generated tokens were spent on."""

    thinking_tokens: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "How many of `output_tokens` the model spent thinking. Across the 114 corpus "
                "records carrying the object it runs from 0 to 3,241, never above "
                "`output_tokens`, so it is a share of that total and not an addition to it"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.259` writes it"),
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
    output_tokens_details: Annotated[
        OutputTokensDetails | None,
        Field(
            default=None,
            description=(
                "How the generated tokens break down. Claude Code added it late: 114 corpus "
                "records in 2 sessions carry it, all written by `2.1.259`"
            ),
        ),
        Cited(scan=CENSUS),
    ]
    service_tier: Annotated[
        str | None,
        Field(default=None, description="The API service tier the reply was served on"),
        Cited(SPINE, "2.1.221", note="`standard` wherever the fixtures leave it unredacted"),
    ]
    speed: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The speed tier the reply was served at. Absent from 30 of the 108 fixture "
                "replies, across versions that carry it elsewhere, so its absence is not a "
                "version fact"
            ),
        ),
        Cited(SPINE, "2.1.221", note="`standard` wherever the fixtures leave it unredacted"),
    ]
    inference_geo: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Where inference ran, or `not_available`. The one `<synthetic>` reply — Claude "
                "Code's own placeholder rather than a model answer — nulls it, along with "
                "`service_tier`, `speed` and `iterations`"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    iterations: Annotated[
        list[dict[str, Any]] | None,
        Field(
            default=None,
            description=(
                "Token counts for each pass a reply took, in this object's own shape. Cost uses "
                "the totals above, and nothing has opened these, so their interior is undeclared"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    server_tool_use: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "How many server-side tool requests the reply made, by kind. Zero on every "
                "fixture reply, `server_tools/` included, so a non-zero count is unrecorded"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]


# What every message says about its content list, which is one field on one base class.
_CONTENT = (
    "Either a string or a list of the blocks below. A `user` record whose list holds a "
    "`tool_result` is plumbing, not a prompt"
)


# The one citation both content declarations carry, so the tables print them as one row.
_CONTENT_EVIDENCE = Cited(SPINE, "2.1.220", note="for the block form")


class Message(Described):
    """The API message a `user` or `assistant` record carried.

    `content` sits on the two subclasses rather than here: the block kinds a `user` message holds
    and the ones an `assistant` message holds are different unions, and the union is what
    dispatches the list.
    """

    role: Annotated[
        str | None,
        Field(
            default=None,
            description="`user` or `assistant`, repeating what the record's own `type` says",
        ),
        Cited(SPINE, "2.1.221"),
    ]


class UserMessage(Message):
    """What the operator, or Claude Code on their behalf, sent."""

    content: Annotated[
        str
        | list[Annotated[TextBlock | ToolResultBlock | ImageBlock, Field(discriminator="type")]]
        | None,
        Field(default=None, description=_CONTENT),
        _CONTENT_EVIDENCE,
    ]


class AssistantMessage(Message):
    """One model reply, spread over as many records as it has content blocks."""

    content: Annotated[
        str
        | list[
            Annotated[
                TextBlock
                | ThinkingBlock
                | ToolUseBlock
                | ServerToolUseBlock
                | AdvisorToolResultBlock
                | FallbackBlock,
                Field(discriminator="type"),
            ]
        ]
        | None,
        Field(default=None, description=_CONTENT),
        _CONTENT_EVIDENCE,
    ]
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
    type: Annotated[
        str | None,
        Field(
            default=None,
            description="The API envelope's own kind: `message` on every fixture reply",
        ),
        Cited(SPINE, "2.1.221"),
    ]
    stop_sequence: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "The stop sequence that ended generation. Null on every fixture reply but one, "
                "which carries an empty string, so a real sequence is unrecorded"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    stop_details: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "More about why generation stopped, beside `stop_reason`. Null on every fixture "
                "reply, so its interior is unrecorded as well as undeclared"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    diagnostics: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Why the prompt cache missed, when it did: a `cache_miss_reason` naming the "
                "cause and what it cost. Null when the cache hit. Nothing has opened it"
            ),
        ),
        Cited(SPINE, "2.1.221"),
    ]
    container: Annotated[
        Any,
        Field(
            default=None,
            description=(
                "The container a code-execution reply ran in. Recorded once, as null, so its "
                "shape is unrecorded"
            ),
        ),
        Cited(SPINE, "2.1.201"),
    ]
    context_management: Annotated[
        dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "What context management did to the request, as an `applied_edits` list. "
                "Nothing has opened it, so its interior is undeclared"
            ),
        ),
        Cited(PARALLEL_TOOLS, "2.1.211"),
    ]


class ToolUseResult(Described):
    """The structured report Claude Code wrote beside a tool's result block.

    Only the two fields readers open are declared. The rest of the object is the tool's, one key
    set per tool and open once an MCP tool writes one, so a new key there is a tool changing its
    report rather than Claude Code changing the transcript.
    """

    OPAQUE = (
        "the tool's own report: one key set per tool, an open set, keyed by nothing "
        "the value carries"
    )

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
