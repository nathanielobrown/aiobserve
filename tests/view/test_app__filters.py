"""The filter form above the session list: what each key narrows, and what it refuses.

Every filter the app registers is checked with a sample the store actually holds, so a filter
that stopped matching anything is a red rather than an empty page nobody looks at. A value
reaches DuckDB only as a binding, and an unknown key or an unparseable value is a 400.
"""

import re

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.view.pages.sessions.routes import LIST_KEYS
from hyphae.view.store import FILTERS
from tests.conftest import (
    MYCELIA,
)
from tests.view.conftest import (
    CUT,
    fields,
    one,
    values,
)

# One value per filter, read off the fixture corpus rather than invented, chosen so each
# narrows the 16-session list without emptying it. The leaf below keeps the set honest when
# a filter is added; the values themselves are checked by the narrowing leaf, which fails
# loudly if a fixture change makes one of them match everything or nothing.
SAMPLES: dict[str, str] = {
    # 13 of the 16 fixture sessions ran in the mycelia checkout...
    "project": MYCELIA,
    # ...the corpus starts on 2026-06-30 and ends on 2026-08-06, so a bound inside that
    # window cuts rows off each end...
    "since": "2026-07-01",
    "until": "2026-08-01",
    # ...two sessions ran the grill-me skill...
    "skill": "grill-me",
    # ...and two recorded a failing tool call.
    "errors": "1",
}


def test_every_filter_the_list_offers_has_a_sample_to_check_it_with() -> None:
    """Each filter the list offers is exercised below, so a new one cannot land untested."""
    assert set(SAMPLES) == set(FILTERS)


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_a_filter_narrows_the_list_without_emptying_it(key: str, client: TestClient) -> None:
    """Every filter cuts the list to some of the sessions it held, never to all or none."""
    whole = values(client.get("/sessions").text, "data-session-id")
    narrowed = values(client.get("/sessions", params={key: SAMPLES[key]}).text, "data-session-id")
    # A filter that matched everything would pass a subset check while filtering nothing,
    # and one that matched nothing would pass it vacuously. This is a proper, non-empty cut.
    assert set(narrowed) < set(whole)
    assert narrowed
    # The rows kept their order rather than being re-sorted by the filtering.
    assert narrowed == [row for row in whole if row in set(narrowed)]


def test_a_filter_keeps_exactly_the_sessions_the_store_says_it_should(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A skill filter shows the sessions that ran that skill — the whole set, and no other."""
    ran_it = {
        row[0]
        for row in store.execute(
            "SELECT DISTINCT session_id FROM live_api_calls WHERE attribution_skill = ?",
            [SAMPLES["skill"]],
        ).fetchall()
    }
    shown = values(
        client.get("/sessions", params={"skill": SAMPLES["skill"]}).text, "data-session-id"
    )
    assert set(shown) == ran_it
    # Every row shown says the skill it was filtered by, so the page shows its own evidence.
    for session_id in shown:
        page = client.get("/sessions", params={"skill": SAMPLES["skill"]}).text
        assert SAMPLES["skill"] in fields(page, "data-session-id", session_id)["skills"]


# The filters whose predicates a value could break out of, one per shape: `skill` binds its
# parameter once, `project` binds the same one twice and concatenates it, which is the place
# a value spliced as text would have two chances to become SQL.
@pytest.mark.parametrize("key", ["skill", "project"])
def test_a_filter_value_reaches_duckdb_only_as_a_binding(
    key: str, client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A filter value that is SQL rather than a name matches nothing and runs nothing."""
    before = one(store, "SELECT count(*) FROM sessions")[0]
    response = client.get("/sessions", params={key: "'; DROP TABLE sessions; --"})
    # A value that reached SQL as text would either error or execute; bound, it is a name
    # no session carries...
    assert response.status_code == 200
    assert values(response.text, "data-session-id") == []
    # ...and the table it named is still there, with every row it had.
    assert one(store, "SELECT count(*) FROM sessions")[0] == before


@pytest.mark.parametrize(
    ("parameters", "says"),
    [
        # A key the list does not offer, however plausible, is told the keys it does...
        ({"filter": "grill-me"}, "skill"),
        ({"Skill": "grill-me"}, "skill"),
        # ...and a known key whose value is not the type its predicate binds is told which.
        ({"since": "last tuesday"}, "since takes date values"),
        ({"errors": "many"}, "errors takes integer values"),
    ],
)
def test_an_unknown_filter_key_or_unparseable_value_is_refused(
    parameters: dict[str, str], says: str, client: TestClient
) -> None:
    """The list reads a closed set of query keys, each at one type; anything else is a 400."""
    response = client.get("/sessions", params=parameters)
    assert response.status_code == 400
    # The refusal says what would have worked, and never echoes what was asked for — a page
    # that reflected the value back would be the one place unescaped request text could land.
    assert says in response.text
    for value in parameters.values():
        assert value not in response.text


def test_a_form_submitted_with_every_key_filled_in_is_still_a_narrowing(
    client: TestClient,
) -> None:
    """Every key the list reads, sent at once, is a legal request rather than a 400.

    The filter form posts all five filters and rides the sort, the page and the size, so a
    reader who types into every box sends the whole of `LIST_KEYS` — the boundary the
    membership test sits on. The samples are the same recorded values the filter leaves use,
    so the request that comes back is a real cut of the corpus and not an empty page.
    """
    filled = dict(SAMPLES) | {"sort": "cost_usd", "direction": "asc", "page": "1", "size": "5"}
    assert filled.keys() == LIST_KEYS, "the list reads a key this leaf does not fill in"
    response = client.get("/sessions", params=filled)
    assert response.status_code == 200
    # It narrowed rather than merely surviving: the corpus is wider than what came back.
    shown = values(response.text, "data-session-id")
    assert shown
    assert set(shown) < set(values(client.get("/sessions").text, "data-session-id"))


def test_a_filter_rides_the_links_and_the_citation(client: TestClient) -> None:
    """A filter survives re-sorting and paging, and the footer says the list was filtered."""
    page = client.get(
        "/sessions", params={"skill": SAMPLES["skill"], "sort": "cost_usd", "size": 1}
    ).text
    # Every heading link and every pager link carries the filter, so changing the order or
    # turning the page does not quietly widen the list back to the corpus...
    links = re.findall(r'href="(/sessions\?[^"]*)"', page)
    assert links
    for link in links:
        assert "skill=grill-me" in link
    # The list lives at `/sessions` whole — its form, its clear link and every link it mints
    # go there. A `/?sort=` survivor would land on the projects page, which answers a
    # different question and would drop the filter on the way.
    assert re.findall(r'href="(/\?[^"]*)"', page) == []
    assert '<form id="filters" method="get" action="/sessions">' in page
    assert '<a href="/sessions">clear</a>' in page
    # ...and the citation carries it too, after the paging, so the line reproduces the rows.
    assert fields(page, "id", "citation")["view_sessions"] == (
        "-- queries/view_sessions.sql sort=cost_usd direction=desc limit=1 offset=0"
        f" {CUT} skill=grill-me"
    )
