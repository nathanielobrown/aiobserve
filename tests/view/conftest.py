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
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from aiobserve.view.app import build_app

Statement = tuple[str, Sequence[str | int]]
Planter = Callable[..., Path]

# An id that matches nothing, in the shape a session id has. Every "the store does not hold
# it" leaf asks for this one, whatever kind of id the route takes.
MISSING = "00000000-0000-0000-0000-000000000000"


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


@pytest.fixture
def plant(corpus_db: Path, tmp_path: Path) -> Planter:
    """A copy of the corpus with statements run against it, for a planted sentinel.

    Every plant lands on a real row: the recorded session stays what it was and one column
    carries an invented value. It is the only way to test markup or an oversized field
    against fixtures whose strings redaction already flattened.
    """

    def build(*statements: Statement) -> Path:
        path = tmp_path / "planted.duckdb"
        path.write_bytes(corpus_db.read_bytes())
        connection = duckdb.connect(str(path))
        try:
            for sql, parameters in statements:
                connection.execute(sql, list(parameters))
        finally:
            connection.close()
        return path

    return build


def one(
    store: duckdb.DuckDBPyConnection, sql: str, parameters: Sequence[str] = ()
) -> tuple[Any, ...]:
    """The single row an expectation reads from the store; no row means the test is wrong."""
    row = store.execute(sql, list(parameters)).fetchone()
    assert row is not None, f"the store answered nothing: {sql}"
    return row


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
