"""The enrichment tables: what they hold, what makes a row stale, and what sweeps it away.

The base rows come from the fixture-built pipeline output, so the natural keys under test are
the ones the pipeline really writes. Assertions are SQL against a real DuckDB file.
"""

import dataclasses
import json
from pathlib import Path

import duckdb
import pytest

from aiobserve.enrich.prompts import Level, TurnItem
from aiobserve.enrich.store import EnrichmentStore, Stamp
from tests.conftest import MODEL_ONLY, build_store, fixture_transcripts
from tests.enrich.conftest import (
    COMPACTION,
    DUP_UUID,
    FORK_BYREF,
    RESUME,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    enrichment,
    session_item,
    stamp,
)


def spine_turns(store: EnrichmentStore) -> list[TurnItem]:
    return [item for item in store.turn_items() if item.session_id == SPINE]


def test_turn_items_are_the_live_main_turns(fixture_db: Path) -> None:
    """The enrichable turns are the session's own main turns, each with its calls attached."""
    with EnrichmentStore(fixture_db) as store:
        items = spine_turns(store)
    # If `spine/` recorded four main turns, two of them slash commands...
    assert [item.turn_id[:8] for item in items] == ["5b848af7", "30aad8e5", "818588ad", "8cdceb31"]
    assert [item.command_name for item in items] == ["/model", "/night-run", None, None]
    # ...then what the CLI printed for each comes with it, read out of the archive: the
    # `/model` turn's stdout record names it as its `parentUuid`, and nothing archived an
    # answer for the other three, which is None rather than an empty string.
    assert [item.command_result for item in items] == ["[redacted]", None, None, None]
    # ...then each turn carries the api calls it drove, and each call its tool calls —
    # turn 818588ad drove two calls, one asking for an Agent and one for a Read.
    third = items[2]
    assert [len(call.tool_calls) for call in third.api_calls] == [1, 1]
    assert [call.tool_calls[0].name for call in third.api_calls] == ["Agent", "Read"]
    # ...and the item names itself with its own primary key, which is what a request and a
    # failure record carry.
    assert third.level is Level.turn
    assert third.key == f"turn|{SPINE}|main|{third.turn_id}"


# A record the archive filter catches whose body no carrier holds. Both are invented, and
# have to be: a shape we have seen is a shape the reader handles, so the only way to exercise
# the guard is to write down one we have not. `spine/`'s `/model` turn is the parent, so each
# row reaches the classification rather than being dropped for hanging off nothing.
UNREADABLE_CARRIERS = {
    # The tag is in the record but in neither field a carrier has ever used: the `coalesce`
    # yields NULL, which `string_agg` would have skipped without a word.
    "no carrier field": {
        "parentUuid": "5b848af7-f86e-4950-b474-cd98125fad24",
        "type": "user",
        "toolUseResult": "<local-command-stdout>printed</local-command-stdout>",
    },
    # A carrier that holds no tag: the extract yields '', which is the empty-body state — an
    # unread record would render as "the command printed nothing".
    "carrier without the tag": {
        "parentUuid": "5b848af7-f86e-4950-b474-cd98125fad24",
        "type": "system",
        "content": "printed, in a shape with no tag around it",
        "toolUseResult": "<local-command-stdout>printed</local-command-stdout>",
    },
}


def plant_record(store: EnrichmentStore, session_id: str, line_no: int, record: object) -> None:
    """Add one raw transcript record to a session's archive, at a line of its own."""
    store.connection.execute(
        "INSERT INTO raw_records (session_id, source, line_no, uuid, timestamp, type, raw)"
        " VALUES (?, 'main', ?, ?, now(), 'user', ?)",
        [session_id, line_no, f"planted-{line_no}", json.dumps(record)],
    )


