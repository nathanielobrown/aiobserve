"""The records browser: a thread's raw transcript, a page at a time and a record at a time.

This is where a report's citation lands. An analysis finding names `(session_id, source,
line_no)`, so the leaves here are about the walk and the mapping: paging that neither repeats
nor skips a line, and a URL derived from the tuple that opens on the record it names.
"""

import json
import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.app import build_app
from aiobserve.view.store import Page
from tests.conftest import ANCESTOR, MAIN, RESUME, RESUME_LONG_RECORD, SPINE, SPINE_RUN
from tests.view.conftest import MISSING, Planter, block, fields, inside, one, plain, values

# The records a page opens with its own body already fetched, in document order. Read off the
# start tag rather than through `inside`, because what says a record is open is `open` itself —
# an attribute with no value, which nothing keyed by value can see.
OPENED = re.compile(r'<details class="whole" open data-open-record="(\d+)"')


def opened(html: str) -> list[str]:
    """The line numbers of the records the page renders expanded."""
    return OPENED.findall(html)


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
            f"/session/{ANCESTOR}/thread/{MAIN}/records", params={"after": after, "size": 20}
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
    response = client.get(
        f"/session/{session_id}/thread/{source}/records", params={"after": line_no - 1}
    )
    assert response.status_code == 200
    # The cited record is the first row of the page, under the anchor the URL fragment names...
    assert values(response.text, "data-record")[0] == str(line_no)
    assert f'id="L{line_no}"' in response.text
    # ...the row says which kind of record it is, so a citation reads in place...
    assert fields(response.text, "data-record", str(line_no))["type"] == kind
    # ...and it is the one record on the page that arrives open, fetching its own body as the
    # page loads: a reader who followed a citation asked for that record, and a row that
    # landed collapsed made them click for what they came for. The rest of the page waits to
    # be opened, which is what keeps a page of records a page and not a transcript.
    assert opened(response.text) == [str(line_no)]
    assert inside(response.text, "data-open-record", str(line_no), "hx-trigger") == ["load"]
    following = values(response.text, "data-record")[1]
    assert inside(response.text, "data-open-record", following, "hx-trigger") == ["toggle once"]
    # ...and the page cites the query it ran, at this request's cursor and the size it took
    # by default, so a reader can re-run what produced the rows around the cited one.
    assert fields(response.text, "id", "citation") == {
        "view_records": f"-- queries/view_records.sql session_id={session_id} source={source}"
        f" after={line_no - 1} page_records={bounds.RECORDS.default}"
        f" preview_chars={queries.RECORD_PREVIEW}"
    }


