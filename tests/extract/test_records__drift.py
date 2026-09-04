"""What ties the closed-world registry to the models that describe it.

Both sides here are live source — no fixtures. `records/registry.py` registers every record type,
subtype and block kind the parser has seen and crashes on the rest. `records/` describes those
same shapes. Nothing makes the two agree at runtime, so these leaves are the tie: a shape the
parser learns must gain a model or a stated reason.

Nothing here asks whether the parser reads a documented field. The models declare every field a
record carries, read or not, so "documented but unread" now describes most of the schema rather
than a handful of exceptions worth naming; `records/unknown.py` is what holds the declarations to
the recordings instead.
"""

import pytest

from hyphae.extract.records import blocks, shapes
from hyphae.extract.records.registry import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    SystemSubtype,
)

# Every closed-world registry a record model could describe, and how a member is spelled when a
# model claims it.
REGISTERED = (
    *RecordType,
    *ArchiveRecordType,
    *SystemSubtype,
    *ContentBlock,
)


def modelled() -> set[str]:
    """Every registered value some model describes: record types, subtypes, and block kinds."""
    return {
        *(model.RECORD_TYPE.value for model in shapes.RECORD_MODELS),
        *(model.SUBTYPE.value for model in shapes.RECORD_MODELS if model.SUBTYPE is not None),
        *(block.BLOCK.value for block in blocks.BLOCK_MODELS),
    }


def excused() -> dict[str, str]:
    """Every registered value described by a stated reason rather than by a model."""
    return {
        **{kind.value: reason for kind, reason in shapes.ARCHIVED_UNREAD.items()},
        **{kind.value: reason for kind, reason in blocks.UNCITED_BLOCKS.items()},
    }


@pytest.mark.parametrize("member", REGISTERED, ids=lambda m: f"{type(m).__name__}.{m.value}")
def test_every_registered_shape_has_a_model_or_a_stated_reason(member: str) -> None:
    # The headline: this is what keeps the two artifacts honest. A record type, subtype or block
    # kind the parser learns tomorrow lands here as an undescribed shape, and the run stops until
    # someone writes the model or writes down why there is nothing to describe.
    assert member in modelled() or member in excused(), (
        f"`{member}` is registered in records/registry.py but has no model, no ARCHIVED_UNREAD "
        "reason and no UNCITED_BLOCKS reason"
    )


def test_no_reason_is_left_for_a_shape_that_no_longer_exists() -> None:
    # The other direction, so the excuse lists shrink as models arrive rather than rotting: every
    # key is a live registry member, and none of them has a model after all.
    registered = {member.value for member in REGISTERED}
    for kind, reason in excused().items():
        assert kind in registered, f"`{kind}` is excused, but no registry holds it"
        assert kind not in modelled(), f"`{kind}` has a model, so its reason is stale"
        assert reason, f"`{kind}` is excused without a reason"
