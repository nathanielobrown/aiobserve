"""What the record models claim: Claude Code's shapes, and a recording behind every claim.

The world here is the fixtures — recorded, redacted sessions — because a model that describes a
transcript format can only be checked against a transcript. Nothing in this tier invents a
record: the models say what Claude Code writes, so an invented record would let the models
describe a format nobody has ever seen.

Two leaves read live source instead: `records/registry.py` names every shape the parser has
seen, `records/` describes those shapes, and nothing makes the two agree at runtime.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from hyphae import extract
from hyphae.extract.records import (
    base,
    blocks,
    bookkeeping,
    conversation,
    evidence,
    field_tables,
    messages,
    shapes,
    system,
)
from hyphae.extract.records.registry import (
    ArchiveRecordType,
    ContentBlock,
    RecordType,
    ResultBlock,
    SystemSubtype,
)
from hyphae.extract.records.unknown import UnknownFields
from hyphae.extract.transcript import read_lines
from tests.conftest import FIXTURES, corpus_transcripts

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


def carries(record: dict[str, Any], model: type[base.Record]) -> bool:
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
    # support that claim. The zoo holds one record of every registered type, so every type
    # resolves to a model — `model_for` is total — and that model accepts the real thing, field
    # types and all. A kind read by nothing resolves to `ArchivedRecord`, which claims only the
    # envelope.
    model = shapes.model_for(record)
    parsed = model.model_validate(record)
    assert parsed.type == record["type"]


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
    # What keeps the registry and the models honest. A record type, subtype or block kind the
    # parser learns tomorrow lands here as an undescribed shape, and the run stops until someone
    # writes the model or writes down why there is nothing to describe.
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


def test_an_archived_kind_keeps_its_envelope_and_carries_the_rest_whole() -> None:
    # `ArchivedRecord` is the model for a kind no reader opens, and what it declares is exactly
    # what `raw_record` writes: the type, the uuid and the timestamp. An `attachment` — 24k of
    # them in the store — carries all three, and everything else it holds rides along as extras
    # rather than as a claim. A `file-history-snapshot` carries neither uuid nor timestamp, which
    # is why those two are optional and why the model claims nothing about which kinds have them.
    zoo = {record["type"]: record for record in zoo_records()}

    attachment = shapes.ArchivedRecord.model_validate(zoo["attachment"])
    assert attachment.type == "attachment"
    assert attachment.uuid == zoo["attachment"]["uuid"]
    assert attachment.timestamp == zoo["attachment"]["timestamp"]
    assert attachment.model_extra, "the attachment's own keys were dropped rather than kept"

    snapshot = shapes.ArchivedRecord.model_validate(zoo["file-history-snapshot"])
    assert snapshot.uuid is None
    assert snapshot.timestamp is None
    assert snapshot.model_extra


def test_a_thin_system_subtype_is_archived_rather_than_read_as_a_system_record() -> None:
    # `SystemRecord` used to be the fallback for any `system` record, which made it the model for
    # subtypes nobody had looked at. Now it is the base of the four subtypes with models, and the
    # six thin ones route to `ArchivedRecord` — the walk stops there, so their fields stay the
    # archive's rather than becoming undeclared fields the corpus leaf below would report.
    thin = [record for record in zoo_records() if record["type"] == "system"]
    archived = [r for r in thin if r["subtype"] in shapes.ARCHIVED_UNREAD]
    assert len(archived) == 6, "the zoo no longer holds one record of every thin system subtype"
    for record in archived:
        assert shapes.model_for(record) is shapes.ArchivedRecord
    for record in thin:
        if record["subtype"] not in shapes.ARCHIVED_UNREAD:
            assert issubclass(shapes.model_for(record), system.SystemRecord)


def test_exactly_two_models_stop_the_walk_and_each_says_why() -> None:
    # `OPAQUE` silences the unknown-field walk under it, so it is the one thing that can make the
    # corpus leaf below pass by describing less rather than by declaring more. Two models may do
    # it: a tool's own report, whose key set is the tool's and not Claude Code's, and an archived
    # kind nobody reads. The set is asserted whole, so a third arrival fails here first.
    opaque = {model for model in described_models() if model.OPAQUE}

    assert opaque == {shapes.ArchivedRecord, messages.ToolUseResult}
    for model in opaque:
        assert model.OPAQUE.strip(), f"{model.__name__} is opaque without a stated reason"


def described_models() -> Iterator[type[evidence.Described]]:
    """Every model in the package, however deeply subclassed."""
    stack = list(evidence.Described.__subclasses__())
    while stack:
        model = stack.pop()
        stack.extend(model.__subclasses__())
        yield model


def test_an_opaque_model_declares_only_the_fields_a_reader_opens() -> None:
    # The other half of opacity: because the walk stops, a field written here is never checked
    # against a recording by the corpus leaf, so the two the readers open are the two that may be
    # here. `toolUseResult` carries around 39 keys across the fixtures; the model claims two, and
    # the citation leaves above prove both against the fixture each names.
    assert set(messages.ToolUseResult.model_fields) == {"persistedOutputPath", "runId"}


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (conversation.UserRecord, "thinkingMetadata"),
        (conversation.UserRecord, "origin"),
        (system.CompactMetadata, "preservedSegment"),
        (messages.AssistantMessage, "stop_details"),
        (messages.AssistantMessage, "context_management"),
        (messages.Usage, "server_tool_use"),
    ],
    ids=lambda arg: arg if isinstance(arg, str) else arg.__name__,
)
def test_an_object_no_reader_opens_is_declared_as_a_dict_rather_than_modelled(
    model: type[evidence.Described], field: str
) -> None:
    # The rule that keeps the schema honest about its own depth: an object gets a model when a
    # reader opens it, and until then it is one declared `dict` leaf with a citation. The walk
    # treats it as a value, so the keys inside it are neither claimed nor reported — which is the
    # difference between "we looked and there is nothing" and "nobody has looked yet".
    assert model.model_fields[field].annotation == (dict[str, Any] | None)


def test_a_field_claude_code_adds_later_rides_along() -> None:
    # `extra="allow"` is the whole posture: Claude Code adds fields without notice, and only the
    # record *types* are closed-world. An unknown key validates and is kept, rather than raising.
    recorded = next(r for r in fixture_records(SPINE) if r["type"] == "assistant")

    parsed = conversation.AssistantRecord.model_validate(recorded | {"whateverIsNext": 7})

    assert parsed.model_extra is not None
    assert parsed.model_extra["whateverIsNext"] == 7


def test_a_shared_field_is_declared_on_one_mixin() -> None:
    # Shared fields live once, on the mixin that says which records carry them: `uuid` belongs to
    # every conversation record, so no record model may redeclare it...
    assert "uuid" in base.Identified.__annotations__
    for model in (conversation.UserRecord, conversation.AssistantRecord, system.SystemRecord):
        assert "uuid" not in model.__annotations__
        assert "uuid" in model.model_fields
    # ...and the row the generator derives from that inheritance names every record that has one.
    uuid_row = next(doc for doc in field_tables.documentation() if doc.path == "uuid")
    assert field_tables.spell(uuid_row.carriers) == ("user", "assistant", "system")


def test_a_record_type_with_no_uuid_does_not_inherit_one() -> None:
    # The other side of the same claim, and the reason `timestamp` and `uuid` are separate
    # mixins: a pr-link record is timestamped and has no uuid at all.
    assert "timestamp" in bookkeeping.PrLinkRecord.model_fields
    assert "uuid" not in bookkeeping.PrLinkRecord.model_fields
    assert "timestamp" not in bookkeeping.ForkContextRefRecord.model_fields


def test_every_documented_field_carries_its_meaning_and_its_evidence() -> None:
    # The rule `docs/schema.md` states in prose — every claim names a recording — as a property
    # of the models themselves, so the generator has nothing to fill a blank cell with.
    for doc in field_tables.documentation():
        assert doc.meaning, f"{doc.path} says nothing"
        assert doc.evidence, f"{doc.path} cites nothing"


def test_every_nested_field_names_exactly_one_container_the_tables_also_document() -> None:
    # A Field cell is the whole address a reader has. `content.type` was three of them: the
    # tables document a `tool_result.content`, an `advisor_tool_result.content`, and a `content`
    # of its own on system records, and the row named none of them. A nested row's container
    # must therefore resolve to one row, matched the way a reader matches it — by the container
    # name, wherever that row spells it from.
    names = {doc.path for doc in field_tables.documentation()}
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
    names = {doc.path for doc in field_tables.documentation()}
    assert "message.content.advisor_tool_result.content.type" in names
    assert "message.content.fallback.from.model" in names
    # And why the name runs the whole way down rather than naming the block and its field: a kind
    # repeats at two depths. A picture is a block of a message's own list and a part of a
    # block-form `tool_result`, so `image.source` alone would send a reader to two different rows.
    assert "message.content.image.source" in names
    assert "message.content.tool_result.content.image.source" in names


def test_every_cited_fixture_exists() -> None:
    # A citation to a fixture that was deleted or renamed is worse than no citation: it reads as
    # verified and is not.
    for doc in field_tables.documentation():
        for cite in doc.evidence:
            if cite.fixture:
                assert (REPO / cite.fixture).is_dir(), (
                    f"{doc.path} cites {cite.fixture}, which is not a fixture directory"
                )


@pytest.mark.parametrize("doc", field_tables.documentation(), ids=lambda doc: doc.path)
def test_every_citation_shows_the_field_in_the_fixture_it_names(
    doc: field_tables.Documentation,
) -> None:
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


@pytest.mark.parametrize(
    "block", [*blocks.BLOCK_MODELS, *blocks.RESULT_MODELS], ids=lambda b: b.BLOCK.value
)
def test_every_block_model_validates_a_recorded_block(block: type[blocks.Kinded]) -> None:
    # Blocks are not records, so the registry test above cannot reach them: they are validated
    # here, against every block of their kind in every fixture — both lists, a message's own and
    # a block-form `tool_result`'s. A kind a fixture cannot hold has to say so in its citation:
    # `image` is the one, because a redacted excerpt carrying one would carry the picture whole.
    found = [
        item
        for record in every_record()
        for item in blocks_and_parts(record)
        if item.get("type") == block.BLOCK
    ]
    if all(cite.scan and not cite.fixture for cite in block.EVIDENCE):
        assert not found, f"a fixture holds a `{block.BLOCK.value}` block, so cite it"
        return
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


def blocks_and_parts(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Both content lists a record holds: the message's blocks, and any block's own parts."""
    found = content_of(record)
    parts = [
        part
        for item in found
        if isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict)
    ]
    return [*found, *parts]


