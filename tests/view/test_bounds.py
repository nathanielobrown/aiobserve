"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Two mechanisms, checked separately: the queries behind the pages never select an unbounded
fat column, and what they do select is truncated in SQL rather than in the template — so
the bound holds however large the record on the other side of it is.
"""

import re
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view.app import PAGE_SESSIONS, Page, build_app
from tests.conftest import SPINE
from tests.view.conftest import Planter, fields, one

# The columns that hold whatever the agent read or wrote: one of them can be megabytes, and
# none of them belongs on a page whole. `raw` is a transcript line, `result` a tool's output,
# `input` its arguments, `text` and `thinking` a model's answer.
FAT = ("raw", "text", "thinking", "result", "input", "content")

# What a page may weigh, and what one session's row in the list may add to it. The list is
# the page a corpus grows, so `PAGE_SESSIONS` rows at `SESSION_BYTES` each have to fit.
PAGE_BYTES = 350_000
SESSION_BYTES = 2_000

# How much of a turn's prompt the timeline shows, from `session_digest`'s own `substr`.
PROMPT_CHARS = 300


def unbounded(sql: str) -> set[str]:
    """The fat columns a statement selects outside a `substr` — what a page cannot afford."""
    without_comments = re.sub(r"--[^\n]*", " ", sql)
    truncated = re.sub(r"substr\s*\([^()]*\)", " ", without_comments)
    return {word for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", truncated) if word in FAT}


def test_the_fat_column_scan_catches_one() -> None:
    """The scan below is worth its green: it flags a select the pages must not contain.

    The two statements are invented — no shipped query selects a fat column whole, which is
    exactly why the instrument needs its own case.
    """
    assert unbounded("SELECT r.raw FROM raw_records r -- text") == {"raw"}
    assert unbounded("SELECT substr(r.raw, 1, 200) AS raw_head FROM raw_records r") == set()


@pytest.mark.parametrize("page", sorted(Page))
def test_no_page_query_selects_a_fat_column_whole(page: Page) -> None:
    """Every query behind a page is bounded in SQL, whatever the record it reads holds."""
    assert unbounded(queries.load(page)) == set()


def test_every_fat_column_is_still_a_column(store: duckdb.DuckDBPyConnection) -> None:
    """The scan is spelled in column names, so a rename must fail here rather than pass."""
    named = {
        row[0]
        for row in store.execute(
            "SELECT column_name FROM duckdb_columns() WHERE schema_name = 'main'"
        ).fetchall()
    }
    assert set(FAT) <= named


def test_a_served_page_stays_under_its_ceiling(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """No page the viewer serves is large enough to stall a browser, at any corpus size."""
    listing = len(client.get("/").content)
    (count,) = one(store, "SELECT count(*) FROM sessions")
    assert listing < PAGE_BYTES
    # The fixture corpus is smaller than a page, so its own weight proves nothing about a
    # large one. What does is the marginal cost of a row — the whole list less the same page
    # holding one session — which is what a growing corpus multiplies.
    chrome = len(client.get("/?size=1").content)
    per_session = (listing - chrome) / (count - 1)
    assert per_session < SESSION_BYTES
    # A full page is the most the list ever serves, and that is the number under the ceiling.
    assert chrome + per_session * PAGE_SESSIONS < PAGE_BYTES
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        assert len(client.get(f"/session/{session_id}").content) < PAGE_BYTES, session_id


def test_a_long_prompt_is_cut_before_it_reaches_the_page(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn's prompt is truncated in the query, so a huge one cannot bloat a page."""
    # A 5,000-character prompt, invented: a redacted fixture carries nothing near the cap...
    turn_id, _ = one(
        store,
        'SELECT id, "index" FROM turns WHERE session_id = ? AND source = \'main\' ORDER BY "index"',
        [SPINE],
    )
    path: Path = plant(
        (
            "UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?",
            ["x" * 5_000, SPINE, turn_id],
        )
    )
    # ...and what the page shows of it is the cap, not the prompt.
    with TestClient(build_app(path)) as client:
        page = client.get(f"/session/{SPINE}").text
    assert len(fields(page, "data-turn", turn_id)["prompt"]) == PROMPT_CHARS
