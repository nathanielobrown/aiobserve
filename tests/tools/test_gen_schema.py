"""What the generated schema tables have to hold: the models' claims, and the document's own.

Two properties, and they pull in opposite directions. Every row comes from `records.py` — no
meaning and no citation is written here — and every field the hand-written document listed is
still in a table. `schema_inventory.toml` is what makes the second one checkable: the field
inventory of `docs/schema.md` frozen before the tables became generated, so a field lost in the
move fails here rather than going quietly missing from the document.
"""

import re
import tomllib
from pathlib import Path
from typing import Annotated, Any, ClassVar

import pytest
from pydantic import Field

from aiobserve.extract.record_types import RecordType
from aiobserve.extract.records import schema, shapes
from tests.tools.conftest import cells
from tools import gen_schema

SECTIONS = list(gen_schema.Section)
# The inventory sits beside this test because it is only ever read here.
INVENTORY = Path(__file__).parent / "schema_inventory.toml"


def inventory() -> list[dict[str, Any]]:
    """Every row `docs/schema.md` printed before the cut, as the frozen file recorded it."""
    return tomllib.loads(INVENTORY.read_text())["row"]


def printed() -> dict[str, tuple[str, ...]]:
    """Every generated row, by the field name in its first cell, over all four tables."""
    rows = {}
    for section in SECTIONS:
        for row in cells(gen_schema.generate(section)):
            rows[row[0].strip("`")] = row
    return rows


def covering(field: str, rows: dict[str, tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The generated rows for one inventoried field, matched by name or by full path.

    The document wrote a nested field either way — `stop_reason` in one row and
    `message.usage` in another — so a suffix match is what "this field is still documented"
    means.
    """
    return [row for path, row in rows.items() if path == field or path.endswith(f".{field}")]


@pytest.mark.parametrize(
    "row", inventory(), ids=lambda row: f"{row['section']}-{'-'.join(row['fields'])}"
)
def test_every_field_the_document_listed_is_still_in_a_table(row: dict[str, Any]) -> None:
    # The headline, and the reason the inventory was frozen before phase B deletes the
    # hand-written tables: the models are the source now, so a field nobody carried across
    # would leave the document quietly smaller than it was.
    rows = printed()
    for field in row["fields"]:
        assert covering(field, rows), f"`{field}` was in the {row['section']} table and is gone"


@pytest.mark.parametrize(
    "row", inventory(), ids=lambda row: f"{row['section']}-{'-'.join(row['fields'])}"
)
def test_every_row_still_names_the_records_the_document_named(row: dict[str, Any]) -> None:
    # A field is only half the claim: the Records cell says which shapes carry it, and a model
    # that inherits from the wrong mixin would still print the field under the wrong records.
    # Only the record names the document spelled in code spans are checked — cells like "most
    # records" were prose about the set, which is exactly what the models replaced.
    rows = printed()
    named = re.findall(r"`([^`]+)`", row["records"])
    said = " ".join(found[1] for field in row["fields"] for found in covering(field, rows))
    for record in named:
        assert f"`{record}`" in said, f"`{record}` carried {row['fields']} and no longer does"


class Undescribed(shapes.Record):
    """A record with a field nobody described. Registered nowhere: this test is its only caller."""

    RECORD_TYPE: ClassVar[RecordType] = RecordType.USER

    mystery: str | None = None


class Uncited(shapes.Record):
    """A record whose field says what it means and names no recording that shows it."""

    RECORD_TYPE: ClassVar[RecordType] = RecordType.USER

    hearsay: Annotated[str | None, Field(description="Something someone remembers")] = None


def documented(model: type[shapes.Record], path: str) -> schema.Documentation:
    return next(doc for doc in schema.documentation((model,)) if doc.path == path)


def test_a_field_with_no_meaning_stops_the_generator() -> None:
    # The gate the old document stated in prose. A blank Meaning cell reads as though the field
    # was checked and found to mean nothing, so the run stops and names the field instead.
    with pytest.raises(ValueError, match="mystery"):
        gen_schema.cells(documented(Undescribed, "mystery"))


def test_a_field_citing_no_recording_stops_the_generator() -> None:
    # And the rule this project runs on: a claim about Claude Code's format that no recording
    # supports is a hypothesis. It may not print as a documented field.
    with pytest.raises(ValueError, match="hearsay"):
        gen_schema.cells(documented(Uncited, "hearsay"))


def test_every_documented_field_is_laid_out_in_exactly_one_table() -> None:
    # `SECTIONS` is the document's editorial choice and the models don't know it, so it is
    # closed on both sides: generating any one table checks the whole layout. A new model field
    # lands in a table or stops the run — it cannot be documented into a table nobody prints.
    assert set(gen_schema.placed()) == set(gen_schema.documented())


def test_a_field_laid_out_twice_stops_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    # The other half of "exactly one": a field printed in two tables is two rows a reader has
    # to reconcile, and only one of them is the one they found.
    monkeypatch.setitem(
        gen_schema.SECTIONS,
        gen_schema.Section.EVENTS,
        (*gen_schema.SECTIONS[gen_schema.Section.EVENTS], "uuid"),
    )
    with pytest.raises(ValueError, match="uuid"):
        gen_schema.generate(gen_schema.Section.IDENTITY)


def test_a_table_naming_a_field_no_model_documents_stops_the_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # And the direction phase B will exercise: a field deleted from the models leaves the
    # layout naming something that no longer exists, rather than printing an empty row.
    monkeypatch.setitem(
        gen_schema.SECTIONS,
        gen_schema.Section.API,
        (*gen_schema.SECTIONS[gen_schema.Section.API], "onceUponATime"),
    )
    with pytest.raises(ValueError, match="onceUponATime"):
        gen_schema.generate(gen_schema.Section.API)


@pytest.mark.parametrize("section", SECTIONS, ids=lambda section: section.value)
def test_the_table_is_rows_all_the_way_down(section: gen_schema.Section) -> None:
    # Meanings and citations are prose of unbounded length, and a wrapped cell is a broken
    # table: every line of a generated table is a row.
    assert all(line.startswith("| ") for line in gen_schema.generate(section).splitlines())


@pytest.mark.parametrize("section", SECTIONS, ids=lambda section: section.value)
def test_the_table_ends_without_its_own_newline(section: gen_schema.Section) -> None:
    # `main()` prints, and the cog splice owns the framing newline.
    assert not gen_schema.generate(section).endswith("\n")


def test_main_prints_the_table_it_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # What the cog block runs: one argument, one table, nothing else on stdout.
    monkeypatch.setattr("sys.argv", ["gen_schema", "events"])
    gen_schema.main()
    assert capsys.readouterr().out == gen_schema.generate(gen_schema.Section.EVENTS) + "\n"


def test_main_refuses_to_guess_which_table() -> None:
    # A cog block that forgot its argument gets an error, not the first table.
    with pytest.raises(SystemExit, match="identity"):
        gen_schema.main()