@pytest.mark.xdist_group("corpus")
def test_no_recorded_record_carries_a_field_the_models_do_not_declare() -> None:
    # What "the models are the schema" costs, stated as a test: every transcript the corpus holds,
    # read through the model its type resolves to, must carry no key the models leave undeclared.
    # The failure message is the list of fields still to write, which is how the declarations were
    # found in the first place. Where the walk stops — a tool's own report, an archived kind, an
    # object nobody has opened — is the boundary of what the models claim at all.
    unknown = UnknownFields(strict=False)
    for transcript in corpus_transcripts():
        # Through `read_lines`, which is where validation and the walk now live, so the corpus
        # is exactly the lines the extractor keeps and reads them exactly as it does.
        read_lines(transcript, transcript.stem, unknown)

    assert unknown.report() == ""


def test_the_content_blocks_a_message_can_hold_are_the_ones_it_lists() -> None:
    # The union each message model declares is what the generator reads to say which records
    # carry a block, so a block recorded under a message that does not list it would document
    # the wrong records. Checked against every block in every fixture.
    listed: dict[type[BaseModel], set[ContentBlock | ResultBlock]] = {
        model: {
            member.BLOCK
            for member in field_tables.members(message.model_fields["content"].annotation)
        }
        for model, message in (
            (conversation.UserRecord, messages.UserMessage),
            (conversation.AssistantRecord, messages.AssistantMessage),
        )
    }
    # A block-form `tool_result` holds its own list, dispatched by the same rule, so the parts
    # are checked against the union that block declares.
    parts = {
        member.BLOCK
        for member in field_tables.members(
            blocks.ToolResultBlock.model_fields["content"].annotation
        )
    }
    for record in every_record():
        model = shapes.model_for(record)
        if model not in listed:
            continue
        for item in content_of(record):
            kind = item["type"]
            assert kind in listed[model] or kind in blocks.UNCITED_BLOCKS, (
                f"a `{kind}` block in a {record['type']} record that lists none"
            )
            for part in item["content"] if isinstance(item.get("content"), list) else []:
                assert part["type"] in parts, f"a `{part['type']}` part no `tool_result` lists"