@pytest.mark.parametrize("shape", list(UNREADABLE_CARRIERS))
def test_a_command_output_the_archive_cannot_read_crashes(mutable_db: Path, shape: str) -> None:
    """A record archiving a command's output in an unknown shape stops the pass, naming it.

    Claude Code owns these shapes and changes them without notice. Neither silent state is
    tolerable: a dropped record loses the one fact the prompt gained, and a body that reads as
    empty tells the model the command printed nothing, which is the absence the fix removes.
    """
    with EnrichmentStore(mutable_db) as store:
        plant_record(store, SPINE, 900, UNREADABLE_CARRIERS[shape])
        # The error names where to look: the session, and the line of the transcript.
        with pytest.raises(ValueError, match=f"{SPINE}.*line 900"):
            store.turn_items()


def test_a_multi_line_command_output_survives_whole(mutable_db: Path) -> None:
    """An output printed over several lines reaches the item as those lines.

    The body is planted into a recorded record and invented, and it has to be: redaction
    flattens every string to `[redacted]`, so no fixture body can hold a newline. A reader
    that stopped at the first line would extract nothing at all and report an empty body.
    """
    with EnrichmentStore(mutable_db) as store:
        store.connection.execute(
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND line_no = 8",
            [
                json.dumps(
                    {
                        "parentUuid": "5b848af7-f86e-4950-b474-cd98125fad24",
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "<local-command-stdout>first line\nsecond line"
                            "</local-command-stdout>",
                        },
                    }
                ),
                SPINE,
            ],
        )
        item = next(item for item in store.turn_items() if item.turn_id.startswith("5b848af7"))
    assert item.command_result == "first line\nsecond line"


def test_a_project_filter_narrows_the_items(fixture_db: Path) -> None:
    """`--project` restricts enrichment to the sessions recorded for one repository."""
    with EnrichmentStore(fixture_db) as store:
        assert store.turn_items(project="/no/such/repo") == []
        assert store.turn_items() != []


def test_a_run_naming_no_parent_agent_hangs_off_the_transcript_that_spawned_it(
    mutable_db: Path,
) -> None:
    """A run the records name no parent agent for still hangs off the run that spawned it.

    112 of 2,459 recorded runs are in this shape. Reading `parent_agent_id` alone calls every
    one of them a root and sends it before the parent whose prompt embeds its description.
    """
    with EnrichmentStore(mutable_db) as store:
        parents = store.item_parents()
        # If `spine/`'s leaf run — which names a parent agent *and* was spawned by a call
        # inside that agent's transcript — loses the named parent (planted, and labeled
        # invented: every fixture run naming no parent agent was spawned from the main
        # transcript or from nothing at all, so no fixture carries the recorded shape)...
        store.connection.execute(
            "UPDATE agent_runs SET parent_agent_id = NULL WHERE id = ?", [SPINE_LEAF]
        )
        # ...then nothing about the forest moves: the transcript holding the spawning call
        # names the parent the deleted column named...
        assert store.item_parents() == parents
    # ...which is the run that spawned it, not the session and not a turn.
    assert parents[f"{Level.agent_run}|{SPINE}|{SPINE_LEAF}"] == (
        f"{Level.agent_run}|{SPINE}|{SPINE_RUN}"
    )


def test_the_tables_survive_a_re_export(mutable_db: Path) -> None:
    """A re-extraction of the same session leaves its enrichment rows exactly as they were."""
    # If a turn of `spine/` is enriched...
    with EnrichmentStore(mutable_db) as store:
        store.upsert(spine_turns(store)[0], enrichment(), stamp())
        before = store.connection.execute("SELECT * FROM turn_enrichments").fetchall()
    # ...and the pipeline then replaces every row that session owns...
    build_store(mutable_db, fixture_transcripts("spine"))
    # ...then the enrichment row is untouched, down to its timestamp: the per-session replace
    # never reaches these tables.
    with EnrichmentStore(mutable_db) as store:
        assert store.connection.execute("SELECT * FROM turn_enrichments").fetchall() == before
        assert len(before) == 1


def test_a_second_upsert_replaces_the_row(mutable_db: Path) -> None:
    """Enriching the same item twice leaves one row, holding the second answer."""
    with EnrichmentStore(mutable_db) as store:
        item = spine_turns(store)[0]
        store.upsert(item, enrichment("The first answer."), stamp("hash-1"))
        store.upsert(item, enrichment("The second answer."), stamp("hash-2"))
        assert store.connection.execute(
            "SELECT description, input_hash FROM turn_enrichments"
        ).fetchall() == [("The second answer.", "hash-2")]


