"""What ties the record models to the parser while the parser still reads dicts.

Both sides here are live source — no fixtures. The parser is closed-world: `record_types.py`
registers every record type, subtype and block kind it has seen and the readers crash on the
rest. `records/` describes those same shapes for `docs/schema.md`. Nothing makes the two agree
at runtime, so these leaves are the tie: a shape the parser learns must gain a model or a stated
reason, and a field the models describe must be one the parser reads or one named as observed
and unread.
"""

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from hyphae.extract import claude_code
from hyphae.extract.record_types import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    SystemSubtype,
)
from hyphae.extract.records import blocks, schema, shapes

# The models side, whose source names every documented field as a literal. Reading it as parser
# source would make "the parser reads this field" true of every field ever documented.
MODELS = "hyphae.extract.records"


def parsing_modules(entry: ModuleType) -> list[ModuleType]:
    """Every `hyphae.extract` module the extractor reaches, entry point first.

    Found by walking imports rather than named here, because the parser is split across modules
    and a list would go stale the next time it is split again — silently, since the leaves below
    read the source as one string and a missing module only ever makes them pass.
    """
    found = [entry]
    seen = {entry.__name__}
    for module in found:
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            name = node.module
            reached = name.startswith("hyphae.extract.")
            if (
                reached
                and name != MODELS
                and not name.startswith(f"{MODELS}.")
                and name not in seen
            ):
                seen.add(name)
                found.append(importlib.import_module(name))
    return found


# The parser's own source: what "the parser reads this field" is checked against, because the
# fields are raw camelCase spellings the parser can only reach as string literals. Concatenated
# over every module the extractor reaches, so a field's reader counts wherever it now lives.
PARSER_MODULES = parsing_modules(claude_code)
PARSER = "\n".join(inspect.getsource(module) for module in PARSER_MODULES)

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
        f"`{member}` is registered in record_types.py but has no model and no entry in UNMODELLED"
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


def test_no_module_outside_the_parser_source_reads_a_documented_field() -> None:
    # The vacuity gate for the leaves below. They read PARSER as one string, so a reader that
    # moves into a module the walk above never reaches makes them pass by finding nothing. Any
    # `extract/` module spelling a documented field is either part of that source or a surprise.
    # Only the camelCase spellings: `id` and `name` are words any module may hold for its own
    # reasons, but nothing writes `toolUseResult` except a reader of Claude Code's transcript.
    reached = {module.__file__ for module in PARSER_MODULES}
    fields = {field for field in documented_fields() if not field.islower()}
    # Recursively, because this gate was written after one split and the next one is exactly
    # what it would otherwise miss: a reader moved into a subpackage is a module the walk above
    # never reaches. `records/` is the one subpackage today, and it is the models side, which
    # names every documented field by design.
    models = Path(importlib.import_module(MODELS).__file__ or "").parent
    for path in Path(claude_code.__file__).parent.rglob("*.py"):
        if str(path) in reached or models in path.parents:
            continue
        source = path.read_text()
        read = {field for field in fields if f'"{field}"' in source}
        assert not read, (
            f"`{path.name}` reads {sorted(read)} but the parser source above never reaches it: "
            "the drift leaves would pass by not looking"
        )


def test_every_documented_field_is_one_the_parser_reads() -> None:
    # A row describing a field nothing reads is a claim about Claude Code that the extractor
    # cannot notice going wrong. Those exist and are worth documenting, so they are named in
    # OBSERVED_UNREAD one at a time, with the reason — never passed over in silence.
    for field, path in documented_fields().items():
        read = f'"{field}"' in PARSER
        assert read or field in shapes.OBSERVED_UNREAD, (
            f"`{path}` documents `{field}`, which no parser module reads: model it as "
            "observed and unread, or the row is describing a field nobody has looked at"
        )


def test_nothing_is_excused_as_unread_that_the_parser_reads() -> None:
    # The ratchet: when the parser starts reading a field, its excuse comes out.
    documented = documented_fields()
    for field, reason in shapes.OBSERVED_UNREAD.items():
        assert field in documented, f"OBSERVED_UNREAD names `{field}`, which no model documents"
        assert f'"{field}"' not in PARSER, f"the parser reads `{field}`, so its excuse is stale"
        assert reason, f"`{field}` is excused without a reason"
