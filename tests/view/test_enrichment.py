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
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION
from aiobserve.view.app import build_app
from aiobserve.view.enrichment import GLYPH, GLYPH_CLASS
from aiobserve.view.format import cut, when
from aiobserve.view.store import Page
from tests.conftest import SPINE, SPINE_RUN
from tests.view.conftest import Planter, fields, inside, one, pages, values

# Every enrichment table, and the statement that empties one — the second absent-safety case.
EMPTIED = tuple((f"DELETE FROM {spec.table}", ()) for spec in LEVELS.values())


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
    listing = enriched_client.get("/sessions").text
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


def test_the_work_cell_counts_the_turn_categories_a_pass_described(
    enriched_client: TestClient,
    enriched_store: duckdb.DuckDBPyConnection,
    client: TestClient,
) -> None:
    """A row says what kind of work its session's turns were, ranked and cut.

    The one column of the list a pass writes rather than the store reads: a session's turn
    categories say what it spent its time on, which no count of turns or tools does. It is
    absent from a store no pass has run over — an empty column would be a claim the store
    cannot support.
    """
    row = fields(enriched_client.get("/sessions").text, "data-session-id", SPINE)
    kinds = enriched_store.execute(
        "SELECT category, count(*) FROM turn_enrichments WHERE session_id = ?"
        " GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?",
        [SPINE, queries.LIST_CATEGORIES],
    ).fetchall()
    assert kinds, "the described corpus no longer describes this session's turns"
    assert row["work"] == ", ".join(f"{name} ×{turns}" for name, turns in kinds)
    # A store with no enrichment tables at all renders the same row without the column.
    assert "work" not in fields(client.get("/sessions").text, "data-session-id", SPINE)


