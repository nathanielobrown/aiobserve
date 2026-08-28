"""One real URL per route the viewer exposes — the scenario list the tier sweeps.

Its own module rather than a section of `conftest.py` because two kinds of reader want it:
the viewer tier, which parametrizes over it and checks it against the routes the app
declares, and the gallery, which serves each entry as a page you can open. A registry a
`conftest` owns is a registry only pytest can import.
"""

from enum import StrEnum
from typing import NamedTuple

from hyphae.view.citation import QUERY_URL
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


class Group(StrEnum):
    """What kind of thing a scenario opens — the gallery's headings, in the order it shows them."""

    PAGES = "Pages"
    NODES = "Node kinds"
    VALUES = "Value fetches"
    ENRICHMENT = "Enrichment fetches"
    PARTS = "Page parts"
    QUERY = "Queries"


class Scenario(NamedTuple):
    """One page of the viewer, at one URL, said in the words a reader picks it by."""

    url: str
    """One real URL against the fixture corpus."""

    title: str
    """What the page shows, in words — the gallery's link text and the snapshot's name."""

    group: Group
    """The heading it is listed under."""

    note: str = ""
    """Why this URL and not another, where the title cannot say it."""


# One item of each level that `enriched_db` describes *and* wrote a friction line for. A pass
# writes friction where it saw some, and the fixture's stands in for that by describing every
# fourth item (`tests/conftest.py:planted_enrichment`) — so the two fetches behind a described
# pane need an item that has both. Found by asking the described store for a row whose
# `friction` is not null.
DESCRIBED_SESSION = FORK_ORIGIN
DESCRIBED_RUN = SPINE_RUN
DESCRIBED_TURN = "5b848af7-f86e-4950-b474-cd98125fad24"

# The two reasons more than one scenario carries, said once each. Both are about the 404 the
# other URL would have served: a pass writes about some items and not others, and only the
# agent run someone asked for in words has a prompt and a result of its own.
DESCRIBED = "At an item the fixture pass described, so this answers a value and not a 404."
ASKED_RUN = "At the run whose spawning `Agent` call the corpus holds, so this answers a value."

# And the one a URL carries rather than the registry: what these two serve is whatever a
# window left out, and at the default window, over this corpus, that is nothing at all.
SPILLED = "The `kin` window is turned down, because the default leaves nothing out here."

