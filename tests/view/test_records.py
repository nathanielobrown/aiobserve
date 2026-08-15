"""The records browser: a thread's raw transcript, a page at a time and a record at a time.

This is where a report's citation lands. An analysis finding names `(session_id, source,
line_no)`, so the leaves here are about the walk and the mapping: paging that neither repeats
nor skips a line, and a URL derived from the tuple that opens on the record it names.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.store import Page
from tests.conftest import ANCESTOR, MAIN, RESUME, RESUME_LONG_RECORD, SPINE, SPINE_RUN
from tests.view.conftest import MISSING, fields, inside, one, values


def test_the_browser_pages_by_line_number_without_repeating_or_skipping(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Following a thread's pages shows every record it holds once, in line order.

    Paged at 20 against the corpus's densest recorded thread, which holds 47 — so the page
    boundary is a real overflow of recorded data rather than a staged one.
    """
    archived = [
        str(row[0])
        for row in store.execute(
            "SELECT line_no FROM raw_records WHERE session_id = ? AND source = ? ORDER BY line_no",
            [ANCESTOR, MAIN],
        ).fetchall()
    ]
    assert len(archived) == 47, "the densest fixture thread moved: re-pick the session"
    # Walking from before the first line, taking the cursor each page hands back...
    seen: list[str] = []
    after = queries.FIRST_PAGE
    for _ in range(4):
        page = client.get(
            f"/session/{ANCESTOR}/records/{MAIN}", params={"after": after, "size": 20}
        )
        shown = values(page.text, "data-record")
        assert len(shown) <= 20
        seen += shown
        following = values(page.text, "data-more-records")
        if not following:
            break
        after = int(following[0])
    else:
        pytest.fail("the browser never ran out of pages")
    # ...covers the thread exactly: no line twice, none missed, and none out of order.
    assert seen == archived
    # Keyset, not OFFSET: a page counted off from the start re-reads rows an extract appended.
    assert "OFFSET" not in queries.load(Page.RECORDS).upper()


