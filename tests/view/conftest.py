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
from html import unescape
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
    """Every page one store can serve — the list, and every node of every session it holds.

    One URL per node the tree can reach, read from the store the way the routes read it, so a
    sweep over this list is a sweep over the whole viewer rather than over the two pages that
    used to exist. Every URL here answers 200: the two buckets are included only where the
    store has something to put in them, because an empty bucket is a node that is not there.
    """
    urls = ["/"]
    urls += [f"/session/{row[0]}" for row in store.execute("SELECT id FROM sessions").fetchall()]
    kinds = {
        "run": "SELECT session_id, NULL, id FROM live_agent_runs",
        "turn": "SELECT session_id, source, id FROM live_turns",
        "call": "SELECT session_id, source, id FROM live_api_calls",
        "tool": "SELECT session_id, source, id FROM live_tool_calls",
        "compaction": "SELECT session_id, source, id FROM live_compactions",
    }
    for kind, sql in kinds.items():
        for session_id, source, node_id in store.execute(sql).fetchall():
            # A run's own id is the thread it ran on, so its URL says it once; everything
            # else hangs off the thread it was recorded on.
            head = f"/session/{session_id}"
            urls.append(
                f"{head}/{kind}/{node_id}"
                if source is None
                else f"{head}/thread/{source}/{kind}/{node_id}"
            )
    # A thread's unattributed bucket exists where one of its calls answers no turn *of that
    # thread* — a fork replays calls whose `turn_id` names a turn of the thread it forked from.
    for session_id, source in store.execute(
        "SELECT DISTINCT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "   AND t.id = c.turn_id"
        " WHERE t.id IS NULL"
    ).fetchall():
        urls.append(f"/session/{session_id}/thread/{source}/unattributed")
    # And the session's unattached bucket exists where a run's spawning call resolves to
    # nothing at all, which is the join `view_runs` makes, failing.
    for (session_id,) in store.execute(
        "SELECT DISTINCT a.session_id FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "   AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "   AND c.id = tc.api_call_id"
        " WHERE c.id IS NULL"
    ).fetchall():
        urls.append(f"/session/{session_id}/unattached")
    return urls


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


# What `view_runs` joins, in the expectation's own SQL: a run against the api call that spawned
# it, and the turn that call answers *on the call's own thread*.
SPAWNS = (
    "SELECT a.id, c.source, st.id, c.id FROM live_agent_runs a"
    " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id"
    "  AND tc.source <> a.id"
    " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
    "  AND c.id = tc.api_call_id"
    " LEFT JOIN live_turns st ON st.session_id = c.session_id AND st.source = c.source"
    "  AND st.id = c.turn_id"
    " WHERE a.session_id = ? ORDER BY a.started_at NULLS LAST, a.id"
)
# The same join keyed on one run, for a leaf about a single edge rather than a whole level.
SPAWN_OF = SPAWNS.replace("a.session_id = ?", "a.id = ?")


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


def block(html: str, field: str) -> str:
    """The markup inside one `<pre data-field="…">`, whole.

    What `fields` cannot give back: that reader stops at the first closing tag, and a block of
    marked-up code is nothing but nested spans.
    """
    found = re.search(rf'<pre data-field="{field}"[^>]*>(.*?)</pre>', html, re.DOTALL)
    assert found is not None, f"no {field} block on the page"
    return found.group(1)


def prose(html: str, field: str) -> str:
    """The markup inside one `<div class="prose" data-field="…">`, whole.

    The other half of `block`: what a pane renders as the markdown a session wrote, which is
    nested elements rather than the one run of text a `data-field` reader hands back.
    """
    found = re.search(rf'<div class="prose" data-field="{field}"[^>]*>(.*?)</div>', html, re.DOTALL)
    assert found is not None, f"no {field} prose on the page"
    return found.group(1)


def classed(html: str) -> set[str]:
    """Every class the highlighter wrote into one run of markup.

    Split on whitespace: an element may carry more than one class, and a reader that took the
    attribute whole would silently skip exactly the tokens Pygments has no short name for.
    """
    return {name for found in re.findall(r'class="([^"]*)"', html) for name in found.split()}


def plain(html: str) -> str:
    """What a browser shows of a run of markup: the tags dropped, the escapes undone.

    For the two places a value is marked up rather than printed — highlighted code, and the
    spans a cut leaves behind — where reading the text back is how a leaf proves the markup
    added nothing and lost nothing.
    """
    return unescape(re.sub(r"<[^>]*>", "", html))


def suggestions(page: str) -> list[str]:
    """The project paths the list's filter box offers, in the order it offers them.

    Any attribute may sit in front of the value's own, the way `printed` reads a children log:
    a pattern anchored on the tag's first attribute reads a box the browser fills as an empty
    one, and a leaf asserting that nothing is offered would pass on markup offering everything.
    """
    return re.findall(r'<option\s[^>]*\bvalue="([^"]*)"', page)


