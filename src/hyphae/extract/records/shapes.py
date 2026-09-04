"""Every record shape in one roster, and the dispatch from a raw record to its model.

The families live beside this module: `base` holds the mixin ladder, and `conversation`,
`system` and `bookkeeping` hold the models themselves. `ArchivedRecord` here takes every
registered kind no reader opens.
"""

from typing import Any

from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.records.base import Record, SessionContext
from hyphae.extract.records.bookkeeping import (
    AgentNameRecord,
    AiTitleRecord,
    CustomTitleRecord,
    ForkContextRefRecord,
    PrLinkRecord,
)
from hyphae.extract.records.conversation import AssistantRecord, UserRecord
from hyphae.extract.records.registry import ArchiveRecordType, RecordType, SystemSubtype
from hyphae.extract.records.system import (
    CompactBoundaryRecord,
    LocalCommandRecord,
    ModelConsentFallbackRecord,
    SystemRecord,
    TurnDurationRecord,
)


class ArchivedRecord(SessionContext):
    """A kind the store keeps verbatim, whose own fields no reader opens.

    It extends `SessionContext` because the envelope is read off every kind that carries one:
    `raw_record` takes `uuid` and `timestamp`, and `session_of` takes `cwd`, `gitBranch`,
    `version` and `entrypoint` from the first record that has them, which for five of the
    3,647 threads in the store is a thin `system` subtype (scanned 2026-09-04; 24,704
    `attachment` records carry the same four). Past the envelope it claims nothing: the rest
    of its keys are the archive's, kept whole rather than described. It is outside
    `RECORD_MODELS`, so it prints no row in `docs/schema.md`.
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
