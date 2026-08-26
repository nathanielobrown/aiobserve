"""What ties the record models to the parser while the parser still reads dicts.

Both sides here are live source — no fixtures. `claude_code.py` is closed-world: it registers
every record type, subtype and block kind it has seen and crashes on the rest. `records.py`
describes those same shapes for `docs/schema.md`. Nothing makes the two agree at runtime, so
these leaves are the tie: a shape the parser learns must gain a model or a stated reason, and a
field the models describe must be one the parser reads or one named as observed and unread.
"""

import inspect

import pytest

from aiobserve.extract import claude_code
from aiobserve.extract.claude_code import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    SystemSubtype,
)
from aiobserve.extract.records import blocks, schema, shapes

# The parser's own source: what "the parser reads this field" is checked against, because the
# fields are raw camelCase spellings the parser can only reach as string literals.
PARSER = inspect.getsource(claude_code)

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


@pytest.mark.parametrize("member", REGISTERED, ids=lambda m: f"{type(m).__name__}.{m.value}")
def test_every_registered_shape_has_a_model_or_a_stated_reason(member: str) -> None:
    # The headline: this is what keeps the two artifacts honest. A record type, subtype or block
    # kind the parser learns tomorrow lands here as an undescribed shape, and the run stops until
    # someone writes the model or writes down why there is nothing to describe.
    assert member in modelled() or member in shapes.UNMODELLED, (
        f"`{member}` is registered in claude_code.py but has no model and no entry in UNMODELLED"
    )


def test_no_reason_is_left_for_a_shape_that_no_longer_exists() -> None:
    # The other direction, so the excuse list shrinks as models arrive rather than rotting: every
    # UNMODELLED key is a live registry member, and none of them has a model after all.
    registered = {member.value for member in REGISTERED}
    for kind, reason in shapes.UNMODELLED.items():
        assert kind in registered, f"UNMODELLED names `{kind}`, which no registry holds"
        assert kind not in modelled(), f"`{kind}` has a model, so its reason is stale"
        assert reason, f"`{kind}` is excused without a reason"


def documented_fields() -> dict[str, str]:
    """Every raw field name the models document, keyed to the row that documents it.

    Block rows are left out: a block kind is a registry member, covered by the leaves above.
    """
    return {
        doc.locate[-1]: doc.path
        for doc in schema.documentation()
        if isinstance(doc.locate[-1], str)
    }


def test_every_documented_field_is_one_the_parser_reads() -> None:
    # A row describing a field nothing reads is a claim about Claude Code that the extractor
    # cannot notice going wrong. Those exist and are worth documenting, so they are named in
    # OBSERVED_UNREAD one at a time, with the reason — never passed over in silence.
    for field, path in documented_fields().items():
        read = f'"{field}"' in PARSER
        assert read or field in shapes.OBSERVED_UNREAD, (
            f"`{path}` documents `{field}`, which claude_code.py never reads: model it as "
            "observed and unread, or the row is describing a field nobody has looked at"
        )


def test_nothing_is_excused_as_unread_that_the_parser_reads() -> None:
    # The ratchet: when the parser starts reading a field, its excuse comes out.
    documented = documented_fields()
    for field, reason in shapes.OBSERVED_UNREAD.items():
        assert field in documented, f"OBSERVED_UNREAD names `{field}`, which no model documents"
        assert f'"{field}"' not in PARSER, f"claude_code.py reads `{field}`, so its excuse is stale"
        assert reason, f"`{field}` is excused without a reason"
