"""Every Detail the registry declares, read against the app and against the whole corpus.

`view/detail.py:DETAILS` is where a previewable value is declared once — its name, its two
queries, its route and how it was written. These leaves sweep that registry rather than a list
kept beside it, so a Detail added anywhere is covered the moment it is declared: the routes
exist, the pane previews under the name, and the URL the pane mints serves the value the pane
previewed. What a Detail *looks* like on either surface is next door in `test_node__details`.
"""

from collections.abc import Callable

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hyphae.view.detail import DETAILS, Spec, Written
from hyphae.view.text.format import ELLIPSIS
from hyphae.view.text.labels import LABELS
from tests.view.conftest import block, classed, fields, pages, values, walled
from tests.view.scenarios import SCENARIOS, Group, path_pattern

# The one `/fragment/` route that serves a whole value and is not a Detail: a record arrives
# with a header line of its own and no pane previews a head of it (`routes/details.py`).
RECORD_ROUTE = "/fragment/record/session/{session_id}/thread/{source}/line/{line_no}"


@pytest.fixture(scope="module")
def rendered(enriched_client: TestClient) -> Callable[[str], str]:
    """One node page of the described corpus, served once however many specs read it.

    The sweep below reads every node of a kind, and the three tool Details read the same
    forty-nine pages between them: served per spec, that would be three passes over the corpus
    for one pass worth of bytes.
    """
    served: dict[str, str] = {}

    def read(url: str) -> str:
        if url not in served:
            response = enriched_client.get(url)
            assert response.status_code == 200, (url, response.status_code)
            served[url] = response.text
        return served[url]

    return read


def test_the_registry_declares_every_value_the_viewer_previews_and_fetches(
    client: TestClient,
) -> None:
    """`DETAILS` is the whole population of previewable values, read against the app itself.

    A Detail used to be declared in six places that agreed only by string equality — the
    pane's call, the fetch route, the two header columns, a `store.Value` and a label key —
    and a hand-kept list in a test was a seventh. This is what replaces them all: every
    spec's route is a route the app answers, and the specs plus the raw record are exactly
    the value and enrichment fetches the scenario corpus pins.

    Both halves are needed. The route set alone would pass a spec that declared a URL nobody
    can reach; the scenario set alone would pass a route no spec declares. Together with
    `test_bounds.py:test_every_route_the_viewer_exposes_is_in_the_payload_sweep`, which
    equates the app's routes to `SCENARIOS`, no public URL can move without one going red.
    """
    routes = {spec.route for spec in DETAILS}
    exposed = {route.path for route in client.app.routes if isinstance(route, APIRoute)}  # pyrefly: ignore
    assert routes <= exposed
    assert routes | {RECORD_ROUTE} == {
        path
        for path, scenario in SCENARIOS.items()
        if scenario.group in (Group.VALUES, Group.ENRICHMENT)
    }


