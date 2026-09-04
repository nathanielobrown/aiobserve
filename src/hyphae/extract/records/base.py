"""The ladder every record model stands on, and the mixins that say who carries what.

Which records carry a field is derived from inheritance rather than written down: a shared
field lives on the mixin whose subclasses have it, so nothing states the set twice.
"""

from typing import Annotated, ClassVar

from pydantic import Field

from hyphae.extract.records.evidence import (
    CENSUS,
    DUP_UUID,
    LEGACY_ENTRYPOINT,
    REGISTRY_ZOO,
    RESUME_PAIR,
    SPINE,
    Cited,
    Described,
)
from hyphae.extract.records.registry import RecordType, SystemSubtype


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


# What `agentId` says on both records that carry one. They sit in different families, so the
# words live here with the ladder rather than beside either of them.
AGENT_ID = (
    "The agent run the record belongs to. A subagent's transcript is "
    "`<session>/subagents/agent-<agentId>.jsonl`, so the id is its file name without the prefix"
)
AGENT_ID_EVIDENCE = Cited(SPINE, "2.1.221", note="every record of each subagent thread")


class ForkedFrom(Described):
    """The transcript a fork branched from, and the record it branched at."""

    sessionId: Annotated[
        str | None,
        Field(default=None, description="The session the fork was cut from"),
        Cited(scan=CENSUS),
    ]
    messageUuid: Annotated[
        str | None,
        Field(default=None, description="The record in that session the fork was cut at"),
        Cited(scan=CENSUS),
    ]


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
        Field(default=None, description=AGENT_ID),
        AGENT_ID_EVIDENCE,
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
    forkedFrom: Annotated[
        ForkedFrom | None,
        Field(
            default=None,
            description=(
                "Where the session was forked from, on every record the fork carried over. One "
                "corpus session has it, on 299 records here and 151 more that are archived "
                "unread. Nothing reads it: a fork's copied rows are found by their content"
            ),
        ),
        Cited(scan=CENSUS, note="only `2.1.220` writes it"),
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
