"""What the gallery serves, and what it cannot be pointed at.

`mise run gallery` builds a store from the redacted fixtures and serves every scenario in
`tests/view/scenarios.py` as a page. The leaves here read the served index the way the viewer
tier reads any page, and one of them reads the entry point's own signature: privacy is
structural here, so a parameter that took a store path would be the whole bug.
"""

import datetime as dt
import inspect
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from html import unescape
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view import format as fmt
from hyphae.view.app import PORT, build_app
from hyphae.view.dev import RELOAD_URL
from tests.conftest import build_enriched_store
from tests.gallery import serve
from tests.view.scenarios import SCENARIOS, Group
from tests.view.test_dev import TAG, declared

REPO = Path(__file__).resolve().parents[2]

# One row of the index: the route it stands for, where clicking it goes, the words it is named
# by, and the route printed beside the name. Read whole because the obligation is that they
# agree — a link set alone passes with every row pointing at the same page.
LINK = re.compile(
    r'<a data-scenario="([^"]+)"\s+href="([^"]+)">\s*([^<]+?)\s*</a>\s*<code>\s*([^<]+?)\s*</code>'
)

# And the heading a run of rows sits under. Split on rather than searched for: a row's group is
# the last heading above it, which is all the page says about where the row belongs.
HEADING = re.compile(r"<h2[^>]*>\s*([^<]+?)\s*</h2>")

# The three tables an enrichment pass writes, beside the one the extractor does. Counted on
# both sides of the builder below, because "the same store" is a claim about rows.
COUNTED = ("sessions", "session_enrichments", "agent_run_enrichments", "turn_enrichments")

# How long ago something happened, as a page prints it. The one cell on any page whose text is
# a reading of the clock rather than of the store — `sessions.html` and `projects.html` are the
# only templates that reach the `ago` filter.
AGO = re.compile(r'<span data-field="ago"[^>]*>\s*([^<]*?)\s*</span>')

# The pages a frozen clock has to hold still: the two that print ages, and a node page, which
# prints none and therefore may not gain one unnoticed.
CLOCKED = ("/", "/sessions", "/session/{session_id}")


@pytest.fixture(scope="module")
def gallery(enriched_db: Path) -> Iterator[TestClient]:
    """The gallery app over the store the fixture holds — the store `main` builds itself.

    Building one freezes the clock of whatever process does it, so this puts `fmt.utcnow` back:
    the freeze belongs to the gallery, and the tiers that run after this one read a real one.
    """
    real = fmt.utcnow
    try:
        with TestClient(serve.gallery(enriched_db)) as client:
            yield client
    finally:
        fmt.utcnow = real


def listed(html: str) -> list[tuple[str, str, str, str, str]]:
    """Every row of the served index in page order: heading, route, URL, title, printed route.

    `re.split` on a pattern with one group yields the text before the first heading, then the
    heading and the rows under it in pairs — so the pairing that makes a group readable is the
    splitting, not a second walk.
    """
    parts = HEADING.split(html)
    return [
        # Read back as text: a URL with two query knobs carries `&amp;` in an attribute, and a
        # title with an apostrophe in it carries `&#39;` on the page.
        (heading, unescape(route), unescape(url), unescape(title), unescape(printed))
        for heading, rows in zip(parts[1::2], parts[2::2], strict=True)
        for route, url, title, printed in LINK.findall(rows)
    ]


def test_the_index_offers_one_link_per_scenario_and_nothing_else(gallery: TestClient) -> None:
    """The index is the tier's scenario list rendered, not a second registry beside it.

    Every row whole, under its group's heading and in registry order: an entry that lost its
    link, gained one the sweep does not cover, is named what another row is named, or points
    somewhere other than where it says, fails here. The headings come in `Group` order, and a
    row prints the route it stands for beside the title a reader picks it by.
    """
    assert listed(gallery.get(serve.INDEX).text) == [
        (group.value, route, scenario.url, scenario.title, route)
        for group in Group
        for route, scenario in SCENARIOS.items()
        if scenario.group is group
    ]


def test_the_gallery_cannot_be_pointed_at_a_store() -> None:
    """The one thing that reaches the gallery from outside is a port number.

    Session data is private, and the gallery's whole claim to serving a store in a browser is
    that it can only serve the one it builds from the redacted fixtures. So every door is
    named here: the entry point takes no parameter, the command line parses to a port and
    nothing else — `vars` is the whole namespace, so a second option would fail here whatever
    it was called — and the environment is not read at all.
    """
    assert inspect.signature(serve.main).parameters == {}
    assert vars(serve.parser().parse_args([])) == {"port": serve.PORT}
    assert serve.parser().parse_args(["--port", "9001"]).port == 9001
    # A store path has no way in, however it is spelled: argparse exits on an option it does
    # not declare, and the gallery declares one.
    with pytest.raises(SystemExit):
        serve.parser().parse_args(["--store", "/tmp/traces.duckdb"])
    source = Path(inspect.getfile(serve)).read_text()
    # An env var is the quiet way back in: it takes no signature and no flag, so a read of one
    # would look like configuration rather than a door onto the canonical store.
    assert "environ" not in source
    assert "getenv" not in source


def test_the_gallery_is_a_dev_viewer(gallery: TestClient) -> None:
    """It is the viewer under `--dev`: the reload client on the page, the stream declared.

    What the stream then does is pinned in `tests/view/test_dev.py` over the same router; the
    obligation here is only that the gallery is the app that carries it.
    """
    assert gallery.get(serve.INDEX).content.count(TAG) == 1
    assert gallery.get("/").content.count(TAG) == 1
    assert RELOAD_URL in declared(gallery)