def test_a_record_too_wide_to_weigh_waits_for_a_click(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A page opens its first record only where fetching it stays inside a page's budget.

    The open row is a fetch nobody clicked, so what it costs is what the page costs — and a
    record is the one value the store holds no bound over: the canonical store archives one of
    7.6 million characters, which renders to nine megabytes. A reader who paged here rather
    than following a citation never asked for it at all.

    So the row opens itself up to `bounds.OPENED_RECORD_CHARS` and stays a click away past it,
    which is the same page either way — the record is a fetch in both, and the difference is
    who triggers it. Planted at the boundary in both directions, because no recorded record
    sits on it.
    """
    widths = ((bounds.OPENED_RECORD_CHARS, True), (bounds.OPENED_RECORD_CHARS + 1, False))
    for length, opens in widths:
        path = plant(
            (
                "UPDATE raw_records SET raw = ?"
                " WHERE session_id = ? AND source = ? AND line_no = ?",
                ["&" * length, RESUME, MAIN, RESUME_LONG_RECORD],
            )
        )
        with TestClient(build_app(path)) as planted:
            page = planted.get(
                f"/session/{RESUME}/thread/{MAIN}/records", params={"after": RESUME_LONG_RECORD - 1}
            ).text
        # The cited record is the first row of the page whichever side of the line it falls...
        assert values(page, "data-record")[0] == str(RESUME_LONG_RECORD)
        assert fields(page, "data-record", str(RESUME_LONG_RECORD))["raw_chars"] == f"{length:,}"
        # ...and the row carries the fetch either way. What the width decides is whether the
        # page pulls it as it loads or waits for the reader to open the row.
        trigger = "load" if opens else "toggle once"
        assert opened(page) == ([str(RESUME_LONG_RECORD)] if opens else [])
        assert inside(page, "data-open-record", str(RESUME_LONG_RECORD), "hx-trigger") == [trigger]


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
            f"/session/{RESUME}/thread/{MAIN}/records", params={"after": RESUME_LONG_RECORD - 1}
        ).text,
        "data-record",
        str(RESUME_LONG_RECORD),
    )
    # Through the same formatter every count on a page goes through. This record is the one
    # recorded value long enough to tell the two spellings apart: 3,054 against 3054.
    assert row["raw_chars"] == f"{len(stored):,}"
    assert len(row["raw_head"]) <= queries.RECORD_PREVIEW


def test_every_number_the_records_browser_prints_carries_its_separators(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """The browser's counts go through the same formatter every count on a page does.

    Planted, because the corpus's densest recorded thread archives 47 lines: under a thousand
    a formatted count and a bare one are the same string. The clones are of a recorded record
    given line numbers of their own, so what the page counts stays the archived population —
    and they carry no uuid, a shape the store records for a summary line.
    """
    over = 1_200
    (recorded,) = one(
        store,
        "SELECT count(*) FROM raw_records WHERE session_id = ? AND source = ?",
        [ANCESTOR, MAIN],
    )
    path = plant(
        (
            "INSERT INTO raw_records (SELECT r.* REPLACE (r.line_no + i * 1000 AS line_no,"
            " NULL AS uuid) FROM raw_records r, range(1, ?) t(i)"
            " WHERE r.session_id = ? AND r.source = ? AND r.line_no ="
            " (SELECT min(line_no) FROM raw_records WHERE session_id = ? AND source = ?))",
            [over + 1, ANCESTOR, MAIN, ANCESTOR, MAIN],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{ANCESTOR}/thread/{MAIN}/records").text
        held = recorded + over
        on_page = values(page, "data-record")
        assert len(on_page) == bounds.RECORDS.default
        # What the thread holds from this cursor on, and what the page left behind it, both
        # grouped in threes — and the plant pushed each past where that is a claim.
        assert fields(page, "id", "records")["matched"] == f"{held:,}"
        (after,) = values(page, "data-more-records")
        assert fields(page, "data-more-records", after)["count"] == f"+{held - len(on_page):,} more"
        # And the reader gets there by clicking, so the next page is fetched through the link
        # the page wrote rather than one this test composed — the only way a change to that
        # URL's shape fails here rather than in a browser.
        (link,) = inside(page, "data-more-records", after, "href")
        following = planted.get(link)
    assert following.status_code == 200, link
    # The cursor carried the reader forward: a full page again, and none of it a repeat.
    next_page = values(following.text, "data-record")
    assert len(next_page) == bounds.RECORDS.default
    assert set(next_page).isdisjoint(on_page)


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
    # Through the fetch the row itself carries, opened at that record. Minting the URL here
    # would leave the template free to write any shape it liked and this test still green.
    browser = client.get(
        f"/session/{RESUME}/thread/{MAIN}/records", params={"after": RESUME_LONG_RECORD - 1}
    ).text
    (fetch,) = inside(browser, "data-open-record", str(RESUME_LONG_RECORD), "hx-get")
    served = client.get(fetch)
    assert served.status_code == 200, fetch
    shown = fields(served.text, "data-record-value", str(RESUME_LONG_RECORD))
    # The whole record arrived — indented and marked up, so it is read back through the
    # markup: every field the store holds, and nothing the page invented.
    assert json.loads(plain(block(served.text, "raw"))) == json.loads(stored)
    # ...saying its stored length in the grouping every count on a page carries...
    assert shown["raw_chars"] == f"{len(stored):,}"
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
            client.get(
                f"/fragment/record/session/{session_id}/thread/{source}/line/{line_no}"
            ).text,
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
        assert link == [f"/session/{SPINE}/thread/{source}/records"], page
        assert client.get(link[0]).status_code == 200


def test_every_turn_links_to_the_record_it_was_read_from(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn's pane reaches the transcript line the extractor read that turn from.

    `turns.id` is a `raw_records.uuid` in the same `(session_id, source)` — the store's own
    join, not a guess about line numbers — which is what makes the link derivable at all. The
    line also arrives whole on open, from the same route the records browser uses.
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
    # Every turn of this thread was read from a record, so no turn page goes unlinked.
    assert len(behind) == turns > 0, "the fixture session lost its turn-to-record join"
    for turn_id, line_no in behind.items():
        page = client.get(f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}").text
        # The link opens the browser at that turn's own line and no other's...
        url = f"/session/{SPINE}/thread/{MAIN}/records?after={line_no - 1}#L{line_no}"
        assert inside(page, "class", "raw", "href") == [
            f"/session/{SPINE}/thread/{MAIN}/records",
            url,
        ], turn_id
        # ...and the closed block beside it fetches the same record whole, again through the
        # URL the pane wrote: this is the third place a record URL is spelled out by hand.
        assert values(page, "data-open-record") == [str(line_no)], turn_id
        (fetch,) = inside(page, "data-open-record", str(line_no), "hx-get")
        assert values(client.get(fetch).text, "data-record-value") == [str(line_no)], fetch
    # And the link lands on the record, which is the whole point of deriving it this way.
    line = next(iter(behind.values()))
    landed = client.get(f"/session/{SPINE}/thread/{MAIN}/records", params={"after": line - 1})
    assert values(landed.text, "data-record")[0] == str(line)


def test_a_record_the_store_does_not_hold_is_a_404(client: TestClient) -> None:
    """A thread or a line the store does not hold is a 404, not an empty browser."""
    for path in (
        f"/session/{ANCESTOR}/thread/{MISSING}/records",
        f"/session/{MISSING}/thread/{MAIN}/records",
        f"/fragment/record/session/{ANCESTOR}/thread/{MAIN}/line/999999",
    ):
        response = client.get(path)
        assert response.status_code == 404, path


def test_a_records_page_size_outside_its_bounds_is_refused(client: TestClient) -> None:
    """A hand-typed page size past the ceiling is a 400, not a page nothing bounds."""
    for size in (0, bounds.RECORDS.ceiling + 1):
        response = client.get(f"/session/{ANCESTOR}/thread/{MAIN}/records", params={"size": size})
        assert response.status_code == 400, size
