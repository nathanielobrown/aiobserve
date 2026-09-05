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
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple, override

import duckdb
import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from hyphae.analyze import macros
from hyphae.extract.pricing import MODELS
from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.components import layout
from hyphae.view.nodes import BAR_STEPS
from tests.conftest import MAIN, SPINE

Statement = tuple[str, Sequence[str | int]]
Planter = Callable[..., Path]

# An id that matches nothing, in the shape a session id has. Every "the store does not hold
# it" leaf asks for this one, whatever kind of id the route takes.
MISSING = "00000000-0000-0000-0000-000000000000"


# What every list citation says about the display cut, which the viewer composes around the
# query the same way it composes the paging: re-running the file alone answers whole values.
CUT = (
    f"head_chars={bounds.LIST_WIDTHS.head_chars} item_chars={bounds.LIST_WIDTHS.item_chars}"
    f" head_items={bounds.LIST_WIDTHS.head_items}"
)


def money(amount: float) -> str:
    """A cost as the pages print it."""
    return f"${amount:.2f}"


def viewer_css(client: TestClient) -> str:
    """The viewer's own stylesheets as one text, joined in the order the head links them.

    Reading through `layout.STYLESHEETS` is what keeps every test that scans the CSS
    indifferent to how the sheets are split: a rule that moves between files moves nowhere
    a test can see. Pygments' sheet stays out, as it always was — its classes are not the
    viewer's vocabulary.
    """
    return "\n".join(client.get(url).text for url in layout.STYLESHEETS)


def counted(value: int) -> str:
    """A count as the pages print it: thousands separated."""
    return f"{value:,}"


def pages(store: duckdb.DuckDBPyConnection) -> list[str]:
    """Every page one store can serve — the list, and every node of every session it holds.

    One URL per node the NavTree can reach, read from the store the way the routes read it, so a
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


@contextmanager
def reading(path: Path) -> Generator[duckdb.DuckDBPyConnection]:
    """A read-only connection to one store, opened the way a request opens one.

    Macros and all (`view/store.py:open_store`): a library query calls them by name, so a bare
    connection answers a catalog error rather than rows.
    """
    connection = duckdb.connect(str(path), read_only=True)
    connection.execute("SET TimeZone='UTC'")
    macros.install(connection)
    try:
        yield connection
    finally:
        connection.close()


def render_pages(path: Path) -> dict[str, str]:
    """Every page one store serves at the default knobs, keyed by URL and served once.

    Four leaves sweep the corpus this way and assert different properties of the *same* bytes —
    same store file, same knobs, same app — so rendering once and sharing the map changes what
    each of them reads not at all, and costs the run one pass instead of four. The 200 is
    asserted here rather than in each of them: a page that failed never reaches the map, and it
    names the URL that did it.

    A function over a path rather than a fixture over the corpus, so a leaf that plants
    something can rebuild the map over its own copy. A plant in a scratch store can never
    surface through a map built from the untouched corpus, so a sweep that could not be
    rebuilt could not be red-checked either.
    """
    with reading(path) as connection:
        urls = pages(connection)
    with TestClient(build_app(path)) as served:
        rendered = {}
        for url in urls:
            response = served.get(url)
            assert response.status_code == 200, (url, response.status_code)
            rendered[url] = response.text
    return rendered


@pytest.fixture(scope="session")
def corpus_pages(corpus_db: Path) -> Mapping[str, str]:
    """The fixture corpus rendered once, for the leaves that sweep every page of it.

    Session-scoped, and its consumers are marked `xdist_group("corpus_sweep")` so one worker
    renders it once for all of them instead of each worker paying for its own pass.
    """
    return render_pages(corpus_db)


@pytest.fixture(scope="session")
def client(corpus_db: Path) -> Iterator[TestClient]:
    """The viewer over the fixture corpus, which nothing in this tier writes to."""
    with TestClient(build_app(corpus_db)) as served:
        yield served


@pytest.fixture(scope="session")
def store(corpus_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection for the expectations — what the page is checked against."""
    with reading(corpus_db) as connection:
        yield connection


@pytest.fixture(scope="session")
def enriched_client(enriched_db: Path) -> Iterator[TestClient]:
    """The viewer over the corpus an enrichment pass has written to, described but for one
    item of each level — the partly-described store every page has to render."""
    with TestClient(build_app(enriched_db)) as served:
        yield served


