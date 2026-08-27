"""One real URL per route the viewer exposes — the scenario list the tier sweeps.

Its own module rather than a section of `conftest.py` because two kinds of reader want it:
the viewer tier, which parametrizes over it and checks it against the routes the app
declares, and the gallery, which serves each entry as a page you can open. A registry a
`conftest` owns is a registry only pytest can import.
"""

from aiobserve.view.citation import QUERY_URL
from tests.conftest import (
    ANCESTOR,
    BASH_TOOL,
    COMPACTED,
    COMPACTED_BOUNDARY,
    CONFIG_ONLY,
    DENSE_CALL,
    DENSE_TOOL,
    DENSE_TURN,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    OFFLOAD_FILE,
    RESUME,
    SLASH_TURN,
    SPINE,
    SPINE_RUN,
)

# One item of each level that `enriched_db` describes *and* wrote a friction line for. A pass
# writes friction where it saw some, and the fixture's stands in for that by describing every
# fourth item (`tests/conftest.py:planted_enrichment`) — so the two fetches behind a described
# pane need an item that has both, or the sweep below reads a 404 as a route that broke. Found
# by asking the described store for a row whose `friction` is not null.
DESCRIBED_SESSION = ANCESTOR
DESCRIBED_RUN = "af6473ae437c9608d"
DESCRIBED_TURN = "5b848af7-f86e-4950-b474-cd98125fad24"

# One real URL per route the app exposes, keyed by the route's own path template. Two tiers
# read it whole — the payload sweep weighs every URL, and the citation leaves read the footer
# of every page among them — and one leaf reads it as a set against the routes the app
# declares, so a route added with no entry here fails rather than going unread.
ROUTES: dict[str, str] = {
    "/": "/",
    "/sessions": "/sessions",
    # The eight node kinds, each at a session that records the shape: a compaction at the
    # session with two of them, a bucket at each of the two sessions that has one.
    "/session/{session_id}": f"/session/{SPINE}",
    "/session/{session_id}/thread/{source}/turn/{turn_id}": (
        f"/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}"
    ),
    "/session/{session_id}/run/{run_id}": f"/session/{SPINE}/run/{SPINE_RUN}",
    "/session/{session_id}/thread/{source}/call/{api_call_id}": (
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
    ),
    "/session/{session_id}/thread/{source}/tool/{tool_call_id}": (
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
    ),
    "/session/{session_id}/thread/{source}/compaction/{compaction_id}": (
        f"/session/{COMPACTED}/thread/main/compaction/{COMPACTED_BOUNDARY}"
    ),
    "/session/{session_id}/thread/{source}/unattributed": (
        f"/session/{RESUME}/thread/main/unattributed"
    ),
    "/session/{session_id}/unattached": f"/session/{FORK_ORIGIN}/unattached",
    # The three pages that are not nodes: where a session failed, a thread's raw transcript,
    # and a file a tool wrote.
    "/session/{session_id}/errors": f"/session/{FORK_ORIGIN}/errors",
    "/session/{session_id}/thread/{source}/records": f"/session/{ANCESTOR}/thread/main/records",
    "/session/{session_id}/offload/{offload_name:path}": (
        f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}"
    ),
    # And the per-value fetches a pane's previews offer: one per fat column a node can hold.
    "/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}": (
        f"/fragment/text/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
    ),
    "/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}": (
        f"/fragment/thinking/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
    ),
    "/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}": (
        f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
    ),
    "/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}": (
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
    ),
    "/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}": (
        f"/fragment/command/session/{SPINE}/thread/{MAIN}/tool/{BASH_TOOL}"
    ),
    "/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}": (
        f"/fragment/prompt/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}"
    ),
    "/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}": (
        f"/fragment/args/session/{SPINE}/thread/main/turn/{SLASH_TURN}"
    ),
    "/fragment/brief/session/{session_id}/run/{run_id}": (
        f"/fragment/brief/session/{SPINE}/run/{SPINE_RUN}"
    ),
    # The run whose spawning `Agent` call the corpus holds, so both of these answer with a
    # value rather than the 404 a run nobody asked in words serves.
    "/fragment/prompt/session/{session_id}/run/{run_id}": (
        f"/fragment/prompt/session/{SPINE}/run/{SPINE_RUN}"
    ),
    "/fragment/result/session/{session_id}/run/{run_id}": (
        f"/fragment/result/session/{SPINE}/run/{SPINE_RUN}"
    ),
    # And what an enrichment pass wrote about an item, which the pane previews the same way and
    # fetches from one route per level. Pointed at an item the described fixture wrote both
    # lines for, so neither answers the 404 an item with no friction serves.
    "/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}": (
        f"/fragment/description/session/{SPINE}/thread/{MAIN}/turn/{DESCRIBED_TURN}"
    ),
    "/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}": (
        f"/fragment/friction/session/{SPINE}/thread/{MAIN}/turn/{DESCRIBED_TURN}"
    ),
    "/fragment/description/session/{session_id}/run/{run_id}": (
        f"/fragment/description/session/{SPINE}/run/{DESCRIBED_RUN}"
    ),
    "/fragment/friction/session/{session_id}/run/{run_id}": (
        f"/fragment/friction/session/{SPINE}/run/{DESCRIBED_RUN}"
    ),
    "/fragment/description/session/{session_id}": (
        f"/fragment/description/session/{DESCRIBED_SESSION}"
    ),
    "/fragment/friction/session/{session_id}": f"/fragment/friction/session/{DESCRIBED_SESSION}",
    "/fragment/record/session/{session_id}/thread/{source}/line/{line_no}": (
        f"/fragment/record/session/{ANCESTOR}/thread/main/line/1"
    ),
    # And a node's body alone, the way a log row expands its child. Two shapes: a run's URL
    # carries its id where every other kind carries a thread.
    "/fragment/body/session/{session_id}/thread/{source}/{kind}/{node_id}": (
        f"/fragment/body/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}"
    ),
    "/fragment/body/session/{session_id}/run/{run_id}": (
        f"/fragment/body/session/{SPINE}/run/{SPINE_RUN}"
    ),
    # And the rest of a level, the way a `+N more` row opens one. Two shapes again, plus the
    # thread the reader is on and the depth the rows are going to, neither of which the level
    # itself can say. The window is turned down because what these serve is whatever a window
    # left out — at the default, over this corpus, nothing at all.
    "/fragment/kin/session/{session_id}/thread/{source}/{kind}/{node_id}": (
        f"/fragment/kin/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}?kin=1&thread=main&depth=2"
    ),
    "/fragment/kin/session/{session_id}/{kind}/{node_id}": (
        f"/fragment/kin/session/{SPINE}/session/{SPINE}?kin=1&thread=main&depth=1"
    ),
    # And the numbers behind a tree row, which every row of the tree fetches when a reader
    # points at it. Three shapes: the session and the run carry their ids where a thread goes,
    # and everything recorded on a thread shares the third.
    "/fragment/numbers/session/{session_id}/thread/{source}/{kind}/{node_id}": (
        f"/fragment/numbers/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}"
    ),
    "/fragment/numbers/session/{session_id}/run/{run_id}": (
        f"/fragment/numbers/session/{SPINE}/run/{SPINE_RUN}"
    ),
    "/fragment/numbers/session/{session_id}": f"/fragment/numbers/session/{SPINE}",
    # And the statement behind a citation, which every page's footer links to.
    f"{QUERY_URL}/{{query_name}}": f"{QUERY_URL}/view_sessions",
}