@pytest.mark.parametrize("spec", DETAILS, ids=lambda spec: f"{spec.whole.name}-{spec.name}")
def test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url(
    spec: Spec,
    enriched_client: TestClient,
    enriched_store: duckdb.DuckDBPyConnection,
    rendered: Callable[[str], str],
) -> None:
    """Every Detail the registry declares previews on its node's pane and fetches whole.

    One route per value rather than one per row: a tool call's input and its result are two
    values a reader opens apart, and a route that served the row whole would send the other
    one every time. The datum per spec is the URL the scenario corpus pins for its route,
    which is a URL known to work against this store.

    The node whose pane previews the value is that same URL with the `/fragment/<name>` prefix
    taken off. That is a claim as well as a convenience: a fetch lives under the node it
    belongs to, so a route minted anywhere else would find no pane here.

    What the fetch answers is read against the pane and never against `spec.whole`, which is
    the query under test — a spec pointing at a sibling's query would otherwise be its own
    oracle and pass. The pane's head comes off the header query instead, so the two agree only
    where the spec named the query answering the column its own header previews.

    One node cannot say that, because a redacted corpus prints `[redacted]` under a call's text
    and under its thinking alike. So the reading is swept over every node of the spec's kind the
    store holds: the fetch answers nothing exactly where the pane previews nothing, and starts
    with the head the pane showed everywhere else. A stolen query surfaces as a value served
    under a name whose pane had none, which is what tells `text` from `thinking` on this corpus
    — and `prompt` from `brief` — where their characters cannot.
    """
    # The `/fragment/<name>` head of the route, which is what tells a value's URL from the
    # node's own: taking it off one gives the other, on the template and on the URL alike.
    fragment = spec.route[: spec.route.index("/session/")]
    url = SCENARIOS[spec.route].url
    node = url.removeprefix(fragment)
    # Which element carries the value is the one thing `Written.LINE` decides here: a line a
    # pass wrote is a span inside the enrichment block, and every other Detail is a block of
    # its own. Both surfaces of one Detail agree on it, which is why one name reads both.
    marker = "data-enrichment-line" if spec.written is Written.LINE else "data-detail"
    # The pane previews it under the spec's own name...
    page = enriched_client.get(node)
    assert page.status_code == 200, node
    assert fields(page.text, marker, spec.name)[spec.name], spec.name
    # ...and the URL it minted answers, filed under that same name: what a value is styled as —
    # the rail that tells an ask from an answer — hangs off it, and a fragment that dropped the
    # name would open unstyled.
    served = enriched_client.get(url)
    assert served.status_code == 200, url
    assert values(served.text, marker) == [spec.name], url
    # And a value that is not prose comes back marked up the way the preview was. The fragment
    # files it under `value` and the pane under the column's own name, so the two `<pre>`
    # classes are what compare — one rule in `view/detail.py:syntax_of` decides both, and this
    # is the reading that would see them part again.
    if spec.written in (Written.JSON, Written.NAMED_FILE, Written.BASH):
        assert walled(served.text, "value") == walled(page.text, spec.name), url
        assert classed(block(served.text, "value")) == classed(block(page.text, spec.name)), url
    # Now the same reading over the whole corpus, one node of this kind at a time — the URLs
    # the pane would mint, matched off the route rather than listed beside the registry.
    matches = path_pattern(spec.route.removeprefix(fragment)).fullmatch
    previewed = 0
    for candidate in pages(enriched_store):
        if not matches(candidate):
            continue
        shown = fields(rendered(candidate), marker, spec.name).get(spec.name)
        answer = enriched_client.get(f"{fragment}{candidate}")
        assert answer.status_code in (200, 404), (candidate, answer.status_code)
        if not shown:
            # A row can exist with nothing under it, and the pane draws no block for one. The
            # fetch says that same nothing two ways — a 404 for the NULL, an empty value for
            # the column a transcript left blank — and either is what a stolen query is not.
            assert not _served(answer.text, spec), candidate
            continue
        previewed += 1
        assert answer.status_code == 200, candidate
        # Every character the pane showed, in the order it showed them. A head the pane had to
        # shorten carries one trailing ellipsis, the one character of a preview that is ours
        # rather than the store's.
        assert _served(answer.text, spec).startswith(shown.removesuffix(ELLIPSIS)), candidate
    # And the sweep read something: a spec whose column this corpus holds nowhere would
    # otherwise pass on absences alone.
    assert previewed, spec.name


def _served(html: str, spec: Spec) -> str:
    """The value one fragment came back with, or nothing where it came back with none.

    Prose and enrichment lines arrive under the Detail's own name and a highlighted payload
    under `value` (`pages/node/markup/values.py`). Empty string for the 404, which carries no
    such element at all — the same nothing an empty value reads as, which is what lets one
    reading cover a fetch that answers nothing either way.
    """
    marker = "data-enrichment-line" if spec.written is Written.LINE else "data-detail"
    marked = spec.written in (Written.JSON, Written.NAMED_FILE, Written.BASH)
    return fields(html, marker, spec.name).get("value" if marked else spec.name, "")


def test_a_brief_is_labelled_as_an_ask_and_not_as_a_description_of_the_run() -> None:
    """A run's brief is what it was asked to do, not what a pass said it did.

    Two fat values sit in a run's pane under words a reader could take for the same thing, and
    only one of them belongs to the enrichment pass. The registry files them apart by name;
    this is the half saying the names still reach a reader as different words.
    """
    assert (LABELS["brief"], LABELS["description"]) == ("Task brief", "Description")