def test_every_recorded_block_parses_as_the_model_its_kind_names() -> None:
    # What the discriminator buys: a content list comes back as the block models themselves, so a
    # reader asks a `tool_use` for its `name` instead of reaching into a dict and hoping. Every
    # block of every recorded fixture, checked by class rather than by "it parsed" — the two
    # differ exactly when a union member is missing and pydantic picks a neighbour.
    # Two maps, not one: `ContentBlock.TEXT` and `ResultBlock.TEXT` are both the string `text`,
    # so a single dict keyed by kind would lose one of them — which is the collision the whole-
    # path naming in `field_tables` exists for.
    by_block = {model.BLOCK: model for model in blocks.BLOCK_MODELS}
    by_part = {model.BLOCK: model for model in blocks.RESULT_MODELS}
    seen: list[ContentBlock | ResultBlock] = []
    for record in every_record():
        parsed = shapes.model_for(record).model_validate(record)
        message = getattr(parsed, "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            assert type(block) is by_block[block.BLOCK], (
                f"a `{block.BLOCK}` block parsed as {type(block)}"
            )
            seen.append(block.BLOCK)
            parts = getattr(block, "content", None)
            for part in parts if isinstance(parts, list) else []:
                assert type(part) is by_part[part.BLOCK], (
                    f"a `{part.BLOCK}` part parsed as {type(part)}"
                )
                seen.append(part.BLOCK)

    # And the count, so a walk that silently found no lists cannot pass: what the models yielded
    # is what the raw JSON holds, kind for kind.
    raw = [item["type"] for record in every_record() for item in blocks_and_parts(record)]
    assert sorted(kind.value for kind in seen) == sorted(raw)


# Any `record["field"]` read, which is how a dict comes back into a reader.
DICT_READ = re.compile(r'\["[a-zA-Z_]+"\]')
# The one the design keeps: `agent_runs.py` opens the `agent-<id>.meta.json` sidecar, a file
# Claude Code writes beside a transcript and no record model describes.
SIDECAR_READ = re.compile(r'\bmeta\["[a-zA-Z_]+"\]')


def test_no_extractor_reads_a_record_as_a_dict() -> None:
    # The pin behind the whole change: the models are the parser's types, so a reader that wants
    # a field asks the record for it. A bracket read is how the dict gets back in — one line at a
    # time, past the models, past the type checker, and without evidence that the field is there.
    # Whole package rather than one module, because the readers moved between modules while the
    # change was underway and a pin on one file follows them nowhere.
    package = Path(extract.__file__).parent
    reads = {
        str(path.relative_to(package)): DICT_READ.findall(SIDECAR_READ.sub("", path.read_text()))
        for path in sorted(package.rglob("*.py"))
    }
    assert {module: found for module, found in reads.items() if found} == {}
