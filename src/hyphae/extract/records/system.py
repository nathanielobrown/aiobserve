"""The `system` records: Claude Code's own notes to the transcript.

One model per subtype that carries fields of its own; the thin subtypes are archived unread
(`hyphae.extract.records.shapes.ARCHIVED_UNREAD`).
"""

from typing import Annotated, Any

from pydantic import Field

from hyphae.extract.records.base import MetaFlagged, SessionContext
from hyphae.extract.records.evidence import (
    COMPACTION,
    MODEL_ONLY,
    REGISTRY_ZOO,
    SPINE,
    Cited,
    Described,
)
from hyphae.extract.records.registry import RecordType, SystemSubtype


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
