"""Every record in the canonical store, read through the model its type resolves to.

The fixtures are redacted excerpts of a few sessions; this is the whole archive. It is what
answers the question the fixtures cannot: does a Claude Code version nobody trimmed a fixture
from write a field the models do not declare? Each one it finds becomes a declaration with a
citation before the readers move onto the models.

Off by default. `HYPHAE_LIVE_STORE` names the store, and the store is private session data, so
`mise run check` never runs this and CI cannot. Run it by hand:

    HYPHAE_LIVE_STORE=data/traces.duckdb uv run pytest tests/extract/test_records__census.py -s

Nothing here asserts on a record. A `raw_records` row is transcript content and a failing
assertion prints its operands, so every assertion below is on a count or on a field path.
"""

import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from hyphae.extract.records.blocks import BLOCK_MODELS, RESULT_MODELS, Kinded
from hyphae.extract.records.evidence import Described
from hyphae.extract.records.registry import ArchiveRecordType
from hyphae.extract.records.shapes import Record, model_for
from hyphae.extract.records.unknown import UnknownFields

# Names a real trace store for the sweep below. Off by default: the store holds private
# session data (`docs/store.md`).
LIVE_STORE = "HYPHAE_LIVE_STORE"
# How many rows come back at a time. The store holds hundreds of thousands and each `raw` is a
# whole record, so the sweep streams rather than materializing the archive in memory.
BATCH = 5_000


@dataclass
class Census:
    """What one sweep of the archive found, in counts and field paths only."""

    records: int = 0
    sessions: int = 0
    # One entry per record type that resolved to no model, or whose model rejected it. The value
    # is the field path pydantic named, never the value it held.
    failures: Counter[tuple[str, str]] = field(default_factory=Counter)
    # Every record type, by the model it resolved to: the sweep's own inventory of what it read.
    models: Counter[tuple[str, str]] = field(default_factory=Counter)
    # `toolUseResult` by the JSON shape it arrived in, which is what obliges the union.
    result_forms: Counter[str] = field(default_factory=Counter)
    # Every content-block kind, by the record type whose message held it.
    blocks: Counter[tuple[str, str]] = field(default_factory=Counter)
    # The undeclared fields, tallied rather than raised: the declaration list this sweep exists
    # to produce.
    unknown: UnknownFields = field(default_factory=lambda: UnknownFields(strict=False))
    # The other side of that walk: one entry per key sitting where it stops. Keys only —
    # a key is a field name, and this file prints no value.
    unclaimed: Counter[str] = field(default_factory=Counter)


def unclaimed_keys(model: Described, path: str, found: Counter[str]) -> None:
    """Every key one level under a place the declaration walk stops, by the path it sits at.

    `UnknownFields` claims nothing about an opaque model's own keys or the interior of a
    dict-typed leaf, so nothing else in the suite would ever name them. A reader who wants a
    field there declares it on the model, which is how `persistedOutputPath` and `runId` came
    off this side and onto `ToolUseResult`; this is the menu that choice is made from. One
    level, because the level below is a further claim nobody has made either.
    """
    if model.OPAQUE:
        for name in model.model_extra or {}:
            found[f"{path}.{name}"] += 1
        return
    for name, info in type(model).model_fields.items():
        value = getattr(model, name)
        step = f"{path}.{info.alias or name}"
        if isinstance(value, Described):
            unclaimed_keys(value, step, found)
        elif isinstance(value, dict):
            for key in value:
                found[f"{step}.{key}"] += 1
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Kinded):
                    unclaimed_keys(item, f"{step}.{item.BLOCK.value}", found)


def live_store_copy(tmp_path: Path) -> Path:
    """A private copy of the real archive `HYPHAE_LIVE_STORE` names.

    Never the store itself: it is the archive (`docs/store.md`), and a reader holding it open
    blocks the extract that writes it. The write-ahead log comes along, or the copy would be
    the archive as of its last checkpoint.
    """
    archive = Path(os.environ[LIVE_STORE])
    copy = tmp_path / archive.name
    shutil.copy(archive, copy)
    wal = archive.with_name(f"{archive.name}.wal")
    if wal.exists():
        shutil.copy(wal, copy.with_name(f"{copy.name}.wal"))
    return copy


def swept(census: Census, session_id: str, line_no: int, raw: str) -> None:
    """One row through its model, with everything the sweep counts about it."""
    record: dict[str, Any] = json.loads(raw)
    kind = str(record.get("type"))
    model = model_for(record)
    census.models[(kind, model.__name__)] += 1
    try:
        parsed: Record = model.model_validate(record)
    except ValidationError as error:
        for fault in error.errors(include_input=False, include_url=False):
            census.failures[(kind, ".".join(str(part) for part in fault["loc"]))] += 1
        return
    census.unknown.note(parsed, session_id, line_no)
    unclaimed_keys(parsed, kind, census.unclaimed)
    if (result := record.get("toolUseResult")) is not None:
        census.result_forms[type(result).__name__] += 1
    content = getattr(getattr(parsed, "message", None), "content", None)
    for block in content if isinstance(content, list) else []:
        census.blocks[(kind, block.BLOCK.value)] += 1
        for part in block.content if isinstance(getattr(block, "content", None), list) else []:
            census.blocks[(f"{kind} / {block.BLOCK.value}", part.BLOCK.value)] += 1


