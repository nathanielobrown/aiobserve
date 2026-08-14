"""What the pages show of the model's words, and what they show when there are none.

Enrichment is written by a pass that may never have run against the store a reader points the
viewer at (`docs/enrichment.md`), so absence is the ordinary case rather than the edge: a
store with no enrichment tables, a store whose tables are empty, and a store described but for
the items the pass has not reached yet all have to render. The three leaves below pin those,
and the rest check that what a described store does hold reaches the page — escaped, tagged,
and marked when it was written under a prompt this build has moved past.

The described store is `enriched_db`, whose four model-written fields are invented and say so
(`tests/conftest.py`): no fixture records a model's answer about a private transcript.
"""

from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.enrich.prompts import PROMPT_VERSION, Level
from aiobserve.enrich.store import LEVELS
from aiobserve.view.app import build_app
from aiobserve.view.store import Page
from tests.conftest import SPINE, SPINE_RUN
from tests.view.conftest import Planter, fields, one, values

# Every enrichment table, and the statement that empties one — the second absent-safety case.
EMPTIED = tuple((f"DELETE FROM {spec.table}", ()) for spec in LEVELS.values())


def pages(store: duckdb.DuckDBPyConnection) -> list[str]:
    """Every page one store can serve — the list, every session, every run — as URLs."""
    sessions = [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]
    runs = store.execute("SELECT session_id, id FROM agent_runs").fetchall()
    return (
        ["/"]
        + [f"/session/{session_id}" for session_id in sessions]
        + [f"/session/{session_id}/run/{run_id}" for session_id, run_id in runs]
    )


def enrichment_of(
    store: duckdb.DuckDBPyConnection, level: Level, session_id: str
) -> dict[str, tuple[str, str, str]]:
    """What one store says about a session's items at one level, keyed by item id.

    Read off the enrichment tables rather than the page, so what the page shows is checked
    against the rows a pass actually wrote.
    """
    spec = LEVELS[level]
    key = spec.keys[-1] if level is not Level.session else "session_id"
    source = " AND source = 'main'" if level is Level.turn else ""
    return {
        row[0]: (row[1], row[2], row[3])
        for row in store.execute(
            f"SELECT {key}, description, category, outcome FROM {spec.table}"
            f" WHERE session_id = ?{source}",
            [session_id],
        ).fetchall()
    }