def test_every_described_node_carries_its_own_words_on_its_own_page(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A pass describes turns and runs, and each one's page shows what it said about it.

    One node per response is the whole point of the browser: the pane reads the selection, so
    a description belongs on the described node's page rather than repeated down a list of
    them. Swept over every described turn and run of one session, because the two levels are
    keyed differently — a turn by its thread, a run by the session.
    """
    for turn_id, said in enrichment_of(enriched_store, Level.turn, SPINE).items():
        page = enriched_client.get(f"/session/{SPINE}/turn/main/{turn_id}").text
        shown = fields(page, "data-enrichment", turn_id)
        assert (shown["description"], shown["category"], shown["outcome"]) == said, turn_id
        # And it is the only enrichment on the page: the tree rows beside it are labels.
        assert values(page, "data-enrichment") == [turn_id], turn_id
    for run_id, said in enrichment_of(enriched_store, Level.agent_run, SPINE).items():
        page = enriched_client.get(f"/session/{SPINE}/run/{run_id}").text
        shown = fields(page, "data-enrichment", run_id)
        assert (shown["description"], shown["category"], shown["outcome"]) == said, run_id


def test_a_run_page_shows_the_runs_own_enrichment_beside_its_brief(
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
    # The run's recorded task keeps its own place, as one of the pane's own values — what the
    # run was asked to do and what it did are two different sentences.
    assert values(page, "data-detail") == ["description"]


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
    shown = served[f"/session/{session_id}/turn/main/{turn_id}"].text
    assert values(shown, "data-selected") == [f"turn:{turn_id}"]
    assert values(shown, "data-enrichment") == []


def wrote(store: duckdb.DuckDBPyConnection, turn_id: str) -> str:
    """What the pane's glyph should say about one described turn, read off the store.

    The provenance a reader hovers for: which model wrote the line, when, under which two
    versions, and whether this build has moved past them.
    """
    model, at, prompt_version, taxonomy = one(
        store,
        "SELECT model, enriched_at, prompt_version, taxonomy_version FROM turn_enrichments"
        " WHERE turn_id = ?",
        [turn_id],
    )
    aged = prompt_version != PROMPT_VERSION[Level.turn] or taxonomy != TAXONOMY_VERSION
    return (
        f"{model} · {when(at)} · prompt v{prompt_version} · taxonomy v{taxonomy}"
        f" · {'stale' if aged else 'fresh'}"
    )


def test_an_item_described_under_an_older_prompt_is_marked_stale(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A description this build's prompt would no longer produce says so, quietly.

    Only the versions are visible from a read: whether the rendered content moved, or which
    model a pass would use today, is not something the store can answer. The tag says which
    of the two states a row is in; the glyph beside the line says what it was written under,
    which is what a reader needs to decide whether to re-run a pass.
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
    stale_page = enriched_client.get(f"/session/{session_id}/turn/main/{turn_id}").text
    assert fields(stale_page, "data-enrichment", turn_id).get("stale") == "stale"
    # ...and one described under the current one is not, so the tag is telling them apart.
    fresh_page = enriched_client.get(f"/session/{fresh[0]}/turn/main/{fresh[1]}").text
    assert "stale" not in fields(fresh_page, "data-enrichment", fresh[1])
    # Both carry the same glyph, and its tooltip is where the two rows differ in full: the
    # model, the hour, the two versions, and which side of them this build is on.
    assert fields(stale_page, "data-enrichment", turn_id)["enriched"] == GLYPH
    assert inside(stale_page, "data-enrichment", turn_id, "title") == [
        wrote(enriched_store, turn_id)
    ]
    assert inside(fresh_page, "data-enrichment", fresh[1], "title") == [
        wrote(enriched_store, fresh[1])
    ]


def test_a_tree_row_the_model_named_carries_a_bare_glyph(
    enriched_client: TestClient, enriched_store: duckdb.DuckDBPyConnection
) -> None:
    """A row named by the pass says so with the glyph alone — no tooltip, no second copy.

    The tree is the page's multiplied part: a row carries `TREE_ROW_BYTES` and no more, so
    the provenance a pane spells out is a mark here. Read against a described turn and an
    undescribed one of the same thread, because a glyph on every row would say nothing.
    """
    described = enrichment_of(enriched_store, Level.turn, SPINE)
    turn_id = next(iter(described))
    page = enriched_client.get(f"/session/{SPINE}").text
    # The described row is labelled with what the pass said, cut to the width of the tree...
    assert fields(page, "data-tree", f"turn:{turn_id}")["label"] == cut(
        described[turn_id][0], queries.NAV_CHARS
    )
    # ...and marked as the model's words, with nothing hanging off the mark.
    assert GLYPH_CLASS in inside(page, "data-tree", f"turn:{turn_id}", "class")
    assert not inside(page, "data-tree", f"turn:{turn_id}", "title")
    # The one turn of the corpus no pass reached sits on another session's tree, labelled by
    # what the session itself recorded and carrying no mark.
    bare_session, bare = one(
        enriched_store,
        "SELECT t.session_id, t.id FROM live_turns t LEFT JOIN turn_enrichments e"
        "  ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id"
        " WHERE t.source = 'main' AND e.turn_id IS NULL",
    )
    undescribed = enriched_client.get(f"/session/{bare_session}").text
    assert GLYPH_CLASS not in inside(undescribed, "data-tree", f"turn:{bare}", "class")


def test_a_model_written_description_is_escaped_like_any_other_transcript_text(
    enriched_plant: Planter,
) -> None:
    """A description is written from a private transcript, so it reaches the page as text.

    The value is invented and has to be: it is what a model would have to be talked into
    writing, which is the case the escaping is for.
    """
    injected = "<script>alert('x')</script> & <b>bold</b>"
    path: Path = enriched_plant(
        ("UPDATE session_enrichments SET description = ?, friction = ?", [injected, injected]),
        # The map labels a node by what the pass said the turn did, so the same words reach a
        # second surface by a second route — as a name rather than as a paragraph.
        ("UPDATE turn_enrichments SET description = ?", [injected]),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # Nothing the model wrote opened a tag, in the pane or on the tree beside it...
    assert "<script>" not in page and "<b>bold</b>" not in page
    # ...and the reader still sees the text it wrote, as the session's own summary...
    shown = fields(page, "data-enrichment", SPINE)
    assert shown["description"] == injected and shown["friction"] == injected
    # ...and as the label of every turn row, which is the second surface and the second route.
    labelled = [key for key in values(page, "data-tree") if key.startswith("turn:")]
    assert labelled, "the session that carries the fixture turn tree no longer opens one"
    for key in labelled:
        assert fields(page, "data-tree", key)["label"] == injected[: queries.NAV_CHARS]


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
    turns = values(page, "data-child")
    assert turns, "the fixture run whose thread this reads no longer holds a turn"
    assert not {key.removeprefix("turn:") for key in turns} & set(values(page, "data-enrichment"))
    # The run's own description is there, which is what covers them.
    assert SPINE_RUN in values(page, "data-enrichment")
