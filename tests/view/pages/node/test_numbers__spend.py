"""A popover's dollars: what the node's own calls were charged, and what the runs under it spent.

The other half of `test_numbers.py`, reading the same fetched fragment through the same
helpers. A dollar is priced from tokens and then summed twice — once over the node's own
calls, once over every thread hanging below it — and that second sum is drawn a second time by
the NavTree's dual badge, so most of what these leaves do is put two derivations beside each
other. Where nothing hangs below a node the two breakout lines are not drawn at all, and the
absence is pinned here as well.
"""

import duckdb
from fastapi.testclient import TestClient

from hyphae.view.nodes import Kind, meter
from hyphae.view.text.format import ABSENT
from tests.conftest import (
    DENSE_TOOL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    INVENTED_PROJECT_SESSION,
    MAIN,
    MODEL_ONLY,
    NO_TTL_SPLIT_CALL,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import SPAWNS, badges, fields, money, one, pages, values, washes
from tests.view.pages.node.test_numbers import CHARGES, amount, charged, misread, popover, popped

# The fields of the breakout, which only a node with agent runs below it draws: the two lines
# and the share printed on the first of them.
BREAKOUT = frozenset({"subagent_share", "cost_subagents", "cost_total"})


def test_a_nodes_total_spend_is_the_number_its_own_navtree_badge_already_draws(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """One rollup, two queries: the badge sums it in Python and the popover sums it in SQL.

    A dual badge is summed over `view_runs`'s rows as a page is built (`view/nodes.py:ledger`);
    the popover's total spend is summed inside `view_numbers` when a reader points at the row.
    Both answer what this node and everything under it cost, and nothing but a comparison
    catches the day they stop agreeing.

    On `spine`, where both edges the rollup walks exist: the session gathers every run, and the
    turn that asked for the outer run gathers it and the run that one asked for in turn. Which
    turn that is comes from the shared spawn join rather than from a pinned id.
    """
    hung = {
        turn_id
        for _, source, turn_id, _ in store.execute(SPAWNS, [SPINE]).fetchall()
        if source == MAIN and turn_id is not None
    }
    assert hung, "no run of the spine hangs on a turn of its main thread"
    page = client.get(f"/session/{SPINE}").text
    read = {f"{Kind.SESSION}:{SPINE}": f"/session/{SPINE}"} | {
        f"{Kind.TURN}:{turn_id}": f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}"
        for turn_id in hung
    }
    for key, path in read.items():
        printed = popover(client, path, key)
        # Compared as the badge prints it: the popover carries four places and the badge two,
        # and the badge's is the figure a reader actually reads off the row.
        assert money(amount(printed["cost_total"])) == badges(page, key)["total_usd"].shown, key


def test_a_node_with_no_runs_under_it_breaks_nothing_out(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The breakout is drawn where there is something to break out, and nowhere else.

    A subagent line of nothing and a total repeating the figure above it are two ways of saying
    what the node already said — and a reader who meets them on every row stops reading them.
    Three shapes of nothing: a run that ended the chain, an api call that asked for no run, and
    a turn of a session that spent nothing at all.
    """
    # An api call of the spine that spawned no run: the tool calls under it asked for none.
    quiet_call, quiet_source = one(
        store,
        "SELECT c.id, c.source FROM live_api_calls c"
        " WHERE c.session_id = ? AND c.id NOT IN ("
        "   SELECT tc.api_call_id FROM live_tool_calls tc"
        "   JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id"
        "   WHERE tc.session_id = ?)"
        ' ORDER BY c.source, c."index" LIMIT 1',
        [SPINE, SPINE],
    )
    quiet_turn = one(store, "SELECT id FROM live_turns WHERE session_id = ? LIMIT 1", [MODEL_ONLY])[
        0
    ]
    for path, key in (
        (f"/session/{SPINE}/run/{SPINE_LEAF}", f"{Kind.RUN}:{SPINE_LEAF}"),
        (
            f"/session/{SPINE}/thread/{quiet_source}/call/{quiet_call}",
            f"{Kind.CALL}:{quiet_call}",
        ),
        (
            f"/session/{MODEL_ONLY}/thread/{MAIN}/turn/{quiet_turn}",
            f"{Kind.TURN}:{quiet_turn}",
        ),
    ):
        # By the names the fields carry rather than by a string search: a template that always
        # rendered the lines and left them empty would pass any reading of the text.
        assert not BREAKOUT & popover(client, path, key).keys(), path
    # And the absence is worth something only because the same corpus draws the lines: the
    # session those three nodes sit in has runs under it, and its own popover carries all three.
    assert popover(client, f"/session/{SPINE}", f"{Kind.SESSION}:{SPINE}").keys() >= BREAKOUT


def test_own_and_subagent_spend_come_to_the_total_wherever_the_breakout_is_drawn(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The one arithmetic a reader does in their head, over every node that offers it.

    Two of the three numbers are read out of different sets of calls — the node's own thread,
    and every thread hanging below it — so a run counted in neither, or in both, shows up here
    and nowhere else. To the cent, which is the precision the badge beside them prints at.

    Swept over every popover route the corpus reaches, narrowed to the sessions that recorded
    an agent run: a session with none has nothing to break out, and a sweep over it would
    measure the skip.
    """
    spawned = {
        session_id
        for (session_id,) in store.execute(
            "SELECT DISTINCT session_id FROM live_agent_runs"
        ).fetchall()
    }
    drawn = 0
    for path, key in _numbered(pages(store)):
        if path.split("/")[2] not in spawned:
            continue
        printed = popover(client, path, key)
        if not BREAKOUT & printed.keys():
            continue
        drawn += 1
        # A node whose own calls our price table could not price still gathers what the runs
        # below it spent: its own half is the dash, and nothing, and the total is theirs.
        own = 0.0 if printed["cost_usd"] == ABSENT else amount(printed["cost_usd"])
        assert round(own + amount(printed["cost_subagents"]), 2) == round(
            amount(printed["cost_total"]), 2
        ), path
    assert drawn, "no node of the corpus draws the breakout"


def _numbered(urls: list[str]) -> list[tuple[str, str]]:
    """Every node URL whose popover `view_numbers` answers, beside the key it answers under.

    A tool call is left out because a fragment of its own answers for it, and a compaction
    because no row fetches numbers for one. Both are the routes `fragments.py` binds, read off
    the same list of pages the rest of the sweeps walk (`tests/view/conftest.py:pages`).
    """
    read: list[tuple[str, str]] = []
    for url in urls:
        parts = url.strip("/").split("/")
        match parts:
            case ["session", session_id]:
                read.append((url, f"{Kind.SESSION}:{session_id}"))
            case ["session", _, "run", run_id]:
                read.append((url, f"{Kind.RUN}:{run_id}"))
            case ["session", _, "thread", _, ("turn" | "call") as kind, node_id]:
                read.append((url, f"{kind}:{node_id}"))
    return read


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
    # The two breakout dollars beside the four: `spine` ran subagents, so its session popover
    # draws them, and a line washed at a share of anything narrower would deepen as a reader
    # walked down the tree.
    for name in (*CHARGES, "cost_usd", "cost_subagents", "cost_total"):
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
    assert amount(printed["new_input_usd"]) > split.input
