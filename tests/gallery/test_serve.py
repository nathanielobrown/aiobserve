"""What the gallery serves, and what it cannot be pointed at.

`mise run gallery` builds a store from the redacted fixtures and serves every scenario in
`tests/view/scenarios.py` as a page. The leaves here read the served index the way the viewer
tier reads any page, and one of them reads the entry point's own signature: privacy is
structural here, so a parameter that took a store path would be the whole bug.
"""

import inspect
import re
import tomllib
from collections.abc import Iterator
from html import unescape
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view.app import PORT
from hyphae.view.dev import RELOAD_URL
from tests.conftest import build_enriched_store
from tests.gallery import serve
from tests.view.scenarios import SCENARIOS
from tests.view.test_dev import TAG, declared

REPO = Path(__file__).resolve().parents[2]

# One row of the index: the route it stands for, and where clicking it goes. Read as a pair
# because the obligation is that the two agree — a link set alone passes with every row
# pointing at the same page.
LINK = re.compile(r'<a data-scenario="([^"]+)" href="([^"]+)"')

# The three tables an enrichment pass writes, beside the one the extractor does. Counted on
# both sides of the builder below, because "the same store" is a claim about rows.
COUNTED = ("sessions", "session_enrichments", "agent_run_enrichments", "turn_enrichments")


@pytest.fixture(scope="module")
def gallery(enriched_db: Path) -> Iterator[TestClient]:
    """The gallery app over the store the fixture holds — the store `main` builds itself."""
    with TestClient(serve.gallery(enriched_db)) as client:
        yield client


def test_the_index_offers_one_link_per_scenario_and_nothing_else(gallery: TestClient) -> None:
    """The index is the tier's scenario list rendered, not a second registry beside it.

    Route name and URL, paired and in registry order: an entry that lost its link, gained one
    the sweep does not cover, or points somewhere other than where it is named, fails here.
    """
    # `unescape` because a URL with two query knobs carries `&amp;` in an attribute.
    linked = [(route, unescape(url)) for route, url in LINK.findall(gallery.get(serve.INDEX).text)]
    assert linked == [(route, scenario.url) for route, scenario in SCENARIOS.items()]


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