def test_a_citation_tuple_maps_to_a_working_url(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A report's `(session_id, source, line_no)` opens the page holding that record.

    The mapping is mechanical — `?after={line - 1}#L{line}` — which is why the viewer's URLs
    are natural keys. A report keeps citing the tuple; the URL is derived from it.
    """
    session_id, source, line_no, kind = one(
        store,
        "SELECT session_id, source, line_no, type FROM raw_records"
        " WHERE session_id = ? AND source = ? AND line_no = ?",
        [RESUME, MAIN, str(RESUME_LONG_RECORD)],
    )
    response = client.get(f"/session/{session_id}/records/{source}", params={"after": line_no - 1})
    assert response.status_code == 200
    # The cited record is the first row of the page, under the anchor the URL fragment names...
    assert values(response.text, "data-record")[0] == str(line_no)
    assert f'id="L{line_no}"' in response.text
    # ...and the row says which kind of record it is, so a citation reads in place.
    assert fields(response.text, "data-record", str(line_no))["type"] == kind


def test_a_record_row_shows_a_preview_and_the_length_it_was_cut_from(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A browser row previews its record and says how much of it is not shown.

    The recorded record here is 3,054 characters against a 160-character preview, so the cut
    is a real one — and the row carries the full length, which is what tells a reader the
    preview is a preview.
    """
    (stored,) = one(
        store,
        "SELECT raw FROM raw_records WHERE session_id = ? AND source = ? AND line_no = ?",
        [RESUME, MAIN, str(RESUME_LONG_RECORD)],
    )
    assert len(stored) > queries.RECORD_PREVIEW * 10
    row = fields(
        client.get(
            f"/session/{RESUME}/records/{MAIN}", params={"after": RESUME_LONG_RECORD - 1}
        ).text,
        "data-record",
        str(RESUME_LONG_RECORD),
    )
    assert row["raw_chars"] == str(len(stored))
    assert len(row["raw_head"]) <= queries.RECORD_PREVIEW


def test_a_record_fragment_holds_the_one_record_it_names(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Opening a row fetches that record whole, and none of its neighbours.

    A record is a per-value fetch — the browser's rows are previews, and this is the only
    route that ships a whole `raw`.
    """
    (stored,) = one(
        store,
        "SELECT raw FROM raw_records WHERE session_id = ? AND source = ? AND line_no = ?",
        [RESUME, MAIN, str(RESUME_LONG_RECORD)],
    )
    served = client.get(f"/fragment/record/{RESUME}/{MAIN}/{RESUME_LONG_RECORD}")
    assert served.status_code == 200
    shown = fields(served.text, "data-record-value", str(RESUME_LONG_RECORD))
    # The whole record arrived — pretty-printed, so at least as long as what was stored...
    assert len(shown["raw"]) >= len(stored)
    # ...and no other line of the same thread rode along with it.
    assert values(served.text, "data-record-value") == [str(RESUME_LONG_RECORD)]


def test_a_record_shows_its_uuid_only_when_it_has_one(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A record's uuid is shown when Claude Code wrote one, and omitted when it did not.

    The column is nullable — a summary record carries no uuid — so an unguarded template
    would print the string "None" where an id someone could search for belongs.
    """
    for held, expected in ((True, "an id"), (False, None)):
        session_id, source, line_no, uuid = one(
            store,
            "SELECT session_id, source, line_no, uuid FROM raw_records"
            f" WHERE uuid IS {'NOT NULL' if held else 'NULL'} LIMIT 1",
        )
        assert (uuid is not None) is held, "the corpus lost one of the two shapes"
        shown = fields(
            client.get(f"/fragment/record/{session_id}/{source}/{line_no}").text,
            "data-record-value",
            str(line_no),
        )
        assert shown.get("uuid") == (uuid if expected else None)
        assert "None" not in shown.values()


def test_a_thread_page_links_to_the_transcript_behind_it(client: TestClient) -> None:
    """A session page and a run page each reach their own thread's records in one click."""
    for page, source in (
        (f"/session/{SPINE}", MAIN),
        (f"/session/{SPINE}/run/{SPINE_RUN}", SPINE_RUN),
    ):
        link = inside(client.get(page).text, "data-field", "records", "href")
        assert link == [f"/session/{SPINE}/records/{source}"], page
        assert client.get(link[0]).status_code == 200


def test_every_turn_links_to_the_record_it_was_read_from(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn on a session page reaches the transcript line the extractor read it from.

    `turns.id` is a `raw_records.uuid` in the same `(session_id, source)` — the store's own
    join, not a guess about line numbers — which is what makes the link derivable at all.
    """
    behind = {
        turn_id: line_no
        for turn_id, line_no in store.execute(
            "SELECT t.id, r.line_no FROM live_turns t JOIN raw_records r"
            " ON r.session_id = t.session_id AND r.source = t.source AND r.uuid = t.id"
            " WHERE t.session_id = ? AND t.source = ?",
            [SPINE, MAIN],
        ).fetchall()
    }
    (turns,) = one(
        store, "SELECT count(*) FROM live_turns WHERE session_id = ? AND source = ?", [SPINE, MAIN]
    )
    # Every turn of this thread was read from a record, so no turn on the page goes unlinked.
    assert len(behind) == turns > 0, "the fixture session lost its turn-to-record join"
    page = client.get(f"/session/{SPINE}").text
    for turn_id, line_no in behind.items():
        url = f"/session/{SPINE}/records/{MAIN}?after={line_no - 1}#L{line_no}"
        # One link per turn, pointing at that turn's own line and no other's.
        assert inside(page, "data-turn", turn_id, "data-record-link") == [str(line_no)], turn_id
        assert url in inside(page, "data-turn", turn_id, "href"), turn_id
    # And the link opens on the record, which is the whole point of deriving it this way.
    line = next(iter(behind.values()))
    landed = client.get(f"/session/{SPINE}/records/{MAIN}", params={"after": line - 1})
    assert values(landed.text, "data-record")[0] == str(line)


def test_a_record_the_store_does_not_hold_is_a_404(client: TestClient) -> None:
    """A thread or a line the store does not hold is a 404, not an empty browser."""
    for path in (
        f"/session/{ANCESTOR}/records/{MISSING}",
        f"/session/{MISSING}/records/{MAIN}",
        f"/fragment/record/{ANCESTOR}/{MAIN}/999999",
    ):
        response = client.get(path)
        assert response.status_code == 404, path


def test_a_records_page_size_outside_its_bounds_is_refused(client: TestClient) -> None:
    """A hand-typed page size past the ceiling is a 400, not a page nothing bounds."""
    for size in (0, bounds.RECORDS.ceiling + 1):
        response = client.get(f"/session/{ANCESTOR}/records/{MAIN}", params={"size": size})
        assert response.status_code == 400, size