def test_enriched_turns_left_joins(mutable_db: Path) -> None:
    """An un-enriched turn still appears in the view, with empty enrichment columns."""
    # If two of `spine/`'s four main turns are enriched...
    with EnrichmentStore(mutable_db) as store:
        for item in spine_turns(store)[:2]:
            store.upsert(item, enrichment(), stamp())
        # ...then the view returns all four, and says plainly which two carry no description.
        rows = store.connection.execute(
            "SELECT description FROM enriched_turns WHERE session_id = ? AND source = 'main'"
            ' ORDER BY "index"',
            [SPINE],
        ).fetchall()
    assert rows == [("Read two files and ran the suite.",)] * 2 + [(None,)] * 2


@pytest.mark.parametrize(
    "mutation",
    [
        # Each of the four fields of the staleness key, changed one at a time on a stored
        # row: the re-render that produced a different prompt...
        {"input_hash": "a-different-hash"},
        # ...an instruction or output-schema change...
        {"prompt_version": 99},
        # ...a taxonomy revision...
        {"taxonomy_version": 99},
        # ...and a `--model` switch.
        {"model": "claude-sonnet-4-5"},
    ],
)
def test_staleness_returns_the_rows_whose_key_moved(
    mutable_db: Path, mutation: dict[str, object]
) -> None:
    """A row is stale when any of the four staleness fields differs from today's value."""
    with EnrichmentStore(mutable_db) as store:
        items = spine_turns(store)
        planned = {item.key: stamp() for item in items}
        # If every turn is enriched under the same stamp, nothing is stale...
        for item in items:
            store.upsert(item, enrichment(), stamp())
        assert store.stale_keys(Level.turn, planned) == []
        # ...and if one stored row's stamp is moved off today's value...
        target = items[1]
        column, value = next(iter(mutation.items()))
        store.connection.execute(
            f"UPDATE turn_enrichments SET {column} = ? WHERE turn_id = ?",
            [value, target.turn_id],
        )
        # ...then that row, and only that row, comes back stale.
        assert store.stale_keys(Level.turn, planned) == [target.key]


def test_an_item_with_no_row_is_stale(mutable_db: Path) -> None:
    """A turn nothing has enriched yet is stale, which is how a first pass finds work."""
    with EnrichmentStore(mutable_db) as store:
        planned = {item.key: stamp() for item in spine_turns(store)}
        assert store.stale_keys(Level.turn, planned) == list(planned)


def test_a_zombie_enrichment_is_swept(mutable_db: Path) -> None:
    """An enrichment whose turn no longer exists is deleted, not left to haunt the views."""
    # If every main turn of `spine/` is enriched...
    with EnrichmentStore(mutable_db) as store:
        items = spine_turns(store)
        for item in items:
            store.upsert(item, enrichment(), stamp())
        # ...and one of those turns then vanishes — an extractor bump redrawing turn
        # boundaries is the real case...
        gone = items[1]
        store.connection.execute("DELETE FROM turns WHERE id = ?", [gone.turn_id])
        # ...then the sweep reports and removes its enrichment, and leaves the rest alone.
        assert store.sweep_zombies() == 1
        assert store.connection.execute(
            "SELECT turn_id FROM turn_enrichments ORDER BY turn_id"
        ).fetchall() == sorted((item.turn_id,) for item in items if item is not gone)


def test_a_session_with_no_turn_and_no_run_is_never_enriched(fixture_db: Path) -> None:
    """A session holding nothing to describe is skipped, not enriched and not failed.

    102 of 575 recorded sessions are in this state — compactions and duplicate-uuid records
    with no work of their own — which leaves 473 holding work, and 428 after the api-call gate.
    """
    with EnrichmentStore(fixture_db) as store:
        described = {item.session_id for item in store.session_items()}
        # If a session in the store recorded no main turn and no agent run...
        empty = {
            session_id
            for (session_id,) in store.connection.execute(
                "SELECT session_id FROM session_rollups WHERE turns = 0 AND agent_runs = 0"
            ).fetchall()
        }
    # ...then it is not an item, so nothing ever sends it or writes a row for it, while every
    # other session of the fixture corpus is. `resume_pair/`'s resume is the third: its api
    # calls all sit under a turn its ancestor ran, so it opened none of its own.
    assert empty == {COMPACTION, DUP_UUID, RESUME}
    assert described & empty == set()
    assert len(described) == 8