@pytest.fixture(scope="module")
def census(tmp_path_factory: pytest.TempPathFactory) -> Census:
    """Every `raw_records` row of the store, read once for every leaf below."""
    if LIVE_STORE not in os.environ:
        pytest.skip(f"set {LIVE_STORE} to a real trace store to run")
    found = Census()
    connection = duckdb.connect(str(live_store_copy(tmp_path_factory.mktemp("census"))))
    try:
        counted = connection.execute(
            "SELECT count(DISTINCT session_id) FROM raw_records"
        ).fetchone()
        assert counted is not None, "the store answered nothing"
        found.sessions = counted[0]
        rows = connection.execute("SELECT session_id, line_no, raw FROM raw_records")
        while batch := rows.fetchmany(BATCH):
            for session_id, line_no, raw in batch:
                swept(found, session_id, line_no, raw)
                found.records += 1
    finally:
        connection.close()
    # What the sweep read, printed rather than asserted. The opaque side is the point: a tool's
    # own report and an archived kind are shapes nobody here claims, and this is where a reader
    # asks how much of the archive that covers.
    print(f"\n{found.records:,} records in {found.sessions:,} sessions")  # noqa: T201 — the section a person runs this for
    print("\n".join(f"  {kind} -> {name}: {n:,}" for (kind, name), n in found.models.most_common()))  # noqa: T201 — the section a person runs this for
    print("\n".join(f"  {kind}.{part}: {n:,}" for (kind, part), n in found.blocks.most_common()))  # noqa: T201 — the section a person runs this for
    print(f"  toolUseResult forms: {dict(found.result_forms)}")  # noqa: T201 — the section a person runs this for
    print("\n".join(f"  unclaimed {path}: {n:,}" for path, n in found.unclaimed.most_common()))  # noqa: T201 — the section a person runs this for
    return found


@pytest.mark.slow  # Reads every recorded session — minutes — and they are private.
def test_every_recorded_record_validates_against_its_model(census: Census) -> None:
    """The whole archive through the models, with nothing quoted back.

    The fixtures hold a few hundred records of a handful of Claude Code versions. This is the
    claim over all of them: a type that resolves to no model raises before the assertion, and a
    field whose declared type is wrong lands in `failures` as a path.
    """
    assert census.records > 0, f"{LIVE_STORE} names a store with no records in it"
    assert census.failures == Counter()
    # And the sweep really resolved records rather than skipping them: every row was read
    # through a model, so the inventory accounts for all of them.
    assert sum(census.models.values()) == census.records


@pytest.mark.slow  # The same sweep; see above.
def test_no_recorded_record_carries_a_field_the_models_do_not_declare(census: Census) -> None:
    """The census's own question. The failure message is the declaration list.

    A field here is a field Claude Code writes that `docs/schema.md` does not print. Each one
    is declared with a fixture citation where a session can be trimmed to show it, and with a
    `Cited(scan=...)` where it cannot.
    """
    assert census.unknown.report() == ""


@pytest.mark.slow  # The same sweep; see above.
def test_both_forms_of_a_tools_report_are_in_the_archive(census: Census) -> None:
    """`toolUseResult` is an object, a string or a list, and the union carries all three.

    A form that never appears would mean the union declares a shape on no evidence, which is
    what the counts here rule out.
    """
    assert set(census.result_forms) == {"dict", "str", "list"}
    assert all(count > 0 for count in census.result_forms.values())


@pytest.mark.slow  # The same sweep; see above.
def test_every_recorded_block_kind_is_one_the_message_that_held_it_declares(
    census: Census,
) -> None:
    """Dispatch is total over the archive, not just over the fixtures.

    A kind outside the union its message declares is a discriminator error, so it would already
    have failed the validation leaf. This is the bounded absence beside it: the sweep read every
    row, and these are the kinds it found.
    """
    assert census.blocks, "no record in the store carried a content list"
    declared = {model.BLOCK.value for model in (*BLOCK_MODELS, *RESULT_MODELS)}
    assert {kind for _, kind in census.blocks} <= declared


@pytest.mark.slow  # The same sweep; see above.
def test_the_keys_no_model_claims_are_counted_rather_than_unseen(census: Census) -> None:
    """The opaque side, which the design promises the census prints when asked.

    An archived kind, a tool's own report and a dict-typed leaf are the three places the
    declaration walk stops. Stopping is the design; being unable to say what is there is not,
    because the next reader who wants one of those fields chooses it from this section.
    """
    assert census.unclaimed, "the sweep stopped at no opaque model and no dict leaf"
    # An archived kind: keys of a record type nothing reads past its envelope...
    assert {path.split(".")[0] for path in census.unclaimed} & {
        kind.value for kind in ArchiveRecordType
    }
    # ...a tool's own report, which is an open set nobody here claims...
    assert any(".toolUseResult." in path for path in census.unclaimed)
    # ...and a dict-typed leaf, of which a tool call's `input` is the one every session holds.
    assert any(".tool_use.input." in path for path in census.unclaimed)
