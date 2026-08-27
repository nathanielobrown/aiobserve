"""What the record models claim: Claude Code's shapes, and a recording behind every claim.

The world here is the fixtures — recorded, redacted sessions — because a model that describes a
transcript format can only be checked against a transcript. Nothing in this tier invents a
record: the models say what Claude Code writes, so an invented record would let the models
describe a format nobody has ever seen.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from hyphae.extract.record_types import ContentBlock
from hyphae.extract.records import blocks, evidence, schema, shapes
from tests.conftest import FIXTURES

# The repository root, because a field's evidence cites a fixture the way a reader would type
# it: `tests/fixtures/spine/`, a path from the root rather than from this test.
REPO = FIXTURES.parent.parent
# The fixture holding one record of every registered type — what makes "every type validates"
# a claim about the registry rather than about whichever fixtures happen to be around.
ZOO = "tests/fixtures/registry_zoo/"
# The whole-object fixture: a session with its subagents, the widest set of shapes in one place.
SPINE = "tests/fixtures/spine/"


class _Missing:
    """A stand-in for "the record does not carry this field", which `None` cannot be."""


MISSING = _Missing()


def fixture_records(fixture: str) -> Iterator[dict[str, Any]]:
    """Every record of every transcript under one fixture directory, in file order."""
    for path in sorted((REPO / fixture).rglob("*.jsonl")):
        for line in path.read_text().split("\n"):
            if line.strip():
                yield json.loads(line)


def zoo_records() -> list[dict[str, Any]]:
    return list(fixture_records(ZOO))


def carries(record: dict[str, Any], model: type[shapes.Record]) -> bool:
    """Whether one record is of the kind `model` describes, subtype included."""
    if record["type"] != model.RECORD_TYPE:
        return False
    subtype = getattr(model, "SUBTYPE", None)
    return subtype is None or record.get("subtype") == subtype


def resolve(value: Any, steps: tuple[evidence.Step, ...]) -> Iterator[Any]:
    """Every value a documented field's locator reaches inside one record.

    Yields nothing when the record does not carry the field, which is what "the fixture does
    not show it" means — a key present with a null value still counts as carried.
    """
    if not steps:
        yield value
        return
    step, rest = steps[0], steps[1:]
    if isinstance(step, evidence.Among):
        for item in value if isinstance(value, list) else []:
            if isinstance(item, dict) and item.get("type") == step.kind:
                yield from resolve(item, rest)
    elif isinstance(value, dict) and step in value:
        yield from resolve(value[step], rest)


@pytest.mark.parametrize("record", zoo_records(), ids=lambda r: r.get("subtype") or r["type"])
def test_every_registered_record_type_validates_against_a_recorded_one(
    record: dict[str, Any],
) -> None:
    # The headline: the models claim to describe Claude Code's shapes, and only a recording can
    # support that claim. The zoo holds one record of every registered type, so every type is
    # either modelled — and its model accepts the real thing, field types and all — or named as
    # one no model describes.
    model = shapes.model_for(record)
    if model is None:
        kind = record.get("subtype") if record["type"] == "system" else record["type"]
        assert kind in shapes.UNMODELLED, f"{kind} has neither a model nor a stated reason"
        return
    parsed = model.model_validate(record)
    assert parsed.type == record["type"]


def test_a_field_claude_code_adds_later_rides_along() -> None:
    # `extra="allow"` is the whole posture: Claude Code adds fields without notice, and only the
    # record *types* are closed-world. An unknown key validates and is kept, rather than raising.
    recorded = next(r for r in fixture_records(SPINE) if r["type"] == "assistant")

    parsed = shapes.AssistantRecord.model_validate(recorded | {"whateverIsNext": 7})

    assert parsed.model_extra is not None
    assert parsed.model_extra["whateverIsNext"] == 7


def test_a_shared_field_is_declared_on_one_mixin() -> None:
    # Shared fields live once, on the mixin that says which records carry them: `uuid` belongs to
    # every conversation record, so no record model may redeclare it...
    assert "uuid" in shapes.Identified.__annotations__
    for model in (shapes.UserRecord, shapes.AssistantRecord, shapes.SystemRecord):
        assert "uuid" not in model.__annotations__
        assert "uuid" in model.model_fields
    # ...and the row the generator derives from that inheritance names every record that has one.
    uuid_row = next(doc for doc in schema.documentation() if doc.path == "uuid")
    assert schema.spell(uuid_row.carriers) == ("user", "assistant", "system")


def test_a_record_type_with_no_uuid_does_not_inherit_one() -> None:
    # The other side of the same claim, and the reason `timestamp` and `uuid` are separate
    # mixins: a pr-link record is timestamped and has no uuid at all.
    assert "timestamp" in shapes.PrLinkRecord.model_fields
    assert "uuid" not in shapes.PrLinkRecord.model_fields
    assert "timestamp" not in shapes.ForkContextRefRecord.model_fields


def test_every_documented_field_carries_its_meaning_and_its_evidence() -> None:
    # The rule `docs/schema.md` states in prose — every claim names a recording — as a property
    # of the models themselves, so the generator has nothing to fill a blank cell with.
    for doc in schema.documentation():
        assert doc.meaning, f"{doc.path} says nothing"
        assert doc.evidence, f"{doc.path} cites nothing"


def test_every_nested_field_names_exactly_one_container_the_tables_also_document() -> None:
    # A Field cell is the whole address a reader has. `content.type` was three of them: the
    # tables document a `tool_result.content`, an `advisor_tool_result.content`, and a `content`
    # of its own on system records, and the row named none of them. A nested row's container
    # must therefore resolve to one row, matched the way a reader matches it — by the container
    # name, wherever that row spells it from.
    names = {doc.path for doc in schema.documentation()}
    for name in sorted(names):
        if "." not in name:
            continue
        container = name.rsplit(".", 1)[0]
        holders = [
            other for other in names if other == container or other.endswith(f".{container}")
        ]
        assert len(holders) == 1, f"`{name}` sits under any of {holders}"


def test_a_field_inside_a_block_is_named_from_the_block() -> None:
    # What makes the addresses unique: a block is the one container a reader identifies by name
    # rather than by position, and two blocks can hold fields that agree on everything else.
    # The advisor result's `content` and the fallback's `from` are the recorded cases.
    names = {doc.path for doc in schema.documentation()}
    assert "advisor_tool_result.content.type" in names
    assert "fallback.from.model" in names


def test_every_cited_fixture_exists() -> None:
    # A citation to a fixture that was deleted or renamed is worse than no citation: it reads as
    # verified and is not.
    for doc in schema.documentation():
        for cite in doc.evidence:
            if cite.fixture:
                assert (REPO / cite.fixture).is_dir(), (
                    f"{doc.path} cites {cite.fixture}, which is not a fixture directory"
                )


@pytest.mark.parametrize("doc", schema.documentation(), ids=lambda doc: doc.path)
def test_every_citation_shows_the_field_in_the_fixture_it_names(doc: schema.Documentation) -> None:
    # The migration's own check, kept: each meaning moved out of `docs/schema.md` with the
    # fixture the document cited for it, and this is what says the citation was right. A field
    # cited as present appears in a record of a kind that carries it; a field cited as absent —
    # `entrypoint` on the oldest transcripts — is missing from records that have the rest.
    for cite in doc.evidence:
        if not cite.fixture:
            continue
        kin = [r for r in fixture_records(cite.fixture) if any(carries(r, m) for m in doc.carriers)]
        assert kin, f"{doc.path} cites {cite.fixture}, which holds no record that could carry it"
        shown = [r for r in kin if next(resolve(r, doc.locate), MISSING) is not MISSING]
        if cite.absent:
            assert not shown, f"{doc.path} is cited as absent from {cite.fixture}, but it is there"
            continue
        assert shown, f"{doc.path} is not in {cite.fixture}"
        # The version too, where the records carry one. The bookkeeping types — titles, pr-links,
        # a fork's opening record — carry no `version` field at all, and their fixture README is
        # what fixes the version, so there is nothing here to compare against.
        written = {r["version"] for r in shown if r.get("version")}
        if written and cite.version and "–" not in cite.version:
            assert cite.version in written, f"{doc.path} cites CC {cite.version}, unwritten there"


@pytest.mark.parametrize("block", blocks.BLOCK_MODELS, ids=lambda b: b.BLOCK.value)
def test_every_block_model_validates_a_recorded_block(block: type[blocks.Block]) -> None:
    # Blocks are not records, so the registry test above cannot reach them: they are validated
    # here, against every block of their kind in every fixture. A block model whose kind no
    # fixture holds cannot be here at all — it would have no evidence to cite.
    found = [
        item
        for record in every_record()
        for item in content_of(record)
        if item.get("type") == block.BLOCK
    ]
    assert found, f"no fixture holds a `{block.BLOCK.value}` block"
    for item in found:
        block.model_validate(item)


def every_record() -> Iterator[dict[str, Any]]:
    """Every record of every recorded fixture — `invented/` is not one of them."""
    for directory in sorted(FIXTURES.iterdir()):
        if directory.is_dir() and directory.name != "invented":
            yield from fixture_records(f"tests/fixtures/{directory.name}/")


def content_of(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The blocks of one record's message, or nothing when it carried a bare string."""
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def test_the_content_blocks_a_message_can_hold_are_the_ones_it_lists() -> None:
    # The union each message model declares is what the generator reads to say which records
    # carry a block, so a block recorded under a message that does not list it would document
    # the wrong records. Checked against every block in every fixture.
    listed: dict[type[BaseModel], set[ContentBlock]] = {
        shapes.UserRecord: {b.BLOCK for b in blocks.UserMessage.BLOCKS},
        shapes.AssistantRecord: {b.BLOCK for b in blocks.AssistantMessage.BLOCKS},
    }
    for record in every_record():
        model = shapes.model_for(record)
        if model not in listed:
            continue
        for item in content_of(record):
            kind = item["type"]
            assert kind in listed[model] or kind in shapes.UNMODELLED, (
                f"a `{kind}` block in a {record['type']} record that lists none"
            )
