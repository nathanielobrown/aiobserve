"""The `/query/{name}` page and the footer that links to it.

Every page says what produced it. This tier is about the other half of that promise: the
citation is a link, and following it lands on the SQL this build ships — bound the way the
page bound it, so a reader who doubts a number can read the statement behind it.

The bindings are never written down here. Each leaf reads the citation line off the page and
checks the link against it, so the two spellings of one fact — the comment a reader copies and
the URL a reader clicks — cannot drift apart.
"""

import re
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.analyze import macros, manifest, queries
from hyphae.view.citation import QUERY_URL
from hyphae.view.text.highlight import Syntax, lit
from tests.conftest import SPINE
from tests.view.conftest import block, classed, fields, inside, plain, values
from tests.view.scenarios import SCENARIOS

# This checkout, for the files the stylesheet gate reads: tests/view/pages/query/… → the root.
REPO = Path(__file__).resolve().parents[4]

# Every page the viewer serves, one URL each, off the route map the route sweep keeps total
# (`tests/view/scenarios.py:SCENARIOS`). Listing them by hand read as coverage and was not: a
# session page opens the turns level, so no page in the list ever ran a query the tools level
# cites. What is left out cites nothing — a fragment carries no footer, and the query page is
# where a citation goes rather than a page that makes one.
CITING = sorted(
    scenario.url
    for route, scenario in SCENARIOS.items()
    if not route.startswith(("/fragment/", QUERY_URL))
)

# The page whose citations name a library macro, off the same map.
TOOL_PAGE = SCENARIOS["/session/{session_id}/thread/{source}/tool/{tool_call_id}"].url


def bound(line: str) -> dict[str, str]:
    """The bindings a citation line quotes, keyed by parameter — `-- queries/x.sql a=1 b=2`."""
    return dict(binding.split("=", 1) for binding in line.split()[2:])


def echoed(html: str) -> dict[str, str]:
    """The bindings a query page shows, keyed by parameter.

    Stripped, because the formatter stands a cell's value on a line of its own: what the page
    shows is the value, and the indentation around it is the markup's own.
    """
    return {key: value.strip() for key, value in re.findall(r'data-binding="(\w+)">([^<]*)<', html)}


def numbered(text: str) -> str:
    """One file behind the line-number gutter the `Read` tool writes down its left."""
    return "".join(f"{no}\t{line}" for no, line in enumerate(text.splitlines(keepends=True), 1))


def commands() -> list[str]:
    """Every shell command this repo runs itself: its task lines and its hook scripts."""
    tasks = tomllib.loads((REPO / "mise.toml").read_text())["tasks"].values()
    runs = [task["run"] for task in tasks if isinstance(task, dict) and "run" in task]
    lines = [line for run in runs for line in ([run] if isinstance(run, str) else run)]
    return lines + [path.read_text() for path in sorted(REPO.glob(".claude/hooks/*.sh"))]


@pytest.mark.parametrize("path", CITING)
def test_every_citation_a_page_carries_links_to_the_query_it_names(
    path: str, client: TestClient
) -> None:
    """Each line in the footer is a link to its own query, carrying that line's bindings.

    The footer's own count is checked against the links so a page that cites five queries and
    shows four is a failure rather than a quieter page.
    """
    page = client.get(path).text
    lines = fields(page, "id", "citation")
    names = inside(page, "id", "citation", "data-field")
    hrefs = inside(page, "id", "citation", "href")
    assert names and names == list(lines)
    assert values(page, "data-citations") == [str(len(names))]
    for name, href in zip(names, hrefs, strict=True):
        target = urlsplit(href)
        # The link goes to the query the line names...
        assert target.path == f"{QUERY_URL}/{name}"
        # ...carrying exactly the bindings the line quotes, and no others.
        asked = parse_qs(target.query, keep_blank_values=True)
        assert {key: found for key, [found] in asked.items()} == bound(lines[name])
        # ...and it answers.
        assert client.get(href).status_code == 200, href


@pytest.mark.parametrize("path", CITING)
def test_a_citation_quotes_every_binding_its_query_takes(path: str, client: TestClient) -> None:
    """A page cites what it ran — all of it, not the bindings that happen to vary by page.

    No `view_` parameter has a default (`view/manifest.py`), so a width left out of a citation
    is a width nobody can recover: the line under the page is the whole record of what the page
    bound, and a reader comparing the line under one page with the line under the next cannot
    tell a query bound differently from a query cited differently.

    Every parameter the manifest declares and not exactly them: a page may bind more than the
    file takes — the sessions list composes its own sort, page and widths around a query that
    declares one (`view/store.py`) — and what it composed is part of what it ran.
    """
    lines = fields(client.get(path).text, "id", "citation")
    assert lines, path
    described = manifest.catalog()
    for name, line in lines.items():
        assert set(described[name].params) <= set(bound(line)), name


def test_the_query_page_serves_the_statement_the_citation_named(client: TestClient) -> None:
    """Following a citation lands on that query's file, whole, under the bindings cited.

    The session node is the page with the most reads behind it, so every one of its links is
    followed rather than the first. The SQL is compared to the file this build ships: a page
    that reformatted or cut a statement would be showing a reader something they cannot run.
    """
    page = client.get(f"/session/{SPINE}").text
    lines = fields(page, "id", "citation")
    for href in inside(page, "id", "citation", "href"):
        name = urlsplit(href).path.removeprefix(f"{QUERY_URL}/")
        shown = client.get(href).text
        assert values(shown, "data-sql") == [name]
        assert plain(block(shown, "sql")) == queries.load(name)
        # And the bindings are echoed as the page ran them, so the statement reads in context.
        assert echoed(shown) == bound(lines[name])