def test_an_api_call_carries_the_stop_reason_as_recorded(fixture_db: Path) -> None:
    """Why generation stopped travels from the store to the render, nulls kept as nulls.

    A null is a recorded state, not a missing row — 26 of the 69 stop reasons in the fixtures
    are null — so the render can say "not recorded" rather than say nothing.
    """
    with EnrichmentStore(fixture_db) as store:
        # If a turn's three recorded calls stopped `end_turn`, `tool_use` and nothing...
        item = next(item for item in store.turn_items() if item.turn_id.startswith("9ae45aaa"))
    # ...then the items carry all three values in the order they were recorded, so no render
    # of the three states rests on an invented row.
    assert [call.stop_reason for call in item.api_calls] == ["end_turn", "tool_use", None]


def test_a_session_whose_turns_drove_no_api_call_is_never_enriched(fixture_db: Path) -> None:
    """A session that only set an option has no model response to describe, so nothing sends it.

    45 of the 473 sessions with work in them are in this state — `/model` and `/effort` turns
    that the CLI answered by itself — and every description written for one was invented.
    """
    with EnrichmentStore(fixture_db) as store:
        described = {item.session_id for item in store.session_items()}
        # If the recorded `/model` session drove no api call under any of its three turns —
        # `/model`, `/clear` and `/reload-skills`, all answered by the CLI itself...
        assert store.connection.execute(
            "SELECT turns, agent_runs, api_calls FROM session_rollups WHERE session_id = ?",
            [MODEL_ONLY],
        ).fetchone() == (3, 0, 0)
        # ...then it is not an item, while a session whose only work is a subagent's — no turn
        # of its own, and its api calls all under the run — still is: the gate counts calls
        # across every source, not just the main transcript.
        assert MODEL_ONLY not in described
        assert FORK_BYREF in described
        # ...its `/model` turn is still an item of its own, since turns are deliberately not
        # gated and the `configure` census reads `turns.command_name` off exactly these...
        assert MODEL_ONLY in {item.session_id for item in store.turn_items()}
        # ...and the sessions view still carries it, with nothing described — which is what
        # makes the viewer render no enrichment block, as a never-described session does.
        assert store.connection.execute(
            "SELECT description FROM enriched_sessions WHERE session_id = ?", [MODEL_ONLY]
        ).fetchall() == [(None,)]


def test_a_row_already_written_for_a_gated_session_is_swept(mutable_db: Path) -> None:
    """An enrichment of a session nothing will describe again is deleted, not left as current."""
    with EnrichmentStore(mutable_db) as store:
        # If a row was written for a gated session before the gate existed — as 45 were...
        store.upsert(session_item(MODEL_ONLY), enrichment(), stamp())
        # ...then the sweep takes it, because a row no pass will ever refresh is a zombie by
        # the same definition a row whose session was deleted is. Skipping the item alone
        # would leave it on disk and rendered as current forever.
        assert store.sweep_zombies() == 1
        assert store.connection.execute("SELECT count(*) FROM session_enrichments").fetchone() == (
            0,
        )


def test_the_gate_and_the_sweep_read_one_population(mutable_db: Path) -> None:
    """Whatever the store hands out to describe is exactly what the sweep leaves alone.

    Two names for the population would bill a row every night: the pass describes a session
    and the next sweep deletes it, forever, and no coverage number would ever show it.
    """
    with EnrichmentStore(mutable_db) as store:
        # If every session in the store is enriched, gated or not...
        for (session_id,) in store.connection.execute("SELECT id FROM sessions").fetchall():
            store.upsert(session_item(session_id), enrichment(), stamp())
        store.sweep_zombies()
        # ...then what survives the sweep is precisely what the store hands out to describe.
        assert {
            session_id
            for (session_id,) in store.connection.execute(
                "SELECT session_id FROM session_enrichments"
            ).fetchall()
        } == {item.session_id for item in store.session_items()}


