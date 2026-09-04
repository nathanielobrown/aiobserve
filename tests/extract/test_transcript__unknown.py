"""What happens to a field no model declares: a crash where a person is looking, a tally elsewhere.

The record models claim to describe every field Claude Code writes, and `UnknownFields` is what
holds them to it. The corpus leaf in `test_records.py` proves the claim over the fixtures; these
leaves prove the walk itself — where it descends, where it stops, and what it says when it finds
something.

The driving fixture is invented by necessity: a field no model declares is, by construction, a
field no recorded session in this repository carries. `tests/fixtures/invented/README.md` labels
it, and its invented values are the tripwire string, so every leaf here can assert that the value
never reaches a message or a report.
"""

from pathlib import Path

import pytest

from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.records import shapes
from hyphae.extract.records.unknown import UnknownFields
from hyphae.extract.transcript import read_lines
from tests.conftest import FIXTURES

# The invented transcript, and the line each of its records sits on: a `mode` record opens it, as
# every file in `invented/` does, then one record per shape the walk has to get right.
UNKNOWN_FIELD = FIXTURES / "invented" / "invented-unknown-field.jsonl"
ENVELOPE_LINE = 2
NESTED_LINE = 3
OPAQUE_LINE = 4
# The value every invented field in that fixture carries. A crash message or a tally line that
# names it has leaked transcript content, which is the one failure mode worse than a wrong count.
TRIPWIRE = "SUPER-SECRET-PAYLOAD-9f2a"
# A recorded fixture whose every field the models declare — the negative control for `report()`.
CLEAN = FIXTURES / "model_only"
ZOO = FIXTURES / "registry_zoo"


def walk(unknown: UnknownFields, path: Path, session_id: str, only: int | None = None) -> None:
    """Feed one transcript through the models into `unknown`, or just one of its lines."""
    for line in read_lines(path, session_id):
        if only is None or line.line_no == only:
            model = shapes.model_for(line.record)
            unknown.note(model.model_validate(line.record), session_id, line.line_no)


def test_strict_mode_stops_on_a_field_no_model_declares() -> None:
    # Strict is what the suite runs under: an undeclared field means the models no longer describe
    # what Claude Code writes, and the run stops at the record that proves it. The message is the
    # whole address a person needs — which model, which field, which session, which line — and
    # nothing else, because everything else in a transcript is private.
    unknown = UnknownFields(strict=True)

    with pytest.raises(TranscriptSchemaError) as raised:
        walk(unknown, UNKNOWN_FIELD, "unknown-field-session", only=ENVELOPE_LINE)

    message = str(raised.value)
    assert "assistant.shimmerBudget" in message
    assert "unknown-field-session" in message
    assert str(ENVELOPE_LINE) in message
    assert TRIPWIRE not in message


def test_the_walk_descends_into_the_models_nested_inside_a_record() -> None:
    # The leaf that makes the claim worth making. `usage` is where Claude Code adds fields most
    # often, and it is three models down from the record: a walk that only checked the envelope
    # would pass every other leaf here while proving nothing about the place fields actually
    # arrive. The reported path is the address, spelled the way the record spells it.
    unknown = UnknownFields(strict=True)

    with pytest.raises(TranscriptSchemaError) as raised:
        walk(unknown, UNKNOWN_FIELD, "unknown-field-session", only=NESTED_LINE)

    assert "assistant.message.usage.shimmer_tokens" in str(raised.value)
    assert TRIPWIRE not in str(raised.value)


@pytest.mark.parametrize("strict", [True, False])
def test_the_walk_stops_at_a_tools_own_report(strict: bool) -> None:
    # The boundary the design buys with `OPAQUE`. A `toolUseResult` is written by whichever tool
    # ran, so its keys are an open set that Claude Code does not own and the models cannot claim.
    # The walk stops there in both modes: no crash under strict, no tally under lax. Without this,
    # the corpus leaf would be a demand that we describe every tool anyone ever writes.
    unknown = UnknownFields(strict=strict)

    walk(unknown, UNKNOWN_FIELD, "unknown-field-session", only=OPAQUE_LINE)

    assert unknown.report() == ""


def test_an_archived_kind_carries_whatever_it_likes() -> None:
    # The same stop, for the other opaque model. `ArchivedRecord` declares the envelope `raw_record`
    # writes and nothing else, so the 24k `attachment` records in the store — and the six thin
    # `system` subtypes — carry keys no model declares by construction. Strict mode has to walk
    # right past them, or the archive would stop every run.
    unknown = UnknownFields(strict=True)

    for transcript in sorted(ZOO.rglob("*.jsonl")):
        walk(unknown, transcript, transcript.stem)

    assert unknown.report() == ""


def test_lax_mode_tallies_one_entry_per_path_with_its_first_sighting() -> None:
    # What an extract does instead of crashing: a field Claude Code added yesterday is news, not a
    # reason to stop archiving. One entry per path however many records carried it, holding where
    # it was first seen and how many sessions have it — the two numbers that say whether it is a
    # new field rolling out or one session doing something odd.
    unknown = UnknownFields(strict=False)

    walk(unknown, UNKNOWN_FIELD, "first-session")
    walk(unknown, UNKNOWN_FIELD, "second-session")

    # The report is what an extract prints, so pin the whole of it: one line per path, in path
    # order. Two paths, not three — the invented key inside `toolUseResult` is behind the opaque
    # stop. Every line says `first-session`, so the second read overwrote no first sighting; a
    # field's arrival date would otherwise drift to whenever the extractor last ran.
    assert unknown.report() == (
        f"assistant.message.usage.shimmer_tokens: first in session first-session "
        f"line {NESTED_LINE}, 2 session(s)\n"
        f"assistant.shimmerBudget: first in session first-session "
        f"line {ENVELOPE_LINE}, 2 session(s)"
    )
    assert TRIPWIRE not in unknown.report()


def test_a_clean_transcript_reports_nothing() -> None:
    # The negative control, and what "the models are the schema" looks like when it holds: a
    # recorded session through the models leaves an empty report. Without this leaf, a walk that
    # silently found nothing anywhere would pass every assertion above.
    unknown = UnknownFields(strict=False)

    for transcript in sorted(CLEAN.rglob("*.jsonl")):
        walk(unknown, transcript, transcript.stem)

    assert unknown.report() == ""