def test_the_index_does_not_displace_a_scenario(gallery: TestClient) -> None:
    """`/` is the projects page and a scenario of its own, so the index lives beside it."""
    assert serve.INDEX not in {scenario.url for scenario in SCENARIOS.values()}
    assert declared(gallery) == set(SCENARIOS) | {RELOAD_URL, serve.INDEX}


def test_the_store_the_gallery_builds_holds_what_the_fixture_store_holds(
    tmp_path: Path, enriched_db: Path
) -> None:
    """One builder, so the pages a reader opens are the pages the tier asserts on.

    The gallery has no corpus to copy, so it takes the arm of `build_enriched_store` the
    fixtures never take. Counted rather than compared byte for byte: two builds of the same
    corpus differ in a DuckDB file's own bookkeeping.
    """
    built = tmp_path / "traces.duckdb"
    build_enriched_store(built, corpus=None)
    for table in COUNTED:
        with duckdb.connect(str(built), read_only=True) as gallery_store:
            counted = gallery_store.execute(f"SELECT count(*) FROM {table}").fetchone()
        with duckdb.connect(str(enriched_db), read_only=True) as fixture_store:
            expected = fixture_store.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert counted == expected, table


def test_a_page_reads_the_same_whenever_the_gallery_is_opened(
    enriched_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page opened today and the same page opened next week print the same ages.

    What the browser tier archives is diffed against yesterday's baseline, so anything on the
    page that moves by itself is a change report nobody asked for. The wall clock is moved a
    week between two openings of the gallery, and every page it serves has to hold still.
    """

    def opened(at: dt.datetime) -> list[str]:
        # If the machine's clock says `at` when the gallery is built...
        monkeypatch.setattr(fmt, "utcnow", lambda: at)
        with TestClient(serve.gallery(enriched_db)) as client:
            return [client.get(SCENARIOS[route].url).text for route in CLOCKED]

    # ...then the pages print real ages. Not every row: the one session recorded with no
    # timestamp prints a dash, and a page of nothing but dashes would compare equal to any
    # other page of them...
    today = opened(dt.datetime.now(dt.UTC))
    ages = {age for page in today for age in AGO.findall(page)}
    assert ages - {fmt.ABSENT}, "no page in CLOCKED prints an age, so this compares nothing"
    # ...and a week later the same pages come back byte for byte, ages included.
    assert opened(dt.datetime.now(dt.UTC) + dt.timedelta(days=7)) == today


def test_the_clock_the_gallery_freezes_to_is_read_out_of_the_corpus(
    enriched_db: Path, tmp_path: Path
) -> None:
    """The instant the pages are read against is the corpus's own present, not a date typed here.

    The newest session in the store is what "now" means to a gallery page, so the fixture the
    ages are measured from is the fixture on the page. A corpus recorded next month moves it
    with no edit to the gallery.
    """
    with duckdb.connect(str(enriched_db), read_only=True) as store:
        latest = store.execute("SELECT max(ended_at) FROM sessions").fetchone()
    assert latest is not None
    assert serve.corpus_now(enriched_db) == latest[0]
    # A store whose sessions all ran a month later carries the gallery's clock a month forward.
    newer = tmp_path / "traces.duckdb"
    shutil.copy(enriched_db, newer)
    with duckdb.connect(str(newer)) as store:
        store.execute("UPDATE sessions SET ended_at = ended_at + INTERVAL 30 DAY")
    assert serve.corpus_now(newer) == latest[0] + dt.timedelta(days=30)


def test_the_viewer_the_package_ships_keeps_its_own_clock(
    enriched_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Freezing the gallery's clock does not freeze the viewer's.

    The freeze is a `setattr` on a module the package owns, so an import that carried it would
    hand every `hp view` the fixtures' idea of the present. Importing the gallery does nothing
    — the freeze happens when a gallery app is built — and the app `build_app` returns still
    reads whatever clock the request finds.
    """
    probe = (
        "import hyphae.view.format as fmt;"
        " own = fmt.utcnow;"
        " import tests.gallery.serve;"
        " print(fmt.utcnow is own)"
    )
    imported = subprocess.run(
        ["uv", "run", "python", "-c", probe],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert imported.stdout.strip() == "True"
    # And the shipped app measures an age against the clock the request reads, so moving the
    # clock moves the page — which a baked-in constant anywhere under `build_app` would not.
    with TestClient(build_app(enriched_db)) as client:

        def ages(at: dt.datetime) -> list[str]:
            monkeypatch.setattr(fmt, "utcnow", lambda: at)
            return AGO.findall(client.get("/sessions").text)

        latest = serve.corpus_now(enriched_db)
        assert ages(latest) != ages(latest + dt.timedelta(days=7))


def test_the_gallery_has_a_task_and_a_port_the_viewer_does_not(gallery: TestClient) -> None:
    """`mise run gallery` is how it is started, on a port a running viewer cannot be on."""
    assert serve.PORT != PORT
    task = tomllib.loads((REPO / "mise.toml").read_text())["tasks"]["gallery"]
    # The default port is the process's, not the task's: the task says how to start it and the
    # process says where it listens when nobody says otherwise.
    assert str(serve.PORT) not in task["run"]
    # `--port` reaches the process only if the task both declares it and passes it on. mise
    # mangles a flag its spec does not declare, and a declared flag the body never
    # interpolates is dropped silently — so both halves are read here.
    assert '"--port <port>"' in task["usage"]
    assert "usage_port" in task["run"]
