"""Scaffolding for the viewer tier: a client over the fixture corpus, and an HTML reader.

The store is the one every tier shares (`tests/conftest.py`), opened read-only through the
app itself — nothing is mocked, so every assertion here is on served HTML or a status code.
A test that needs a value no redacted fixture carries copies the store and plants one.

The reader is deliberately thin: it pulls values out of `data-` attributes, which the
templates carry for exactly this reason. A test that had to match rendered prose would fail
on a wording change, and a test that read the database instead would prove nothing about
the page.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.view.app import build_app
from tests.conftest import MAIN, SPINE

Statement = tuple[str, Sequence[str | int]]
Planter = Callable[..., Path]

# An id that matches nothing, in the shape a session id has. Every "the store does not hold
# it" leaf asks for this one, whatever kind of id the route takes.
MISSING = "00000000-0000-0000-0000-000000000000"


def pages(store: duckdb.DuckDBPyConnection) -> list[str]:
    """Every page one store can serve — the list, every session, every run — as URLs."""
    sessions = [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]
    runs = store.execute("SELECT session_id, id FROM agent_runs").fetchall()
    return (
        ["/"]
        + [f"/session/{session_id}" for session_id in sessions]
        # The map is its own response, and the one surface that reads what a pass wrote to
        # *name* a thing rather than to show it — a sweep of pages alone would miss it.
        + [f"/fragment/nav/{session_id}" for session_id in sessions]
        + [f"/session/{session_id}/run/{run_id}" for session_id, run_id in runs]
    )


@pytest.fixture(scope="session")
def client(corpus_db: Path) -> Iterator[TestClient]:
    """The viewer over the fixture corpus, which nothing in this tier writes to."""
    with TestClient(build_app(corpus_db)) as served:
        yield served


@pytest.fixture(scope="session")
def store(corpus_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection for the expectations — what the page is checked against."""
    connection = duckdb.connect(str(corpus_db), read_only=True)
    connection.execute("SET TimeZone='UTC'")
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def enriched_client(enriched_db: Path) -> Iterator[TestClient]:
    """The viewer over the corpus an enrichment pass has written to, described but for one
    item of each level — the partly-described store every page has to render."""
    with TestClient(build_app(enriched_db)) as served:
        yield served


@pytest.fixture(scope="session")
def enriched_store(enriched_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection to the described corpus, for what its pages are checked against."""
    connection = duckdb.connect(str(enriched_db), read_only=True)
    connection.execute("SET TimeZone='UTC'")
    yield connection
    connection.close()


def planter(base: Path, tmp_path: Path) -> Planter:
    """Copies of one store with statements run against them, for a planted sentinel.

    Every plant lands on a real row: the recorded session stays what it was and one column
    carries an invented value. It is the only way to test markup or an oversized field
    against fixtures whose strings redaction already flattened.
    """

    # One file per call, so a leaf that needs two stores — the same page with a row and without
    # it — can plant both and measure the difference between them.
    planted = 0

    def build(*statements: Statement) -> Path:
        nonlocal planted
        planted += 1
        path = tmp_path / f"planted-{planted}.duckdb"
        path.write_bytes(base.read_bytes())
        connection = duckdb.connect(str(path))
        try:
            for sql, parameters in statements:
                connection.execute(sql, list(parameters))
        finally:
            connection.close()
        return path

    return build


@pytest.fixture
def plant(corpus_db: Path, tmp_path: Path) -> Planter:
    """Planted copies of the fixture corpus, which holds no enrichment table at all."""
    return planter(corpus_db, tmp_path)


@pytest.fixture
def enriched_plant(enriched_db: Path, tmp_path: Path) -> Planter:
    """Planted copies of the described corpus, for what a page shows beside an item."""
    return planter(enriched_db, tmp_path)


def one(
    store: duckdb.DuckDBPyConnection, sql: str, parameters: Sequence[str | int] = ()
) -> tuple[Any, ...]:
    """The single row an expectation reads from the store; no row means the test is wrong."""
    row = store.execute(sql, list(parameters)).fetchone()
    assert row is not None, f"the store answered nothing: {sql}"
    return row


class Chipped(NamedTuple):
    """A recorded run that chips onto a turn, and everything a plant needs to move it."""

    run_id: str
    # The api call the run was spawned from, and the turn that call answers.
    call_id: str
    turn_id: str
    # Where that turn sits in its thread, which is the cursor a page of one turn opens at.
    turn_index: int


def chipped(store: duckdb.DuckDBPyConnection) -> Chipped:
    """The first run of `SPINE` the chip join hangs on a turn of the main thread.

    The join `view_runs` makes, in the expectation's own SQL: a run is a chip when its
    `tool_use_id` names a tool call outside its own transcript, whose api call sits under a
    turn. Read from the store rather than pinned, so a re-recorded fixture moves it.
    """
    run_id, call_id, turn_id, turn_index = one(
        store,
        'SELECT a.id, c.id, t.id, t."index" FROM live_agent_runs a'
        " JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id"
        "  AND tc.source <> a.id"
        " JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " JOIN live_turns t ON t.session_id = a.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE a.session_id = ? AND c.source = ? ORDER BY a.id LIMIT 1",
        [SPINE, MAIN],
    )
    return Chipped(run_id, call_id, turn_id, turn_index)


def values(html: str, attribute: str) -> list[str]:
    """Every value of one data attribute in the document, in document order."""
    return re.findall(rf'{attribute}="([^"]*)"', html)


class _Element(HTMLParser):
    """What sits inside the one element carrying `attribute="value"`."""

    def __init__(self, attribute: str, value: str) -> None:
        super().__init__()
        self.attribute = attribute
        self.value = value
        self.depth = 0
        self.field: str | None = None
        self.fields: dict[str, str] = {}
        self.attributes: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if self.depth:
            self.depth += 1
        elif found.get(self.attribute) == self.value:
            self.depth = 1
        if not self.depth:
            return
        self.attributes.append(found)
        if (name := found.get("data-field")) is not None:
            self.field = name
            self.fields.setdefault(name, "")

    def handle_data(self, data: str) -> None:
        if self.field is not None:
            self.fields[self.field] += data

    def handle_endtag(self, tag: str) -> None:
        self.field = None
        if self.depth:
            self.depth -= 1


def _element(html: str, attribute: str, value: str) -> _Element:
    parser = _Element(attribute, value)
    parser.feed(html)
    return parser


def fields(html: str, attribute: str, value: str) -> dict[str, str]:
    """One element's labelled fields, keyed by `data-field` and stripped of whitespace."""
    return {name: text.strip() for name, text in _element(html, attribute, value).fields.items()}


def inside(html: str, attribute: str, value: str, inner: str) -> list[str]:
    """Every `inner` attribute value found inside the element carrying `attribute="value"`."""
    return [
        found for tag in _element(html, attribute, value).attributes if (found := tag.get(inner))
    ]