# One real URL per route the app exposes, keyed by the route's own path template. Two tiers
# read it whole — the payload sweep weighs every URL, and the citation leaves read the footer
# of every page among them — and one leaf reads it as a set against the routes the app
# declares, so a route added with no entry here fails rather than going unread.
SCENARIOS: dict[str, Scenario] = {
    "/": Scenario("/", "Projects", Group.PAGES),
    "/sessions": Scenario("/sessions", "Session list", Group.PAGES),
    # The three pages that are not nodes: where a session failed, a thread's raw transcript,
    # and a file a tool wrote.
    "/session/{session_id}/errors": Scenario(
        f"/session/{FORK_ORIGIN}/errors", "Every failed tool call of a session", Group.PAGES
    ),
    "/session/{session_id}/thread/{source}/records": Scenario(
        f"/session/{ANCESTOR}/thread/main/records", "A thread's raw records", Group.PAGES
    ),
    "/session/{session_id}/offload/{offload_name:path}": Scenario(
        f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}",
        "An offload file a tool wrote",
        Group.PAGES,
    ),
    # The eight node kinds, each at a session that records the shape: a compaction at the
    # session with two of them, a bucket at each of the two sessions that has one.
    "/session/{session_id}": Scenario(f"/session/{SPINE}", "Session", Group.NODES),
    "/session/{session_id}/thread/{source}/turn/{turn_id}": Scenario(
        f"/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}", "Turn", Group.NODES
    ),
    "/session/{session_id}/run/{run_id}": Scenario(
        f"/session/{SPINE}/run/{SPINE_RUN}", "Agent run", Group.NODES
    ),
    "/session/{session_id}/thread/{source}/call/{api_call_id}": Scenario(
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
        "API call",
        Group.NODES,
    ),
    "/session/{session_id}/thread/{source}/tool/{tool_call_id}": Scenario(
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        "Tool call",
        Group.NODES,
    ),
    "/session/{session_id}/thread/{source}/compaction/{compaction_id}": Scenario(
        f"/session/{COMPACTED}/thread/main/compaction/{COMPACTED_BOUNDARY}",
        "Compaction",
        Group.NODES,
    ),
    "/session/{session_id}/thread/{source}/unattributed": Scenario(
        f"/session/{RESUME}/thread/main/unattributed",
        "Unattributed api calls",
        Group.NODES,
    ),
    "/session/{session_id}/unattached": Scenario(
        f"/session/{FORK_ORIGIN}/unattached", "Unattached agent runs", Group.NODES
    ),
    # The per-value fetches a pane's previews offer: one per fat column a node can hold.
    "/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}": Scenario(
        f"/fragment/text/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
        "What an api call answered",
        Group.VALUES,
    ),
    "/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}": Scenario(
        f"/fragment/thinking/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
        "What an api call thought",
        Group.VALUES,
    ),
    "/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}": Scenario(
        f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        "What a tool call was given",
        Group.VALUES,
    ),
    "/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}": Scenario(
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        "What a tool call gave back",
        Group.VALUES,
    ),
    "/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}": Scenario(
        f"/fragment/command/session/{SPINE}/thread/{MAIN}/tool/{BASH_TOOL}",
        "The command a Bash call ran",
        Group.VALUES,
    ),
    "/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}": Scenario(
        f"/fragment/prompt/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}",
        "What a turn was asked",
        Group.VALUES,
    ),
    "/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}": Scenario(
        f"/fragment/args/session/{SPINE}/thread/main/turn/{SLASH_TURN}",
        "What followed a turn's slash command",
        Group.VALUES,
    ),
    "/fragment/brief/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/brief/session/{SPINE}/run/{SPINE_RUN}",
        "The brief an agent run was given",
        Group.VALUES,
    ),
    "/fragment/prompt/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/prompt/session/{SPINE}/run/{SPINE_RUN}",
        "What an agent run was asked",
        Group.VALUES,
        note=ASKED_RUN,
    ),
    "/fragment/result/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/result/session/{SPINE}/run/{SPINE_RUN}",
        "What an agent run reported back",
        Group.VALUES,
        note=ASKED_RUN,
    ),
    "/fragment/record/session/{session_id}/thread/{source}/line/{line_no}": Scenario(
        f"/fragment/record/session/{ANCESTOR}/thread/main/line/1",
        "One raw record, whole",
        Group.VALUES,
    ),
    # And what an enrichment pass wrote about an item, which the pane previews the same way
    # and fetches from one route per level.
    "/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}": Scenario(
        f"/fragment/description/session/{SPINE}/thread/{MAIN}/turn/{DESCRIBED_TURN}",
        "What a pass wrote about a turn",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    "/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}": Scenario(
        f"/fragment/friction/session/{SPINE}/thread/{MAIN}/turn/{DESCRIBED_TURN}",
        "The friction a pass saw in a turn",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    "/fragment/description/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/description/session/{SPINE}/run/{DESCRIBED_RUN}",
        "What a pass wrote about an agent run",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    "/fragment/friction/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/friction/session/{SPINE}/run/{DESCRIBED_RUN}",
        "The friction a pass saw in an agent run",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    "/fragment/description/session/{session_id}": Scenario(
        f"/fragment/description/session/{DESCRIBED_SESSION}",
        "What a pass wrote about a session",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    "/fragment/friction/session/{session_id}": Scenario(
        f"/fragment/friction/session/{DESCRIBED_SESSION}",
        "The friction a pass saw in a session",
        Group.ENRICHMENT,
        note=DESCRIBED,
    ),
    # And a node's body alone, the way a log row expands its child. Two shapes: a run's URL
    # carries its id where every other kind carries a thread.
    "/fragment/body/session/{session_id}/thread/{source}/{kind}/{node_id}": Scenario(
        f"/fragment/body/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}",
        "A turn's body, expanded in place",
        Group.PARTS,
    ),
    "/fragment/body/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/body/session/{SPINE}/run/{SPINE_RUN}",
        "An agent run's body, expanded in place",
        Group.PARTS,
    ),
    # And the rest of a level, the way a `+N more` row opens one. Two shapes again, plus the
    # thread the reader is on and the depth the rows are going to, neither of which the level
    # itself can say.
    "/fragment/kin/session/{session_id}/thread/{source}/{kind}/{node_id}": Scenario(
        f"/fragment/kin/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}?kin=1&thread=main&depth=2",
        "The NavTree rows under a turn that a window left out",
        Group.PARTS,
        note=SPILLED,
    ),
    "/fragment/kin/session/{session_id}/{kind}/{node_id}": Scenario(
        f"/fragment/kin/session/{SPINE}/session/{SPINE}?kin=1&thread=main&depth=1",
        "The NavTree rows under a session that a window left out",
        Group.PARTS,
        note=SPILLED,
    ),
    # And the numbers behind a NavTree row, which every row of the NavTree fetches when a
    # reader points at it. Four shapes: the session and the run carry their ids where a thread
    # goes, the compaction has a route of its own because it shares no column with the kinds
    # made of api calls, and everything else recorded on a thread shares the fourth.
    "/fragment/numbers/session/{session_id}/thread/{source}/{kind}/{node_id}": Scenario(
        f"/fragment/numbers/session/{ANCESTOR}/thread/main/turn/{DENSE_TURN}",
        "The popover behind a turn's NavTree row",
        Group.PARTS,
    ),
    "/fragment/numbers/session/{session_id}/thread/{source}/compaction/{compaction_id}": Scenario(
        f"/fragment/numbers/session/{COMPACTED}/thread/main/compaction/{COMPACTED_BOUNDARY}",
        "The popover behind a compaction's NavTree row",
        Group.PARTS,
    ),
    "/fragment/numbers/session/{session_id}/run/{run_id}": Scenario(
        f"/fragment/numbers/session/{SPINE}/run/{SPINE_RUN}",
        "The popover behind an agent run's NavTree row",
        Group.PARTS,
    ),
    "/fragment/numbers/session/{session_id}": Scenario(
        f"/fragment/numbers/session/{SPINE}",
        "The popover behind a session's NavTree row",
        Group.PARTS,
    ),
    # And the statement behind a citation, which every page's footer links to.
    f"{QUERY_URL}/{{query_name}}": Scenario(
        f"{QUERY_URL}/view_sessions", "The query behind the session list", Group.QUERY
    ),
}
