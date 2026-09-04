"""The records that keep the session's books rather than converse in it.

Its titles, the names it gave its agent runs, the pull requests it opened, and the transcript
a fork carried by reference.
"""

from typing import Annotated

from pydantic import Field

from hyphae.extract.records.base import (
    AGENT_ID,
    AGENT_ID_EVIDENCE,
    Record,
    SessionScoped,
    Timestamped,
)
from hyphae.extract.records.evidence import (
    FORK_BYREF,
    LEGACY_TITLE,
    SPINE,
    Cited,
)
from hyphae.extract.records.registry import RecordType


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
        Field(default=None, description=AGENT_ID),
        AGENT_ID_EVIDENCE,
    ]