def test_the_run_and_session_views_left_join_too(mutable_db: Path) -> None:
    """Every level's view returns un-enriched rows with empty enrichment columns."""
    # If one of `spine/`'s two agent runs is enriched, and neither session is...
    with EnrichmentStore(mutable_db) as store:
        runs = [item for item in store.run_items() if item.session_id == SPINE]
        store.upsert(runs[0], enrichment("Read one file."), stamp())
        # ...then the runs view returns both, saying which carries no description...
        assert store.connection.execute(
            "SELECT id, description FROM enriched_agent_runs WHERE session_id = ? ORDER BY id",
            [SPINE],
        ).fetchall() == sorted(
            [(runs[0].agent_run_id, "Read one file."), (runs[1].agent_run_id, None)]
        )
        # ...it keeps the run's own recorded task and model under names that say whose they
        # are, so `description` means the enrichment's in all three views...
        assert {
            name
            for (name,) in store.connection.execute(
                "SELECT column_name FROM duckdb_columns() WHERE table_name = 'enriched_agent_runs'"
            ).fetchall()
        } >= {"task_description", "agent_model", "description", "enrichment_model"}
        # ...and the sessions view reads coverage honestly for a corpus nothing has described.
        assert store.connection.execute(
            "SELECT count(*), count(description) FROM enriched_sessions"
        ).fetchone() == (12, 0)


def test_zombies_are_swept_at_all_three_levels(mutable_db: Path) -> None:
    """An enrichment of any level whose base row is gone is deleted with it."""
    # If a turn, a run and a session are each enriched...
    with EnrichmentStore(mutable_db) as store:
        turn_item = spine_turns(store)[0]
        run_item = next(item for item in store.run_items() if item.session_id == SPINE)
        session_item = next(item for item in store.session_items() if item.session_id == SPINE)
        for item in (turn_item, run_item, session_item):
            store.upsert(item, enrichment(), stamp())
        # ...and every base row they hang off is then deleted, as an extractor bump that
        # redraws a session's boundaries would...
        store.connection.execute("DELETE FROM turns WHERE id = ?", [turn_item.turn_id])
        store.connection.execute("DELETE FROM agent_runs WHERE id = ?", [run_item.agent_run_id])
        store.connection.execute("DELETE FROM sessions WHERE id = ?", [SPINE])
        # ...then all three enrichments go, because the LEFT-joined views would otherwise hide
        # them completely.
        assert store.sweep_zombies() == 3
        for table in ("turn_enrichments", "agent_run_enrichments", "session_enrichments"):
            assert store.connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_opening_a_store_creates_every_enrichment_table(mutable_db: Path) -> None:
    """The enrichment schema is created on open, whatever the store held before."""
    with EnrichmentStore(mutable_db) as store:
        names = {
            name
            for (name,) in store.connection.execute(
                "SELECT table_name FROM duckdb_tables()"
            ).fetchall()
        }
        views = {
            name
            for (name,) in store.connection.execute(
                "SELECT view_name FROM duckdb_views()"
            ).fetchall()
        }
    assert {"turn_enrichments", "agent_run_enrichments", "session_enrichments"} <= names
    assert "enriched_turns" in views


def test_a_store_written_by_another_schema_is_refused(tmp_path: Path) -> None:
    """Enrichment refuses a store whose base tables this build cannot read."""
    path = tmp_path / "old.duckdb"
    build_store(path, fixture_transcripts("spine"))
    connection = duckdb.connect(str(path))
    connection.execute("UPDATE meta SET schema_version = 1")
    connection.close()
    with pytest.raises(Exception, match="schema version"):
        EnrichmentStore(path)


def test_stamp_is_the_four_field_staleness_key() -> None:
    """The staleness key is exactly the design's four fields, so a fifth cannot creep in."""
    assert [field.name for field in dataclasses.fields(Stamp)] == [
        "input_hash",
        "prompt_version",
        "taxonomy_version",
        "model",
    ]
