"""Which page to fetch: one node of every kind, and one level of every shape a log has.

Every kind is here on purpose — the pane dispatches on the kind, and a kind missing from a
sweep is a kind whose page nothing renders. Each is read out of the store rather than pinned,
so a re-recorded fixture moves the selection instead of reddening the tier.
"""

import duckdb

from tests.conftest import ANCESTOR, DENSE_TURN, MAIN
from tests.view.conftest import (
    one,
)

# The corpus's densest main-thread turn — 4 api calls under it — so the pane's children log
# has more than one row and the tree has a level under the selection worth rendering.
TURN = f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}"


# One node of every kind a URL can name, read out of the store: the SQL that finds one, and the
# URL template it fills. Every kind is here on purpose — the pane dispatches on the kind, and a
# kind missing from the sweep is a kind whose page nothing renders.
KINDS: dict[str, tuple[str, str]] = {
    "session": ("SELECT id FROM sessions ORDER BY id LIMIT 1", "/session/{0}"),
    "turn": (
        'SELECT session_id, source, id FROM live_turns ORDER BY session_id, source, "index"'
        " LIMIT 1",
        "/session/{0}/thread/{1}/turn/{2}",
    ),
    "run": (
        "SELECT session_id, id FROM live_agent_runs ORDER BY session_id, id LIMIT 1",
        "/session/{0}/run/{1}",
    ),
    "call": (
        'SELECT session_id, source, id FROM live_api_calls ORDER BY session_id, source, "index"'
        " LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
    ),
    "tool": (
        "SELECT session_id, source, id FROM live_tool_calls ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/thread/{1}/tool/{2}",
    ),
    "compaction": (
        "SELECT session_id, source, id FROM live_compactions ORDER BY session_id, source, id"
        " LIMIT 1",
        "/session/{0}/thread/{1}/compaction/{2}",
    ),
    # The two buckets, each found by what puts a row in it: a call answering no turn of its own
    # thread, and a run whose spawning call resolves to nothing at all.
    "unattributed": (
        "SELECT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL ORDER BY c.session_id, c.source LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
    ),
    "unattached": (
        "SELECT a.session_id FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "  AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE c.id IS NULL ORDER BY a.session_id LIMIT 1",
        "/session/{0}/unattached",
    ),
}


def node_url(store: duckdb.DuckDBPyConnection, kind: str) -> str:
    """The URL of one recorded node of `kind`, whichever the store answers with."""
    sql, shape = KINDS[kind]
    return shape.format(*one(store, sql))


# The widest parent the store holds for each shape a children log takes, and the URL of the page
# that logs it. Every shape is here because the log is assembled per shape — a shape missing from
# the sweep is a shape whose page size and whose count above it nothing reads. Widest because a
# page has to be shorter than its level for either to be legible: against a level of one, a page
# that served an extra row and a heading that counted the page would both look right.
LEVELS: dict[str, tuple[str, str, str]] = {
    "session": (
        "SELECT session_id FROM live_turns WHERE source = 'main' GROUP BY 1"
        " ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}",
        "turns",
    ),
    "run": (
        "SELECT a.session_id, a.id FROM live_agent_runs a"
        " JOIN live_turns t ON t.session_id = a.session_id AND t.source = a.id"
        " GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/run/{1}",
        "turns",
    ),
    "turn": (
        "SELECT session_id, source, turn_id FROM live_api_calls WHERE turn_id IS NOT NULL"
        " GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/turn/{2}",
        "calls",
    ),
    "call": (
        "SELECT session_id, source, api_call_id FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        "/session/{0}/thread/{1}/call/{2}",
        "tools",
    ),
    # The two buckets, which page the same way: one out of a query, one out of a list the page
    # already holds.
    "unattributed": (
        "SELECT c.session_id, c.source FROM live_api_calls c"
        " LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source"
        "  AND t.id = c.turn_id"
        " WHERE t.id IS NULL GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1",
        "/session/{0}/thread/{1}/unattributed",
        "calls",
    ),
    "unattached": (
        "SELECT a.session_id FROM live_agent_runs a"
        " LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id"
        "  AND tc.id = a.tool_use_id AND tc.source <> a.id"
        " LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source"
        "  AND c.id = tc.api_call_id"
        " WHERE c.id IS NULL GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1",
        "/session/{0}/unattached",
        "runs",
    ),
}