def values(html: str, attribute: str) -> list[str]:
    """Every value of one data attribute in the document, in document order."""
    return re.findall(rf'{attribute}="([^"]*)"', html)


# One tree row that stands for a node, depth beside key. Read as a pair rather than as two
# `values` scans because a cap's tail row carries a depth and no key, so the two lists are
# not the same length whenever a level was cut.
_ROW = re.compile(r'data-depth="(\d+)"\s+data-tree="([^"]*)"')


def rows(html: str) -> list[tuple[int, str]]:
    """Every tree row that stands for a node: its depth beside its key, in document order."""
    return [(int(depth), key) for depth, key in _ROW.findall(html)]


def kin(html: str) -> list[str]:
    """The children the tree opened under the selection, as node keys in document order.

    The rows one level below the open chain, which is what the crumbs count. Everything else
    on the tree is an ancestor, an ancestor's sibling, or a tail row.
    """
    depth = len(values(html, "data-crumb"))
    return [key for at, key in rows(html) if at == depth]


class _Element(HTMLParser):
    """What sits inside the one element carrying `attribute="value"`."""

    def __init__(self, attribute: str, value: str) -> None:
        super().__init__()
        self.attribute = attribute
        self.value = value
        self.depth = 0
        self.field: str | None = None
        self.fields: dict[str, str] = {}
        self.marking = False
        self.marks: list[str] = []
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
        elif "icon" in (found.get("class") or "").split():
            self.marking = True

    def handle_data(self, data: str) -> None:
        if self.field is not None:
            self.fields[self.field] += data
        elif self.marking:
            self.marks.append(data)

    def handle_endtag(self, tag: str) -> None:
        self.field = None
        self.marking = False
        if self.depth:
            self.depth -= 1


def _element(html: str, attribute: str, value: str) -> _Element:
    parser = _Element(attribute, value)
    parser.feed(html)
    return parser


def fields(html: str, attribute: str, value: str) -> dict[str, str]:
    """One element's labelled fields, keyed by `data-field` and stripped of whitespace."""
    return {name: text.strip() for name, text in _element(html, attribute, value).fields.items()}


def icons(html: str, attribute: str, value: str) -> list[str]:
    """The bare marks inside the element carrying `attribute="value"`, in document order.

    Read by class rather than by a `data-` key: a mark is not a value the store holds, so it
    carries no `data-field` (`.claude/rules/viewer-ui.md`) — and a key naming it would be
    twenty bytes on every one of a node page's 3,217 tree rows.
    """
    return _element(html, attribute, value).marks


def inside(html: str, attribute: str, value: str, inner: str) -> list[str]:
    """Every `inner` attribute value found inside the element carrying `attribute="value"`."""
    return [
        found for tag in _element(html, attribute, value).attributes if (found := tag.get(inner))
    ]


# Attributes htmx reads off the closest ancestor that carries one, so a page can write the
# half every link shares once and leave each link carrying only what differs. `hx-get` and
# `href` are not among them: htmx finds the elements to wire by their own `hx-get`.
INHERITED = ("hx-target", "hx-swap", "hx-select", "hx-select-oob", "hx-push-url")
# Tags that never close, so the reader below must not wait for an end tag to pop them.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


class _Wiring(HTMLParser):
    """Every `hx-get` element's wiring as htmx composes it, keyed by the row it sits in."""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key
        self.stack: list[dict[str, str | None]] = []
        self.wiring: list[tuple[str, dict[str, str]]] = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        self.stack.append(found)
        if tag not in VOID and "hx-get" in found:
            # Innermost first, which is the order htmx resolves an inherited attribute in.
            near = list(reversed(self.stack))
            row = next((tag[self.key] for tag in near if tag.get(self.key)), None)
            if row is not None:
                self.wiring.append(
                    (
                        row,
                        {
                            name: value
                            for name in ("href", "hx-get", *INHERITED)
                            if (value := next((at[name] for at in near if at.get(name)), None))
                            is not None
                        },
                    )
                )
        if tag in VOID:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()


def wired(html: str, key: str) -> list[tuple[str, dict[str, str]]]:
    """What htmx would do, for every fetching element under a `key` attribute, in page order.

    Inheritance and all: the tree writes the swap its rows share on the element it hands back,
    so an assertion on a row's own attributes would read a page that works and one that does
    not the same way. Each pair is the `key` of the row an element sits in and its wiring; a
    row holding two of them — a link and a body toggle — gives two pairs.
    """
    parser = _Wiring(key)
    parser.feed(html)
    return parser.wiring