@pytest.fixture(scope="session")
def enriched_store(enriched_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection to the described corpus, for what its pages are checked against."""
    with reading(enriched_db) as connection:
        yield connection


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

    What `fields` cannot give back: that reader hands back the text a browser shows, and a
    block of marked-up code is nothing but nested spans.
    """
    found = re.search(rf'<pre data-field="{field}"[^>]*>(.*?)</pre>', html, re.DOTALL)
    assert found is not None, f"no {field} block on the page"
    return found.group(1)


def walled(html: str, field: str) -> str:
    """The class on one `<pre data-field="…">`: the syntax the page marked that value up in.

    Empty where the block carries none, which is a value printed as the characters the store
    holds — the fallback every unmarkable value takes, and a class-presence reading is how a
    leaf tells the two apart without going through the spans inside.
    """
    found = re.search(rf'<pre data-field="{field}"(?: class="([^"]*)")?>', html)
    assert found is not None, f"no {field} block on the page"
    return found.group(1) or ""


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


def headings(html: str) -> dict[str, str]:
    """What each column of a children log heads itself with, keyed by the column it heads.

    Whitespace collapsed the way a browser collapses it, so the heading a reader sees is what
    the assertion reads: the mark, one space, and the word the label registry gives the column.
    Read by two files — the log on a page, and the log an expansion mounts.
    """
    return {
        column: " ".join(plain(inner).split())
        for column, inner in re.findall(
            r'<th [^>]*data-column="([^"]*)"[^>]*>(.*?)</th>', html, flags=re.DOTALL
        )
    }


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


class Bar(NamedTuple):
    """Where a row's context bar draws each of its bands, as steps of the window.

    Three cumulative edges rather than three widths: the bar is a set of nested prefixes, so
    a band is the ground between two of them and the last one is the whole of the fill. A
    row that draws no band of a kind answers None for it — a session has nothing before it to
    have added to, and only a turn stands its growth against the prompt the session opened on.
    """

    # How full the window was when the node ended, which is where the bar ends.
    fill: int | None
    # Where what the node itself added begins: everything left of it was already there.
    prior: int | None
    # Where the conversation begins — the context the session opened on, before a word of it.
    base: int | None


def bar(page: str, key: str) -> Bar:
    """The steps a row's context bar is drawn at, read back off its classes.

    Shared, because the bar is where the NavTree's numbers and the popover's have to disagree:
    a turn that gave the window back draws no tip and prints a negative delta, and a leaf
    that read one seam alone could not say so.
    """
    classes = inside(page, "data-nav-tree", key, "class")[0].split()
    steps = {name[0]: int(name[1:]) for name in classes if re.fullmatch(r"[fpb]\d+", name)}
    return Bar(steps.get("f"), steps.get("p"), steps.get("b"))


def marked(page: str, key: str, name: str) -> bool:
    """Whether one NavTree row carries a bare class — a mark rather than a step."""
    return name in inside(page, "data-nav-tree", key, "class")[0].split()


class Badge(NamedTuple):
    """One half of a row's cost badge: what it printed, and the step its wash is drawn at."""

    shown: str
    step: str


def washes(html: str, attribute: str, value: str) -> dict[str, str]:
    """Every labelled value inside one element, keyed by field, with the classes it wears.

    A wash is a class per step of a share (`view/nodes.py:meter`), and it rides on the value
    it washes rather than on what holds it: a NavTree row draws two of them and a popover
    four, so the element is never what says which share a step stands for.
    """
    return {
        name: tag.get("class") or ""
        for tag in _element(html, attribute, value).attributes
        if (name := tag.get("data-field")) is not None
    }


def badges(page: str, key: str) -> dict[str, Badge]:
    """A row's cost badge, half by half, keyed by the field each half carries.

    `cost_usd` is what the node's own thread spent and `total_usd` what its whole subtree did,
    so a row printing one number answers with one entry. Each half wears its own step class,
    which is the whole reason this reads the two together: a pair drawn at one depth is a pair
    that took its share against the same number twice.
    """
    shown = fields(page, "data-nav-tree", key)
    return {
        name: Badge(shown[name], step)
        for name, step in washes(page, "data-nav-tree", key).items()
        if name in ("cost_usd", "total_usd")
    }


def step(tokens: int | None, model: str) -> int | None:
    """Which step of the bar a token count lands on, in the model's own window.

    The ladder restated rather than imported: `nodes` owns how a share becomes a class, and an
    oracle reading that would agree with it whatever it said. A fill past the window is held at
    the top — the window a request asked for is not a `message.model` our table can key on, so
    a call above it is drawn full rather than given a scale of its own.
    """
    if tokens is None:
        return None
    # Every model a recorded call names is one the table sizes; only the placeholder is not,
    # and a synthetic reply reports no tokens to draw.
    window = MODELS[model].context_window
    assert window is not None, model
    return min(round(tokens / window * BAR_STEPS), BAR_STEPS)


# One NavTree row that stands for a node, depth beside key. Read as a pair rather than as two
# `values` scans because a cap's tail row carries a depth and no key, so the two lists are
# not the same length whenever a level was cut. Anything but a `>` may sit between the two
# attributes: the formatter owns how a tag is laid out (`mise run format-html`), and a tag
# boundary is the only thing this needs to hold — a tail row's depth cannot pair with the
# next row's key.
_ROW = re.compile(r'data-depth="(\d+)"[^>]*?\sdata-nav-tree="([^"]*)"')


def rows(html: str) -> list[tuple[int, str]]:
    """Every NavTree row that stands for a node: its depth beside its key, in document order."""
    return [(int(depth), key) for depth, key in _ROW.findall(html)]


def under(html: str, key: str) -> list[str]:
    """The rows the NavTree draws directly under one row, as node keys in document order.

    Containment rather than depth: a run renders under its nearest visible ancestor, so a
    closed row anywhere on the page stands runs at whatever depth it sits at plus one. What
    belongs to a row is the run of rows deeper than it, up to the next one at its own depth.
    """
    drawn = rows(html)
    at = next(index for index, (_, drawn_key) in enumerate(drawn) if drawn_key == key)
    depth = drawn[at][0]
    kin: list[str] = []
    for row_depth, row_key in drawn[at + 1 :]:
        if row_depth <= depth:
            break
        if row_depth == depth + 1:
            kin.append(row_key)
    return kin


def kin(html: str) -> list[str]:
    """The children the NavTree opened under the selection, as node keys in document order."""
    return under(html, values(html, "data-selected")[0])


class _Element(HTMLParser):
    """What sits inside the one element carrying `attribute="value"`."""

    def __init__(self, attribute: str, value: str) -> None:
        super().__init__()
        self.attribute = attribute
        self.value = value
        self.depth = 0
        self.field: str | None = None
        # The depth the open field's own element sits at, so a tag nested inside it — a
        # `<strong>` a title rendered, a token the highlighter marked — closes without
        # ending the field and losing everything the element says after it.
        self.field_depth = 0
        self.markup: dict[str, list[str]] = {}
        self.fields: dict[str, str] = {}
        self.marking = False
        self.marks: list[str] = []
        self.attributes: list[dict[str, str | None]] = []
        self.text: list[str] = []

    @override
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
            self.field_depth = self.depth
            self.fields.setdefault(name, "")
            self.markup.setdefault(name, [])
        elif self.field is not None:
            self.markup[self.field].append(self.get_starttag_text() or "")
        elif "icon" in (found.get("class") or "").split():
            self.marking = True

    @override
    def handle_data(self, data: str) -> None:
        if self.depth:
            self.text.append(data)
        if self.field is not None:
            self.fields[self.field] += data
            # Escaped again the way the template escaped it, so a `<` the page printed as
            # characters reads as characters here and a flat value comes back the bytes it
            # was served as. A leaf asking whether an element got in wants the parser's
            # answer rather than the one a raw slice of the document would give.
            self.markup[self.field].append(str(escape(data)))
        elif self.marking:
            self.marks.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.field is not None and self.depth > self.field_depth:
            self.markup[self.field].append(f"</{tag}>")
        if self.depth <= self.field_depth:
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


def marked_up(html: str, attribute: str, value: str, field: str) -> str:
    """The markup inside one labelled field of one element, tags and all.

    What `fields` reads as text — for the one value a page renders rather than prints, a title
    written in markdown. `block` and `prose` do the same for the two fat values, by regex;
    this one is scoped by the element around it, because a title is printed in a dozen places
    on a page and only the pane's heading may carry a link.
    """
    found = _element(html, attribute, value).markup
    assert field in found, f"no {field} field inside {attribute}={value}"
    return "".join(found[field])


def reads(html: str, attribute: str, value: str) -> str:
    """What a browser shows of one element, its whitespace collapsed the way a browser does.

    The one reader here that can see a space between two values: `fields` strips each one and
    `plain` keeps the markup's own indentation, so neither can tell `0 errors` from `0errors`.
    That is the difference a component's own children make, and the spaces written as children
    rather than left between two elements are pinned through this (`view/components/parts.py`).
    """
    return " ".join("".join(_element(html, attribute, value).text).split())


def icons(html: str, attribute: str, value: str) -> list[str]:
    """The bare marks inside the element carrying `attribute="value"`, in document order.

    Read by class rather than by a `data-` key: a mark is not a value the store holds, so it
    carries no `data-field` (`.claude/rules/viewer-ui.md`) — and a key naming it would be
    twenty bytes on every one of a node page's 3,217 NavTree rows.
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

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    @override
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

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()


def wired(html: str, key: str) -> list[tuple[str, dict[str, str]]]:
    """What htmx would do, for every fetching element under a `key` attribute, in page order.

    Inheritance and all: the NavTree writes the swap its rows share on the element it hands back,
    so an assertion on a row's own attributes would read a page that works and one that does
    not the same way. Each pair is the `key` of the row an element sits in and its wiring; a
    row holding two of them — a link and a body toggle — gives two pairs.
    """
    parser = _Wiring(key)
    parser.feed(html)
    return parser.wiring
