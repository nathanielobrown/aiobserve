"""A popover's dollars: what the node's own calls were charged, at the ground they are drawn on.

The other half of `test_numbers.py`, reading the same fetched fragment through the same
helpers. A dollar is a derivation where a count is a column: it is priced from tokens a model
at a time, summed, and then washed against what the whole session spent — so a leaf here is
mostly one derivation put beside another.
"""

import duckdb
from fastapi.testclient import TestClient

from hyphae.view.nodes import Kind, meter
from tests.conftest import (
    DENSE_TOOL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    INVENTED_PROJECT_SESSION,
    MAIN,
    NO_TTL_SPLIT_CALL,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import fields, one, values, washes
from tests.view.test_numbers import CHARGES, amount, charged, misread, popover, popped


def test_every_dollar_in_a_popover_is_washed_at_its_share_of_what_the_session_spent(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The dollars carry the badge's own ground, so a glance reads the same scale in both places.

    `nodes.meter` by name rather than the ladder restated: the wash behind a NavTree row's badge
    and the wash behind these four are one function of one share — what the value is of what the
    whole session spent — and a second implementation here would agree with itself and with
    nothing on the page.
    """
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [SPINE])
    key = f"{Kind.SESSION}:{SPINE}"
    served = popped(client, f"/session/{SPINE}")
    printed = fields(served, "data-popover", key)
    drawn = washes(served, "data-popover", key)
    for name in (*CHARGES, "cost_usd"):
        assert drawn[name].split() == ["badge", meter(amount(printed[name]) / whole)], name


def test_the_row_that_stands_for_a_run_says_where_its_own_cost_came_from(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A ⚒ row's badge is the api call that asked for the run, and its popover says so.

    A tool call is billed nothing of its own (`docs/schema.md`), so the badge on the one row
    that draws one is an attribution rather than a measurement — and an attribution a reader
    cannot see is a number they will read as the tool's own.
    """
    spawn_tool, source = one(
        store,
        "SELECT a.tool_use_id, t.source FROM live_agent_runs a"
        " JOIN live_tool_calls t ON t.session_id = a.session_id AND t.id = a.tool_use_id"
        " WHERE a.session_id = ? AND a.id = ?",
        [SPINE, SPINE_RUN],
    )
    served = popped(client, f"/session/{SPINE}/thread/{source}/tool/{spawn_tool}")
    assert values(served, "data-attribution") == ["spawn_call"]
    # And no other tool row claims one: nothing else on the page is charged a call's cost.
    plain = popped(client, f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}")
    assert values(plain, "data-attribution") == []


def test_a_cache_write_with_no_ttl_on_it_is_charged_at_the_short_rate(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A reply that reported no TTL split still pays for the cache it wrote.

    The columns say "no split reported" with NULLs rather than zeroes, so a group summing them
    would charge that write at nothing (`tests/fixtures/invented/README.md`). The popover
    prices a node one model-group at a time, and the group has to fall back to the whole write
    at the 5-minute rate — the same fallback `extract/pricing.py` applies to a single call.
    """
    where = f"AND id = '{NO_TTL_SPLIT_CALL}'"
    creation, five, hour = one(
        store,
        "SELECT cache_creation_tokens, cache_5m_tokens, cache_1h_tokens FROM live_api_calls"
        f" WHERE session_id = ? {where}",
        [INVENTED_PROJECT_SESSION],
    )
    assert creation and five is None and hour is None, "the corpus's one untimed cache write"
    printed = popover(
        client,
        f"/session/{INVENTED_PROJECT_SESSION}/thread/{MAIN}/call/{NO_TTL_SPLIT_CALL}",
        f"{Kind.CALL}:{NO_TTL_SPLIT_CALL}",
    )
    split, _ = charged(store, INVENTED_PROJECT_SESSION, extra=where)
    assert not misread(printed, split)
    # And the write is a charge a reader can see rather than one that rounded away, which is
    # what makes the line above a reading of the fallback. It is charged on the new-input line,
    # where its tokens are counted, so what shows it was charged at all is that dollar standing
    # above what the call's own input came to.
    assert split.cache_write > 0
    assert amount(printed["cost_new_input"]) > split.input