def test_a_query_page_carries_the_definitions_its_statement_runs_under(
    client: TestClient,
) -> None:
    """A statement calling a library macro does not run alone, and the page says so.

    The footer promises a line a shell re-runs, and four viewer queries now call a macro the
    consumer installs first (`analyze/macros.py`). A reader who pastes one of those into a
    bare `duckdb` gets a catalog error and no way to find out why, so the page carries the
    setup above the statement. A query that calls none carries nothing extra.
    """
    for name in manifest.names():
        page = client.get(f"{QUERY_URL}/{name}").text
        calls = any(f"{macro}(" in queries.load(name) for macro in macros.DEFINITIONS)
        assert ('data-field="macros"' in page) == calls, name
        if calls:
            assert plain(block(page, "macros")) == macros.SETUP, name


def test_what_a_query_page_shows_runs_in_a_shell_that_installed_nothing(
    client: TestClient, corpus_db: Path
) -> None:
    """The promise end to end: the page a citation links to, pasted, answers.

    The connection is opened the way a reader's shell opens one — read-only over the store,
    with no macro on it — and what runs is what the page prints, in the order it prints it,
    under the bindings the citing page quoted. A cited value comes back as the text of the
    citation line, which is the one place a reader copies it from.
    """
    lines = fields(client.get(TOOL_PAGE).text, "id", "citation")
    hrefs = inside(client.get(TOOL_PAGE).text, "id", "citation", "href")
    ran = 0
    for name, href in zip(lines, hrefs, strict=True):
        page = client.get(href).text
        if 'data-field="macros"' not in page:
            continue
        shell = duckdb.connect(str(corpus_db), read_only=True)
        try:
            shell.execute(plain(block(page, "macros")))
            shell.execute(
                plain(block(page, "sql")),
                {
                    key: None if text == "NULL" else int(text) if text.isdigit() else text
                    for key, text in bound(lines[name]).items()
                },
            )
        finally:
            shell.close()
        ran += 1
    # The tool page is on the list because it cites queries that need the setup — if it stops
    # doing that this leaf proves nothing, and says so rather than passing empty.
    assert ran, "no citation on the tool page names a macro any more"


def test_a_query_asked_for_with_no_bindings_still_serves(client: TestClient) -> None:
    """The page is a reader's entry point as much as a link target — the URL alone is enough."""
    page = client.get(f"{QUERY_URL}/view_sessions")
    assert page.status_code == 200
    assert plain(block(page.text, "sql")) == queries.load("view_sessions")
    assert echoed(page.text) == {}


@pytest.mark.parametrize(
    "name",
    [
        # A name no build ships...
        "nope",
        # ...the file name rather than the query name, which is the near miss a reader makes...
        "view_sessions.sql",
        # ...and a name shaped like a path out of the query directory. It is a miss before
        # anything is read, which is what keeps the route from being a file server.
        "..%2f..%2fpyproject",
        "..%2f..%2f.env",
    ],
)
def test_only_a_name_the_library_declares_is_served(name: str, client: TestClient) -> None:
    """A name outside the manifest is a 404, and the response repeats nothing back."""
    response = client.get(f"{QUERY_URL}/{name}")
    assert response.status_code == 404
    assert name not in response.text


def test_the_sheet_paints_only_classes_the_highlighter_can_emit(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every class `static/pygments.css` styles is one the lexers actually write.

    A hand-written sheet's failure mode is a rule nobody can see fail — a class from another
    language, or a typo. Each syntax is swept over real material of its own: the JSON is every
    value the viewer marks up, out of the recorded corpus, and the other four are files this
    repo ships — its queries, its docs, its modules, and the commands it runs on itself. The
    docs are swept twice, the second time behind the line numbers a `Read` result carries, so
    what a reader sees of a file is what the sweep saw.
    """
    sheet = client.get("/static/pygments.css")
    assert sheet.status_code == 200
    # Selectors only: a comment names files, and `.py` in one is not a class anyone styles.
    selectors = re.sub(r"/\*.*?\*/", "", sheet.text, flags=re.DOTALL).split("{")
    # Pygments' classes are one to three letters, which leaves this viewer's own class
    # names (`code`, `plain`, `lineno`) out of the comparison.
    painted = {found for rule in selectors for found in re.findall(r"\.([a-z]{1,3}\d?)\b", rule)}
    emitted: set[str] = set()
    for name in manifest.names():
        emitted |= classed(lit(queries.load(name), Syntax.SQL).html)
    for (value,) in store.execute(
        "SELECT input FROM live_tool_calls UNION ALL SELECT result FROM live_tool_calls"
        " UNION ALL SELECT raw FROM raw_records"
    ).fetchall():
        emitted |= classed(lit(value, Syntax.JSON).html)
    for path in sorted(REPO.glob("docs/*.md")):
        prose = path.read_text()
        emitted |= classed(lit(prose, Syntax.MARKDOWN).html)
        emitted |= classed(lit(numbered(prose), Syntax.MARKDOWN).html)
    for path in sorted((REPO / "src").rglob("*.py")):
        emitted |= classed(lit(path.read_text(), Syntax.PYTHON).html)
    for command in commands():
        emitted |= classed(lit(command, Syntax.BASH).html)
    assert painted <= emitted, painted - emitted