def test_a_session_page_shows_what_the_model_said_about_the_session(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A described session carries its own description, its category and its outcome."""
    page = enriched_client.get(f"/session/{SPINE}").text
    description, category, outcome = enrichment_of(enriched_store, Level.session, SPINE)[SPINE]
    shown = fields(page, "data-enrichment", SPINE)
    assert (shown["description"], shown["category"], shown["outcome"]) == (
        description,
        category,
        outcome,
    )
    # ...and the query behind it is cited like every other query the page ran.
    assert Page.ENRICHMENT.value in fields(page, "id", "citation")


def test_the_session_list_shows_what_the_model_said_about_each_session(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A row of the list carries the head of its session's description and its two tags.

    The list is where a reader picks what to open, so the pass's one-line answer to "what was
    this session" belongs on it — cut to a row's head, because the row is multiplied by the
    page and the whole description is on the session's own page.
    """
    listing = enriched_client.get("/").text
    said = {
        row[0]: (row[1], row[2], row[3])
        for row in enriched_store.execute(
            "SELECT session_id, description, category, outcome FROM session_enrichments"
        ).fetchall()
    }
    listed = values(listing, "data-session-id")
    described = [session_id for session_id in listed if session_id in said]
    # The store is the partly-described one, so the page has rows of both kinds on it...
    assert 0 < len(described) < len(listed)
    for session_id in described:
        row = fields(listing, "data-session-id", session_id)
        description, category, outcome = said[session_id]
        # ...each described row showing a head of what the pass wrote, and both its tags...
        assert row["description"] == description[: queries.LIST_CHARS]
        assert (row["category"], row["outcome"]) == (category, outcome)
    # ...and a session the pass never reached carrying nothing at all beside it.
    assert values(listing, "data-enrichment") == described
    # The query behind that is cited like every other query the page ran.
    assert Page.DESCRIBED_SESSIONS.value in fields(listing, "id", "citation")


def test_a_session_page_tags_every_turn_and_run_the_pass_described(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """Each turn of the timeline and each run beside it carries its own category and outcome.

    A run row links to the run's page, which is where its description is; a turn has no page
    of its own, so the turn row carries the description itself.
    """
    page = enriched_client.get(f"/session/{SPINE}").text
    turns = enrichment_of(enriched_store, Level.turn, SPINE)
    runs = enrichment_of(enriched_store, Level.agent_run, SPINE)
    # Every turn on the page that the pass described shows what it said...
    for turn_id in values(page, "data-turn"):
        shown = fields(page, "data-enrichment", turn_id)
        description, category, outcome = turns[turn_id]
        assert (shown["description"], shown["category"], shown["outcome"]) == (
            description,
            category,
            outcome,
        )
    # ...and every run row shows its two tags, with the description a click away.
    chips = values(page, "data-chip")
    assert chips, "the session that carries the fixture run tree no longer chips one"
    for run_id in chips:
        shown = fields(page, "data-enrichment", run_id)
        assert (shown["category"], shown["outcome"]) == runs[run_id][1:]
        assert "description" not in shown


def test_a_run_page_shows_the_runs_own_enrichment_beside_its_header(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A run's page says what the model said the run did, not just what it was asked to do."""
    page = enriched_client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    description, category, outcome = enrichment_of(enriched_store, Level.agent_run, SPINE)[
        SPINE_RUN
    ]
    shown = fields(page, "data-enrichment", SPINE_RUN)
    assert (shown["description"], shown["category"], shown["outcome"]) == (
        description,
        category,
        outcome,
    )
    # The run's recorded task keeps its own place in the header, under the name it always had.
    assert "description" in fields(page, "id", "run-header")


def test_a_store_no_enrichment_pass_has_touched_renders_every_page(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The viewer over a store holding no enrichment table at all serves every page.

    The ordinary case, not the edge: the tables are created by a pass that writes, and the
    viewer only ever reads. Nothing on the page stands in for the missing rows either — an
    empty tag is noise a reader has to learn to ignore.
    """
    for url in pages(store):
        page = client.get(url)
        assert page.status_code == 200, url
        assert values(page.text, "data-enrichment") == [], url
    # And the store really is the bare one, so the sweep above proves what it claims.
    tables = {row[0] for row in store.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert not tables & {spec.table for spec in LEVELS.values()}


def test_a_store_whose_enrichment_tables_are_empty_renders_every_page(
    enriched_plant: Planter, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A store a pass created but described nothing in renders like a store with no pass at all.

    A pass that quits before its first round leaves exactly this: the three tables, and no
    row in any of them.
    """
    path = enriched_plant(*EMPTIED)
    with TestClient(build_app(path)) as emptied:
        for url in pages(enriched_store):
            page = emptied.get(url)
            assert page.status_code == 200, url
            assert values(page.text, "data-enrichment") == [], url


def test_a_partly_described_store_shows_the_items_it_reached_and_nothing_for_the_rest(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """An item no pass has described yet is simply absent from its page, not a blank tag.

    `enriched_db` leaves the last item of every level undescribed, which is the state a pass
    stopped part way — or one run under `--limit` — leaves behind, and the state any store
    is in while a pass is still going.
    """
    served = {url: enriched_client.get(url) for url in pages(enriched_store)}
    for url, page in served.items():
        assert page.status_code == 200, url
    # Some of them carry descriptions and some carry none, which is what makes this store the
    # partial case rather than either of the two above...
    described = [url for url, page in served.items() if values(page.text, "data-enrichment")]
    assert 0 < len(described) < len(served)
    # ...and the turn the pass never reached is on its page, with nothing beside it.
    session_id, turn_id = one(
        enriched_store,
        "SELECT t.session_id, t.id FROM live_turns t"
        " LEFT JOIN turn_enrichments e"
        "   ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id"
        " WHERE t.source = 'main' AND e.turn_id IS NULL",
    )
    shown = served[f"/session/{session_id}"].text
    assert turn_id in values(shown, "data-turn")
    assert turn_id not in values(shown, "data-enrichment")


def test_an_item_described_under_an_older_prompt_is_marked_stale(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A description this build's prompt would no longer produce says so, quietly.

    Only the versions are visible from a read: whether the rendered content moved, or which
    model a pass would use today, is not something the store can answer.
    """
    session_id, turn_id = one(
        enriched_store,
        "SELECT session_id, turn_id FROM turn_enrichments"
        " WHERE source = 'main' AND prompt_version < ?",
        [PROMPT_VERSION[Level.turn]],
    )
    fresh = one(
        enriched_store,
        "SELECT session_id, turn_id FROM turn_enrichments"
        " WHERE source = 'main' AND prompt_version = ?",
        [PROMPT_VERSION[Level.turn]],
    )
    # The turn described under the older prompt version is tagged...
    stale_page = enriched_client.get(f"/session/{session_id}").text
    assert fields(stale_page, "data-enrichment", turn_id).get("stale") == "stale"
    # ...and one described under the current one is not, so the tag is telling them apart.
    fresh_page = enriched_client.get(f"/session/{fresh[0]}").text
    assert "stale" not in fields(fresh_page, "data-enrichment", fresh[1])


def test_a_model_written_description_is_escaped_like_any_other_transcript_text(
    enriched_plant: Planter,
) -> None:
    """A description is written from a private transcript, so it reaches the page as text.

    The value is invented and has to be: it is what a model would have to be talked into
    writing, which is the case the escaping is for.
    """
    injected = "<script>alert('x')</script> & <b>bold</b>"
    path: Path = enriched_plant(
        ("UPDATE session_enrichments SET description = ?, friction = ?", [injected, injected])
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # Nothing the model wrote opened a tag...
    assert "<script>" not in page and "<b>bold</b>" not in page
    # ...and the reader still sees the text it wrote.
    shown = fields(page, "data-enrichment", SPINE)
    assert shown["description"] == injected and shown["friction"] == injected


def test_a_run_pages_turns_carry_no_description_of_their_own(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A run's turns are described by the run's description, not one apiece.

    A pass describes the main thread's turns and leaves an agent run's to the run — so the
    only enrichment on a run page is the run's own, and its children's tags. The page asks for
    its own thread all the same, because the turn key is `(session, source, turn)`: a page
    that asked for `main` would show one thread's descriptions against another's turns.
    """
    (main_only,) = one(
        enriched_store, "SELECT count(*) FROM turn_enrichments WHERE source <> 'main'"
    )
    assert main_only == 0, "a pass now describes an agent run's turns: the run page can show them"
    page = enriched_client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
    turns = values(page, "data-turn")
    assert turns, "the fixture run whose timeline this reads no longer holds a turn"
    assert not set(turns) & set(values(page, "data-enrichment"))
    # The run's own description is there, which is what covers them.
    assert SPINE_RUN in values(page, "data-enrichment")
