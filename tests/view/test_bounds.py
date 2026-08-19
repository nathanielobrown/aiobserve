"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Three mechanisms, checked separately: the queries behind the pages and fragments never select
an unbounded fat column, what they do select is truncated in SQL rather than in the template,
and every page size is a bound parameter whose production default is pinned here. Together
they are what makes the bound hold by construction rather than by the fixture corpus's luck —
a per-value fetch is the one exception, and it is exempt because its unit *is* one value.
"""

import re
from html import unescape
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.analyze.queries import QUERIES, VIEW_PREFIX
from aiobserve.view import bounds
from aiobserve.view.app import build_app
from aiobserve.view.listing import SHOWN
from aiobserve.view.store import Fragment, Page, Value, cursorless_rows
from aiobserve.view.threads import TURN_CURSOR
from tests.conftest import (
    ANCESTOR,
    CONFIG_ONLY,
    DENSE_CALL,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    OFFLOAD_FILE,
    RESUME,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import Planter, Statement, fields, inside, one, values

# The columns that hold whatever the agent read or wrote: one of them can be megabytes, and
# none of them belongs on a page whole. `raw` is a transcript line, `result` a tool's output,
# `input` its arguments, `text` and `thinking` a model's answer, and `description` the line a
# run was spawned with or the one a pass wrote about an item — prose either way, and nothing
# bounds what a caller passes the Agent tool. `agent_type` and `model` are short in every
# session recorded so far and short by nothing: an agent definition is named by whoever writes
# it, and a model name is a string an api request carried.
# `prompt` is whatever was typed or pasted at a turn, and `command_args` whatever followed a
# slash command — the canonical store holds one of 7,947 characters. Both reach a page through
# a turn's heading, and both are cut by the digests that select them.
FAT = (
    "raw",
    "text",
    "thinking",
    "result",
    "input",
    "content",
    "description",
    "agent_type",
    "model",
    "prompt",
    "command_args",
)

# What a page may weigh. The list is the page a corpus grows, so `bounds.SESSIONS.ceiling` rows of
# what one row can hold have to fit under it. Raised from 350,000 when the pages began showing
# what an enrichment pass said: a described run row costs half again what a bare one does, and
# `bounds.CHIP_BUDGET` multiplies that 200 times. The alternative was cutting the budget, which
# would put the widest forest the corpus records behind a "+N more" nobody can open.
PAGE_BYTES = 500_000
# What the markup around one row of the list costs, with the content the row carries taken off.
# Re-measured through the app by the leaf at the bottom of this file, every cap full of `&`,
# at the dearest row the list holds rather than at whichever one sorted second: that row cost
# 4,519 B, of which 2,730 B is content at those caps and 257 B the enrichment markup below,
# leaving 1,532 B of stacked cells, counted lists and the row around them.
MEASURED_SESSION_ROW_MARKUP = 1_600
# What the markup around one row's enrichment costs on top of that, with the model's own words
# taken off. Measured through the app by the leaf at the bottom of this file, every field
# planted full of `&`: 257 B. The list never renders the stale tag — it joins what a pass wrote
# and not the versions that would judge it — so this is the two tags and the block around them.
MEASURED_LIST_ENRICHMENT_MARKUP = 300
# What a list page weighs apart from its rows: the filter form, the project suggestions, the
# table head and the two pagers. Measured through the app by the leaf at the bottom of this
# file, with `&` planted in every suggestion and the box at its cap — 8,915 B, a worst case
# rather than a corpus observation, because the box is bound in SQL like everything else.
MEASURED_LIST_CHROME = 10_000
# What the markup around one row of the landing page costs, with the path it carries taken off,
# and what that page weighs apart from its rows: the table head, and the line saying how many
# projects it left out. Both re-measured through the app by the leaf at the bottom of this
# file, every project path planted full of `&` and the store filled past the page's ceiling:
# 2,076 B a row, of which 782 B is a planted path in its cell and in its link, leaving 1,294 B
# of stacked window cells and the row around them — and 2,055 B of chrome, which is small
# because the page carries no form, no pager and no suggestions.
MEASURED_PROJECT_ROW_MARKUP = 1_400
MEASURED_PROJECTS_CHROME = 2_500

# How much of a turn's prompt the timeline shows, from `session_digest`'s own `substr`, and
# the same two for what a slash-command turn shows instead: the name of the command, and what
# was typed after it. A command is named by whoever wrote the file that defines it, so the
# badge is cut in SQL like everything else rather than assumed short.
PROMPT_CHARS = 300
COMMAND_NAME_CHARS = 60
COMMAND_ARGS_CHARS = 300
# The same, for what a fragment shows of an api call's text and a tool call's arguments.
TEXT_CHARS = 2_000
INPUT_CHARS = 200
# What the markup around one call row and one tool row of a turn fragment costs, with the
# content those rows carry subtracted. Measured against `data/traces.duckdb` on 2026-08-07 as
# the marginal cost of one more row — 18,292 B for 20 tool rows less 1,255 B for one, and
# 50,311 B for 25 call rows less 2,346 B for one — with the rows' own `input` and `text` heads
# taken back off. The call figure was measured at `tools=1`, so it still carries one tool row
# that the arithmetic below counts again: a ceiling should err high. The fixture rows are
# redacted to a few characters and project nothing about either.
MEASURED_TOOL_ROW_MARKUP = 700
MEASURED_CALL_ROW_MARKUP = 2_000
# What a row of the records browser really costs — the preview plus the row's own markup, most
# of it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` on
# 2026-08-08: 83,659 B for a 100-record page less 1,865 B of chrome, over the 99 rows between.
# The fixture records are redacted to a few characters, so they project nothing about this.
MEASURED_RECORD_BYTES = 826

# What the markup around one row of a session timeline costs, with the row's own content taken
# off. Measured through the app with `&` planted at every cap, which is what the leaf at the
# bottom of this file re-measures so none of the three can go stale: 1,313 B for a turn row and
# 264 B for the "+N more" line one turn may carry, 311 B for a run row — the nested kind, the
# dearer of the two — and 266 B for a compaction row.
MEASURED_TURN_ROW_MARKUP = 1_700
MEASURED_CHIP_ROW_MARKUP = 400
MEASURED_MARK_ROW_MARKUP = 300
# What the markup around one item's enrichment costs on top of those, with the model's own
# words taken off — the block beside a turn, and the tags beside a run. Measured the same way,
# with every enrichment field planted full of `&` and every row stamped stale so the third tag
# renders too: 314 B for a turn and 244 B for a run.
MEASURED_TURN_ENRICHMENT_MARKUP = 400
MEASURED_CHIP_ENRICHMENT_MARKUP = 300
# What a session page weighs apart from the rows above: the header, the page's own markup, and
# the lines a cut list mints. Measured through the app by the leaf at the bottom of this file,
# with `&` planted at every cap the header carries and the session described at every one of
# its own — 14,419 B, which is a page's worst case rather than a corpus observation, because
# everything in a header is now cut in SQL.
MEASURED_SESSION_CHROME = 15_000
# What the markup around one node of the map beside a session page costs, with the label the
# node carries taken off, and what the map weighs apart from its nodes: the citation lines, the
# heading over the runs no turn holds, and the tail saying how many nodes the cap cut. Both
# re-measured through the app by the leaf at the bottom of this file, every cap a label reads
# planted full of `&` and every node's cost left short of a call: 721 B a turn node and 754 B a
# run node — the nested kind, the dearer of the two — of which 240 B is the label at its cap,
# leaving 514 B of link, meter class, unpriced mark and the row around them; and 1,055 B of
# chrome, the widest any recorded map reaches. The allowance is larger than that because no
# recorded session carries the heading and the tail at once.
MEASURED_NAV_NODE_MARKUP = 600
MEASURED_NAV_CHROME = 1_500
# The parameter every truncated column of a run row is cut to. Counted per query rather than
# listed, so a fourth column added to a chip shows up in the arithmetic instead of quietly
# spending the ceiling `bounds.CHIP_BUDGET` times over.
CHIP_HEAD = "$chip_chars"
# The same two for one row of the session list, whose cuts the viewer composes around the
# query rather than making in it: a string's head, and a skill name's.
LIST_HEAD = "$head_chars"
LIST_ITEM_HEAD = "$item_chars"
# And the one a kind of work in the Work cell is cut to, which is a tag's head rather than a
# name's: the categories a pass writes come from a taxonomy, not from a transcript.
LIST_KIND_HEAD = "$kind_chars"

# The most one character of a transcript's own content can weigh on the page that shows it.
# Content has no shape at all — a tool wrote the file, a model wrote the text — so every bound
# over it holds for the worst character rather than the measured average. Markupsafe's longest
# escape is five bytes (`&amp;`, `&#34;`, `&#39;`), and the longest UTF-8 encoding is four, so
# five bytes a character covers both.
ESCAPED_CHAR_BYTES = 5
# And the most one character can weigh where a page writes it into a link rather than into
# text. Percent-encoding spends three bytes on every byte it escapes, and a character is up to
# four bytes of UTF-8: a project path is a directory someone named, so its link is budgeted at
# the worst character the same way its cell is.
ENCODED_CHAR_BYTES = 12


def heads(sql: str, parameter: str) -> int:
    """How many of a statement's columns are cut to `parameter` — what one of its rows carries."""
    return re.sub(r"--[^\n]*", " ", sql).count(parameter)


def worst_call_bytes(page_tools: int) -> int:
    """What one call row of a turn fragment can weigh: markup, its heads, and its tool rows.

    Markup is measured, because it is ours; every content head is counted at the worst
    character, because a call's `text`, its model names and a tool's `input` are whatever the
    session held. The model names are counted off the query rather than listed, so a fourth
    string added to a call row lands here instead of quietly spending the ceiling ten times over.
    """
    tool_row = MEASURED_TOOL_ROW_MARKUP + INPUT_CHARS * ESCAPED_CHAR_BYTES
    names = heads(queries.load(Fragment.TURN_CALLS), CHIP_HEAD) * queries.CHIP_CHARS
    return (
        MEASURED_CALL_ROW_MARKUP + (TEXT_CHARS + names) * ESCAPED_CHAR_BYTES + page_tools * tool_row
    )


def worst_session_row_bytes() -> int:
    """What one row of the session list can weigh: its markup, and every head it shows all `&`.

    The heads are counted off the composition rather than listed, so a column added to what a
    row shows lands in the arithmetic instead of quietly spending the ceiling
    `bounds.SESSIONS.ceiling` times over. A described row is what this budgets — the enrichment the
    list joins is a column of the row like the rest, and every row of a described store carries
    it — which is why the description takes a row's head and not the page's larger one.
    """
    said = queries.load(Page.DESCRIBED_SESSIONS)
    strings = heads(SHOWN, LIST_HEAD) * queries.LIST_CHARS
    # The skill names are cut in the composition and the agent types in the query itself —
    # a type is grouped after its cut, so the cut has to be where the grouping can see it.
    listed = heads(SHOWN, LIST_ITEM_HEAD) + heads(queries.load(Page.SESSIONS), LIST_ITEM_HEAD)
    names = listed * queries.LIST_ITEMS * queries.LIST_ITEM_CHARS
    described = heads(said, LIST_HEAD) * queries.LIST_CHARS
    kinds = heads(said, LIST_KIND_HEAD) * queries.LIST_CATEGORIES * queries.TAG_CHARS
    return (
        MEASURED_SESSION_ROW_MARKUP
        + (strings + names + described + kinds) * ESCAPED_CHAR_BYTES
        + MEASURED_LIST_ENRICHMENT_MARKUP
        + worst_tag_bytes()
    )


def worst_project_row_bytes() -> int:
    """What one row of the landing page can weigh: its markup, and the path it carries twice.

    A project path is a directory someone chose, so both copies are counted at the worst
    character — once escaped into the cell, once percent-encoded into the link that narrows
    the list to it. Everything else in the row is the store's own arithmetic: two counts, three
    costs and a timestamp, each as long as its type allows and no longer.
    """
    return MEASURED_PROJECT_ROW_MARKUP + queries.LIST_CHARS * (
        ESCAPED_CHAR_BYTES + ENCODED_CHAR_BYTES
    )


def worst_tag_bytes() -> int:
    """What the taxonomy tags beside a described item can weigh, all `&`.

    Two of them — category and outcome — and the third says the row is stale, which is words
    of ours rather than of the store's and rides in the markup measured above.
    """
    return 2 * queries.TAG_CHARS * ESCAPED_CHAR_BYTES


def worst_heading_bytes() -> int:
    """What one turn's heading can weigh in content: the larger of its two arms, never both.

    A slash turn shows the command it ran and the arguments after it *instead of* the prompt
    as recorded, so the two arms never land on one heading — and pricing their sum would put
    a full page of turns some 13 KB over the ceiling for a cost no reader can produce.
    """
    return max(PROMPT_CHARS, COMMAND_NAME_CHARS + COMMAND_ARGS_CHARS) * ESCAPED_CHAR_BYTES


def worst_turn_bytes() -> int:
    """What one turn row of a session timeline can weigh: its markup, the "+N more" line it may
    carry, a heading of nothing but `&`, and what a pass said about the turn.

    A turn has no page of its own, so its description rides the row that lists it — the one
    place a described item costs a page more than a link would.
    """
    return (
        MEASURED_TURN_ROW_MARKUP
        + worst_heading_bytes()
        + MEASURED_TURN_ENRICHMENT_MARKUP
        + queries.ENRICHMENT_CHARS * ESCAPED_CHAR_BYTES
        + worst_tag_bytes()
    )


def worst_chip_bytes() -> int:
    """What one run row can weigh: its markup, every head it shows all `&`, and its tags.

    Tags and no description: what a pass said a run did is on the run's own page, which the
    row already links to. The row is the one the caps multiply two hundred times, so the
    description it doesn't carry is most of what the ceiling above buys back.
    """
    return (
        MEASURED_CHIP_ROW_MARKUP
        + heads(queries.load(Page.RUNS), CHIP_HEAD) * queries.CHIP_CHARS * ESCAPED_CHAR_BYTES
        + MEASURED_CHIP_ENRICHMENT_MARKUP
        + worst_tag_bytes()
    )


def worst_mark_bytes() -> int:
    """What one compaction row can weigh: its markup, and its trigger all `&`."""
    return MEASURED_MARK_ROW_MARKUP + (
        heads(queries.load(Page.COMPACTIONS), CHIP_HEAD) * queries.CHIP_CHARS * ESCAPED_CHAR_BYTES
    )


def worst_session_bytes() -> int:
    """The largest session page any pair of sizes a URL can carry produces.

    A search over the pairs the route allows, not the product of the two maxima: the sizes
    trade against each other, so 20 turns of 100 runs each is not a page anyone can ask for.
    The turn rows are `turns` plus the rows no cursor reaches, which ride the last page
    outside the size a reader asked for; the run-row `+ 1` is the unattached list, a list of
    `chips` like a turn's that rides every page. The rows the window cannot reach add no run
    rows of their own: the digest gives them `queries.UNATTRIBUTED` for a turn id, which no
    run's `spawn_turn_id` carries. The marks are not a size a URL carries, so they cost the
    same on any page.
    """
    return (
        MEASURED_SESSION_CHROME
        + bounds.MARKS * worst_mark_bytes()
        + max(
            (turns + bounds.CURSORLESS_TURNS) * worst_turn_bytes()
            + (turns + 1) * chips * worst_chip_bytes()
            for turns in range(1, bounds.TURNS.ceiling + 1)
            for chips in range(1, bounds.CHIPS.ceiling + 1)
            if (turns + 1) * chips <= bounds.CHIP_BUDGET
        )
    )


def worst_nav_node_bytes() -> int:
    """What one node of a session's map can weigh: its markup, and a label of nothing but `&`.

    One label and two numbers, whichever kind of node it is — a turn's label is the head of
    what a pass said about it, of the command it ran, or of the prompt, and a run's is its
    agent type and the line it was spawned with. Each is composed and then cut to the one cap
    the map reads at, so a node carries one head and not a head per column. The `<details>` a
    nested run sits under rides the markup, which is measured at the dearer of the two kinds.
    """
    return MEASURED_NAV_NODE_MARKUP + queries.NAV_CHARS * ESCAPED_CHAR_BYTES


def worst_record_bytes() -> int:
    """What one row of the records browser can weigh: its markup, and a preview of `&`."""
    return (
        MEASURED_RECORD_BYTES - queries.RECORD_PREVIEW + queries.RECORD_PREVIEW * ESCAPED_CHAR_BYTES
    )


# Describing every item of a store at every cap: a row per turn, per run and per session, each
# field full of `&` and each stamped under version 0 so the stale tag renders too. `enriched_db`
# describes most of its items and no plant can reach the rest, so the rows go in wholesale —
# a marginal cost measured between a described row and an undescribed one is not one.
def _described_at_every_cap() -> tuple[Statement, ...]:
    payload: list[str | int] = [
        "&" * queries.ENRICHMENT_CHARS,
        "&" * queries.TAG_CHARS,
        "&" * queries.TAG_CHARS,
        "&" * queries.ENRICHMENT_CHARS,
    ]
    stamp = "'planted', 0, 0, 'planted', '1970-01-01T00:00:00Z'"
    return (
        ("DELETE FROM turn_enrichments", []),
        (
            "INSERT INTO turn_enrichments"
            f" SELECT t.session_id, t.source, t.id, ?, ?, ?, ?, {stamp} FROM live_turns t",
            payload,
        ),
        ("DELETE FROM agent_run_enrichments", []),
        (
            "INSERT INTO agent_run_enrichments"
            f" SELECT r.session_id, r.id, ?, ?, ?, ?, {stamp} FROM live_agent_runs r",
            payload,
        ),
        ("DELETE FROM session_enrichments", []),
        (
            f"INSERT INTO session_enrichments SELECT s.id, ?, ?, ?, ?, {stamp} FROM sessions s",
            payload,
        ),
    )


DESCRIBED_AT_EVERY_CAP = _described_at_every_cap()

# What a query may wrap a fat column in and still be bounded: a fixed-width prefix of it, or
# a count of what it holds. Anything else puts the whole value on the page.
BOUNDING = ("substr", "length")


def unbounded(sql: str) -> set[str]:
    """The fat columns a statement selects outside a bounding call — what a page can't afford.

    An output name is not a selected column, so `AS` and what follows it comes out first: a
    cut column keeps the name of the column it cuts, and the cut is what the page shows.
    """
    without_comments = re.sub(r"--[^\n]*", " ", sql)
    named = re.sub(r"\bAS\s+[A-Za-z_][A-Za-z0-9_]*", " ", without_comments, flags=re.IGNORECASE)
    truncated = re.sub(rf"(?:{'|'.join(BOUNDING)})\s*\([^()]*\)", " ", named)
    return {word for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", truncated) if word in FAT}


def test_the_fat_column_scan_catches_one() -> None:
    """The scan below is worth its green: it flags a select the pages must not contain.

    The statements are invented — no shipped query selects a fat column whole, which is
    exactly why the instrument needs its own case.
    """
    assert unbounded("SELECT r.raw FROM raw_records r -- text") == {"raw"}
    assert unbounded("SELECT substr(r.raw, 1, 200) AS raw_head FROM raw_records r") == set()
    # A count of a value is a number, and a page can afford any number.
    assert unbounded("SELECT length(r.raw) AS raw_chars FROM raw_records r") == set()
    # A cut column may keep the name of the column it cuts, and the name is not the value...
    assert unbounded("SELECT substr(e.description, 1, 200) AS description FROM turns e") == set()
    # ...but the column under that name still counts.
    assert unbounded("SELECT e.description AS description FROM turns e") == {"description"}


@pytest.mark.parametrize("name", sorted(Page) + sorted(Fragment))
def test_no_page_or_fragment_query_selects_a_fat_column_whole(name: str) -> None:
    """Every query behind a page or a fragment is bounded in SQL, however large the record."""
    assert unbounded(queries.load(name)) == set()


@pytest.mark.parametrize("value", sorted(Value))
def test_a_per_value_query_returns_the_one_value_it_is_named_for(value: Value) -> None:
    """The per-value queries are the exception, and they are the exception by declaration.

    They select a fat column whole — that is what they are for. What keeps the bound is that
    the unit is one row of one value, so the fetch tops out at the largest value in the store
    rather than at a page's worth of them. Rendering is the other half of that promise, and
    the planted leaf below holds it: what a fragment serves stays proportional to what the
    store holds, however the value nests.
    """
    assert unbounded(queries.load(value)) != set()


def test_every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value() -> None:
    """A viewer query lands in one of the three sets, so the scans above cannot miss it.

    Without this, a query shipped under `view_` but named in no enum is scanned by nothing
    and can select a fat column onto a page with the whole tier still green.
    """
    declared = set(Page) | set(Fragment) | set(Value)
    # Every query the viewer owns is scanned by one of the leaves above...
    assert {name for name in QUERIES if name.startswith(VIEW_PREFIX)} <= declared
    # ...and every name declared is a query that ships, digests shared with the runner too.
    assert declared <= set(QUERIES)


def test_the_manifest_pins_the_production_page_sizes() -> None:
    """The page sizes the payload bound is computed from are the ones production runs.

    Every other leaf in this file binds fixture-sized values, so without this pin the whole
    section would pass against any defaults at all — a `page_tools` of 5,000 would break the
    bound in production while CI stayed green. The numbers come from the turn-expand
    paragraph of `plans/trace-viewer/design.md`.
    """
    assert QUERIES["view_turn_calls"].params["page_calls"].default == 10
    assert QUERIES["view_call_tools"].params["page_tools"].default == 12
    assert QUERIES["view_records"].params["page_records"].default == 100
    assert QUERIES["view_records"].params["preview_chars"].default == 160
    assert QUERIES["view_offload"].params["chunk_chars"].default == 50_000
    # How much of a run row's three columns a chip shows. `session_digest` keeps no LIMIT of
    # its own — a report quotes the whole digest — so the timeline's *sizes* are the ones the
    # viewer composes around it, and they are checked as arithmetic below rather than pinned.
    assert QUERIES["view_runs"].params["chip_chars"].default == 60
    assert QUERIES["view_compactions"].params["chip_chars"].default == 60
    # A call row is multiplied by the page the same way, and carries two model names — which
    # an api request wrote, so their length is Claude Code's to change and not ours.
    assert QUERIES["view_turn_calls"].params["chip_chars"].default == 60
    # And what a session header shows of each string and each list it carries. The header is
    # the page's one unbounded surface — a session's PR links grow with every PR it opens — so
    # these three are what turn `MEASURED_SESSION_CHROME` into a worst case.
    assert QUERIES["view_session_header"].params["head_chars"].default == 100
    assert QUERIES["view_session_header"].params["item_chars"].default == 60
    assert QUERIES["view_session_header"].params["head_items"].default == 5
    # And how much of a label the map beside a session page shows, per node. Short by design:
    # a node is a line in a sidebar rather than a row of a table, and the map is the one list
    # whose rows a reader sees all of.
    assert QUERIES["view_session_nav"].params["nav_chars"].default == 48
    # A run header carries three strings of the same kind — the line the run was spawned with,
    # the agent definition that ran, and the model it ran on — so they take the same head.
    assert QUERIES["view_run_header"].params["head_chars"].default == 100
    # The list's rows drop the agent types a session spawned, but the query behind them still
    # gathers the names, so a member is cut where the list cuts a skill name.
    assert QUERIES["view_sessions"].params["item_chars"].default == queries.LIST_ITEM_CHARS
    # And how much of what a pass wrote an item shows. The taxonomy is closed and its longest
    # member is nine characters (`enrich/taxonomy.py`), so the tag cut bounds a hand-edited row
    # rather than anything a pass writes.
    assert QUERIES["view_enrichment"].params["description_chars"].default == 200
    assert QUERIES["view_enrichment"].params["tag_chars"].default == 20
    # The same for the list, whose row the viewer cuts in its own composition — the filters
    # read the whole values — and whose project suggestions the query itself caps.
    assert (queries.LIST_CHARS, queries.LIST_ITEM_CHARS, queries.LIST_ITEMS) == (100, 20, 4)
    assert QUERIES["view_projects"].params["head_chars"].default == 100
    assert QUERIES["view_projects"].params["head_projects"].default == 10
    # And the landing page, whose row shows a path at the list's head and links by the whole
    # one. How many projects it ranks is a size like the rest; the two windows it counts them
    # in are not sizes, and `tests/view/test_projects.py` pins those against what it cites.
    assert QUERIES["view_project_rollups"].params["head_chars"].default == queries.LIST_CHARS
    assert QUERIES["view_project_rollups"].params["projects"].default == 100
    # Every ceiling is projected at the largest page a URL can ask for, because a size is
    # something a reader types. The turn fragment's two sizes multiply, so its ceiling is
    # spent by the defaults themselves and `?calls=` only goes down from here.
    assert bounds.CALLS.ceiling * worst_call_bytes(bounds.TOOLS.ceiling) < PAGE_BYTES
    assert bounds.RECORDS.ceiling * worst_record_bytes() < PAGE_BYTES
    assert bounds.CHUNK.ceiling * ESCAPED_CHAR_BYTES < PAGE_BYTES
    # The list is the page a corpus grows, so its ceiling is the widest page a URL can ask for
    # plus the chrome that rides every page — both bound by construction now, not by how long
    # the titles this corpus happens to hold are.
    assert MEASURED_LIST_CHROME + bounds.SESSIONS.ceiling * worst_session_row_bytes() < PAGE_BYTES
    # The landing page grows the same way — a project per repository the corpus records — and
    # its ceiling is not a size a URL carries: a reader picks a project rather than paging.
    assert (
        MEASURED_PROJECTS_CHROME + bounds.PROJECTS.ceiling * worst_project_row_bytes() < PAGE_BYTES
    )
    # The map beside a session page is its own response, so it spends a ceiling of its own
    # rather than the page's: every node its cap admits, each label at the cut the query makes,
    # plus what the map carries whatever it holds.
    assert MEASURED_NAV_CHROME + bounds.NAV.ceiling * worst_nav_node_bytes() < PAGE_BYTES
    # The session timeline is the page whose caps this arithmetic sets rather than checks: its
    # run rows are most of what the ceiling buys, and the chip budget is what that leaves room for.
    assert worst_session_bytes() < PAGE_BYTES
    # And no default asks for more than its own ceiling allows, which nothing else checks: a
    # default above the ceiling serves a 400 to a reader who typed no size at all. Read off the
    # module rather than listed, so a size added later cannot dodge the check.
    declared = {
        name: value for name, value in vars(bounds).items() if isinstance(value, bounds.Bound)
    }
    for name, bound in declared.items():
        assert bound.default <= bound.ceiling, name
    # ...and those are the sizes this leaf priced above: a new one reds here until its ceiling
    # is spent in the arithmetic too, rather than riding a page nobody weighed.
    assert set(declared) == {
        "CALLS",
        "TOOLS",
        "RECORDS",
        "CHUNK",
        "TURNS",
        "CHIPS",
        "SESSIONS",
        "PROJECTS",
        "NAV",
    }
    assert (bounds.TURNS.default + 1) * bounds.CHIPS.default <= bounds.CHIP_BUDGET
    # And every run is reachable: one turn's runs, or the unattached list, fits a page of its
    # own at `?turns=1&chips={bounds.CHIPS.ceiling}` — which the widest forest the corpus records,
    # 94 runs under one turn, needs to be more than a "+N more" nobody can open.
    assert (1 + 1) * bounds.CHIPS.ceiling <= bounds.CHIP_BUDGET


def limits(sql: str) -> list[str]:
    """What follows each LIMIT in a statement, comments cut — a parameter, or a number."""
    return re.findall(r"\bLIMIT\s+([^\s;]+)", re.sub(r"--[^\n]*", " ", sql))


def test_the_limit_scan_catches_a_literal_page_size() -> None:
    """The scan below is worth its green: it flags the page size no caller can change.

    Both statements are invented — every shipped query binds its limit, which is exactly why
    the instrument needs a case of its own.
    """
    assert limits("SELECT * FROM raw_records LIMIT 100;") == ["100"]
    assert limits("SELECT * FROM raw_records LIMIT $page_records -- LIMIT 100") == ["$page_records"]


@pytest.mark.parametrize("name", sorted(name for name in QUERIES if name.startswith(VIEW_PREFIX)))
def test_every_page_size_in_a_viewer_query_is_a_bound_parameter(name: str) -> None:
    """No viewer query hides a page size in its text, so every bound is one a reader can see.

    The rule rather than a list of the parameters that exist today: a query landing with a
    literal `LIMIT 100` is a size nobody can bind down to reach its boundary in a test, and
    nobody can bind up when a real corpus needs more.
    """
    for limit in limits(queries.load(name)):
        assert limit.startswith("$"), f"{name} limits by a literal: {limit}"
        assert limit.lstrip("$") in QUERIES[name].params


def test_every_fat_column_is_still_a_column(store: duckdb.DuckDBPyConnection) -> None:
    """The scan is spelled in column names, so a rename must fail here rather than pass."""
    named = {
        row[0]
        for row in store.execute(
            "SELECT column_name FROM duckdb_columns() WHERE schema_name = 'main'"
        ).fetchall()
    }
    assert set(FAT) <= named


def test_a_served_page_stays_under_its_ceiling(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """No page the viewer serves is large enough to stall a browser, at any corpus size."""
    listing = len(client.get("/sessions").content)
    (count,) = one(store, "SELECT count(*) FROM sessions")
    assert listing < PAGE_BYTES
    # The fixture corpus is smaller than a page, so its own weight proves nothing about a
    # large one. What does is the marginal cost of a row — the whole list less the same page
    # holding one session — which is what a growing corpus multiplies. The rows here are
    # redacted down to a few characters, so this is a smoke check: the worst case a real
    # corpus can reach is the arithmetic above, and the planted leaf below re-measures it.
    chrome = len(client.get("/sessions?size=1").content)
    per_session = (listing - chrome) / (count - 1)
    assert chrome + per_session * bounds.SESSIONS.ceiling < PAGE_BYTES
    # Every session page at the defaults, and at the widest single list a URL can ask for —
    # a different shape rather than a larger one, and the shape the arithmetic above bounds.
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        for sizes in ({}, {"turns": 1, "chips": bounds.CHIPS.ceiling}):
            page = client.get(f"/session/{session_id}", params=sizes)
            assert page.status_code == 200, (session_id, sizes)
            assert len(page.content) < PAGE_BYTES, (session_id, sizes)


# One real URL per route the app exposes, keyed by the route's own path template. The sweep
# below reads this as a set, so a route added with no entry fails rather than going unswept.
ROUTES: dict[str, str] = {
    "/": "/",
    "/sessions": "/sessions",
    "/session/{session_id}": f"/session/{SPINE}",
    "/session/{session_id}/run/{run_id}": f"/session/{SPINE}/run/{SPINE_RUN}",
    "/fragment/turn/{session_id}/{source}/{turn_id}": (
        f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}"
    ),
    "/fragment/tools/{session_id}/{source}/{api_call_id}": (
        f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/text/{session_id}/{source}/{api_call_id}": (
        f"/fragment/text/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/thinking/{session_id}/{source}/{api_call_id}": (
        f"/fragment/thinking/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/tool/{session_id}/{source}/{tool_call_id}": (
        f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
    ),
    "/fragment/nav/{session_id}": f"/fragment/nav/{SPINE}",
    "/session/{session_id}/records/{source}": f"/session/{ANCESTOR}/records/main",
    "/fragment/record/{session_id}/{source}/{line_no}": f"/fragment/record/{ANCESTOR}/main/1",
    "/session/{session_id}/offload/{name:path}": f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}",
}


def test_every_route_the_viewer_exposes_is_in_the_payload_sweep(client: TestClient) -> None:
    """The sweep covers the routes the app has, not the ones someone remembered to list.

    Without this, a route shipped later is a page nothing weighs — and a route that selects
    a fat column is exactly the kind of thing that arrives quietly.
    """
    exposed = {
        route.path
        for route in client.app.routes  # pyrefly: ignore
        if isinstance(route, APIRoute)
    }
    assert exposed == set(ROUTES)


@pytest.mark.parametrize("path", sorted(ROUTES.values()))
def test_no_route_serves_more_than_the_page_ceiling(path: str, client: TestClient) -> None:
    """Every route answers under the ceiling at the production defaults — no size bound down.

    A smoke check rather than the proof: the fixture corpus is far smaller than a page, so
    what makes the bound hold is the fat-column scan and the page-size arithmetic above. What
    this catches is the route that ships a whole column anyway.
    """
    response = client.get(path)
    assert response.status_code == 200, path
    assert len(response.content) < PAGE_BYTES, path


def test_the_turn_fragment_pages_its_calls_and_partitions_them(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Following a turn's pages shows every call it made once, in order, with none skipped.

    Bound at two calls against the corpus's densest recorded turn, which holds four — so the
    page boundary is a real overflow of recorded data rather than a staged one.
    """
    indexes = [
        row[0]
        for row in store.execute(
            'SELECT "index" FROM live_api_calls'
            " WHERE session_id = ? AND source = 'main' AND turn_id = ? ORDER BY \"index\"",
            [ANCESTOR, DENSE_TURN],
        ).fetchall()
    ]
    assert len(indexes) == 4, "the densest fixture turn moved: re-pick DENSE_TURN"
    seen: list[str] = []
    after = -1
    # Two at a time, following the fragment's own continuation each round...
    for _ in range(4):
        html = client.get(
            f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}", params={"after": after, "calls": 2}
        ).text
        page = values(html, "data-call-index")
        assert len(page) <= 2
        seen += page
        following = values(html, "data-more-calls")
        if not following:
            break
        after = int(following[0])
    else:
        pytest.fail("the fragment never ran out of pages")
    # ...walks the turn's calls exactly once each, in index order.
    assert seen == [str(index) for index in indexes]
    # Keyset, never OFFSET: a page computed by counting rows re-reads what a page missed.
    assert "OFFSET" not in queries.load(Fragment.TURN_CALLS).upper()


def test_the_tool_cap_truncates_the_list_and_says_how_much_it_cut(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A call's tool rows are capped in SQL, and the indicator pages the rest rather than
    losing them.

    Bound at two tool rows against the recorded call that made four. The count in the
    indicator is what gives this leaf teeth: a cap that renders two rows without saying how
    many it dropped looks identical to a call that only made two.
    """
    (total,) = one(
        store,
        "SELECT count(*) FROM live_tool_calls"
        " WHERE session_id = ? AND source = ? AND api_call_id = ?",
        [FORK_ORIGIN, FORK_ORIGIN_RUN, DENSE_CALL],
    )
    assert total == 4, "the densest fixture call moved: re-pick DENSE_CALL"
    url = f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    first = client.get(url, params={"after": -1, "tools": 2}).text
    # Two rows, and the page says two more are behind the indicator...
    shown = values(first, "data-tool-call")
    assert len(shown) == 2
    assert fields(first, "data-more", DENSE_CALL)["count"] == "+2 more"
    # ...which fetches exactly those two, so the two pages partition the call's tools.
    after = int(values(first, "data-more-tools")[0])
    rest = values(client.get(url, params={"after": after, "tools": 2}).text, "data-tool-call")
    assert len(rest) == 2
    assert set(shown) & set(rest) == set()
    assert len(set(shown) | set(rest)) == total


def test_an_offload_of_nothing_but_escapes_still_serves_under_the_ceiling(
    plant: Planter,
) -> None:
    """The largest chunk anyone can ask for stays under the ceiling however the file escapes.

    Every other bound here rests on a measured cost per row. An offload can't: it holds a file
    a tool wrote, and a chunk of pure `&` weighs five times what the same chunk of prose does.
    The content is invented for exactly that reason — no recorded offload is adversarial, and
    the point of the leaf is the character no corpus happens to contain.
    """
    escapes = "&" * bounds.CHUNK.ceiling
    path = plant(
        ("UPDATE offload_files SET content = ? WHERE session_id = ?", [escapes, CONFIG_ONLY])
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(
            f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}", params={"size": bounds.CHUNK.ceiling}
        )
    assert page.status_code == 200
    # Served whole — the chunk is not silently cut — and still under the ceiling.
    assert page.text.count("&amp;") == bounds.CHUNK.ceiling
    assert len(page.content) < PAGE_BYTES


def test_a_turn_fragment_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """A call row and a tool row weigh no more than the ceiling's arithmetic gives them.

    The row costs behind that arithmetic were measured against the canonical store once, and a
    template that grows a row past them puts the ceiling out by the page size it multiplies.
    The content is planted `&` at both caps — the character that escapes to five bytes — for
    the same reason the offload leaf plants one: no recorded row is adversarial.
    """
    path = plant(
        ("UPDATE api_calls SET text = ? WHERE session_id = ?", ["&" * TEXT_CHARS, ANCESTOR]),
        ("UPDATE tool_calls SET input = ? WHERE session_id = ?", ["&" * INPUT_CHARS, FORK_ORIGIN]),
    )
    turn = f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}"
    tools = f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    with TestClient(build_app(path)) as planted:

        def served(url: str, **params: int) -> int:
            response = planted.get(url, params={"after": -1, **params})
            assert response.status_code == 200
            return len(response.content)

        # What one more call row costs on a fragment, and one more tool row — the marginal
        # cost, so the fragment's own chrome is not counted against a row's budget.
        call_row = served(turn, calls=2, tools=1) - served(turn, calls=1, tools=1)
        tool_rows = served(tools, tools=4) - served(tools, tools=1)
    assert call_row <= worst_call_bytes(1)
    assert tool_rows / 3 <= MEASURED_TOOL_ROW_MARKUP + INPUT_CHARS * ESCAPED_CHAR_BYTES


def test_a_session_timeline_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A turn row, a run row and a compaction row weigh no more than the arithmetic gives them.

    These are the costs the session page's ceiling is computed from, and a run row is the one
    the caps multiply two hundred times — so a template that grows one past its budget puts the
    ceiling out by that much. Every head is planted full of `&`, the character that escapes to
    five bytes, because no recorded row is adversarial. Two stores, because the marginal cost
    of a row is the same page with the row and without it.
    """
    (marks,) = one(store, "SELECT count(*) FROM compactions WHERE session_id = ?", [ANCESTOR])
    assert marks == 1, "the compaction fixture moved: re-pick the session this measures"
    heads_full = "&" * queries.CHIP_CHARS
    content: tuple[Statement, ...] = (
        # Both arms of the heading at their caps, so the row measured is the dearer of the
        # two: a turn that ran a slash command shows the command and its arguments instead
        # of the prompt, and that arm is the one `worst_heading_bytes` prices.
        (
            "UPDATE turns SET prompt = ?, command_name = ?, command_args = ? WHERE session_id = ?",
            [
                "&" * PROMPT_CHARS,
                "&" * COMMAND_NAME_CHARS,
                "&" * COMMAND_ARGS_CHARS,
                SPINE,
            ],
        ),
        (
            "UPDATE agent_runs SET agent_type = ?, description = ?, model = ? WHERE session_id = ?",
            [heads_full, heads_full, heads_full, SPINE],
        ),
        ("UPDATE compactions SET trigger = ? WHERE session_id = ?", [heads_full, ANCESTOR]),
        *DESCRIBED_AT_EVERY_CAP,
    )
    whole = enriched_plant(*content)
    # The same corpus less the run nested under `SPINE`'s one chipped turn, and less the
    # compaction `ANCESTOR` recorded — each of which a page above renders and this one does not.
    without = enriched_plant(
        *content,
        ("DELETE FROM agent_runs WHERE session_id = ? AND id = ?", [SPINE, SPINE_LEAF]),
        ("DELETE FROM compactions WHERE session_id = ?", [ANCESTOR]),
    )

    def served(path: Path, url: str, **params: int) -> int:
        with TestClient(build_app(path)) as planted:
            response = planted.get(url, params=params)
            assert response.status_code == 200, response.text[:200]
            return len(response.content)

    spine = f"/session/{SPINE}"
    # `SPINE`'s first two turns spawned nothing, so one more of them is a turn row and nothing
    # else. Its third spawned the pair, so the page holding that turn is where the rest is
    # measured: both runs, one run and the "+N more" line, and one run alone.
    turn_row = served(whole, spine, after=-1, turns=2, chips=1) - served(
        whole, spine, after=-1, turns=1, chips=1
    )
    pair = served(whole, spine, after=1, turns=1, chips=2)
    cut = served(whole, spine, after=1, turns=1, chips=1)
    alone = served(without, spine, after=1, turns=1, chips=2)
    mark_row = served(whole, f"/session/{ANCESTOR}") - served(without, f"/session/{ANCESTOR}")
    # A turn row costs its markup, its prompt head, and the "+N more" line it may carry...
    assert turn_row + (cut - alone) <= worst_turn_bytes()
    # ...a run row its markup and every head it shows, nested where a run row is dearest...
    assert pair - alone <= worst_chip_bytes()
    # ...and a compaction row its markup and its trigger.
    assert mark_row <= worst_mark_bytes()


def test_a_command_turns_heading_costs_one_arm_and_not_both(plant: Planter) -> None:
    """A turn holding both a full prompt and a full command shows one of them, not the sum.

    This is what `worst_heading_bytes` claims and nothing else checks: the macro renders the
    command *instead of* the prompt, so the two 300-character heads never meet on a page.
    Pricing their sum instead would put a full timeline about 13 KB over the ceiling — and
    a macro that rendered both would put a real page there. Planted `&` at all three caps,
    because no recorded turn is adversarial and redaction flattened the ones that exist.
    """
    path = plant(
        (
            "UPDATE turns SET prompt = ?, command_name = ?, command_args = ?"
            " WHERE session_id = ? AND source = ?",
            ["&" * PROMPT_CHARS, "&" * COMMAND_NAME_CHARS, "&" * COMMAND_ARGS_CHARS, SPINE, "main"],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}", params={"turns": 1}).text
    heading = re.search(r"<article id=\"turn-.*?<h2>(.*?)</h2>", page, re.S)
    assert heading is not None, "the planted page rendered no turn heading"
    # The command arm is on the line whole — both its heads, at the caps the digests cut to...
    escapes = heading.group(1).count("&amp;")
    assert escapes == COMMAND_NAME_CHARS + COMMAND_ARGS_CHARS
    # ...and the prompt is not on it as well, which is the half the arithmetic rests on: the
    # content of the heading weighs the arm it shows, not the two arms added together.
    assert escapes * ESCAPED_CHAR_BYTES == worst_heading_bytes()


# What the session arithmetic counts row by row, which chrome is the page without: a turn row
# with the runs and cut line it carries, a run list of the page's own, a compaction row. Cut
# innermost first, because a run list nests inside a run list.
COUNTED_ROWS = (
    r'<article id="turn-.*?</article>',
    r'<ul class="runs">.*?</ul>',
    r'<div class="compaction".*?</div>',
)


def chrome(html: str) -> str:
    """A session page with every row `worst_session_bytes` counts separately taken out."""
    for pattern in COUNTED_ROWS:
        while (stripped := re.sub(pattern, "", html, count=1, flags=re.S)) != html:
            html = stripped
    # The strip is the instrument, so it is checked both ways: a row left in is a cost counted
    # twice, and a wrapper taken out hides part of the page this measures.
    assert not values(html, "data-turn") and not values(html, "data-chip")
    assert not values(html, "data-compaction")
    assert 'id="session-header"' in html and 'id="timeline"' in html
    return html


def test_a_session_page_of_nothing_but_escapes_carries_the_chrome_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """What a session page holds outside its counted rows fits the allowance the ceiling gives it.

    The header is the part of a page no size a reader types bounds: every string in it is one a
    transcript wrote, and its two lists grow with the session — one recorded session opened 32
    PRs, which alone was 6 KB of a 6 KB allowance. So the query cuts all of them, and this
    measures what the cuts leave: `&` planted at every cap, on every session, because the widest
    chrome belongs to whichever page carries an unattached list, a "+N more" turn link and a cut
    compaction list as well as a header. The session's own enrichment sits in the header and is
    planted with it — two more capped strings, and the only place `friction` reaches a page.
    """
    head = "&" * queries.HEADER_CHARS
    # Distinct values that agree on their first `$item_chars` characters, which is what makes
    # them the worst case: the list cuts to `$item_chars` of `&` a member, five bytes apiece.
    item = "&" * queries.HEADER_ITEM_CHARS
    over = queries.HEADER_ITEMS + 2
    path = enriched_plant(
        (
            "UPDATE sessions SET title = ?, agent_name = ?, project_dir = ?, git_branch = ?,"
            " version = ?, entrypoint = ?",
            [head] * 6,
        ),
        # A skill rides an api call, so the plant clones a live one per session rather than
        # inventing a row: `live_api_calls` is the population the header's list counts.
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            " ? || i AS attribution_skill)"
            " FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c,"
            " range(1, ?) t(i))",
            [item, over + 1],
        ),
        (
            "INSERT INTO pr_links (SELECT s.id, 900000 + i, i, ? || i, 'planted/repo',"
            " '2026-01-01T00:00:00Z' FROM sessions s, range(1, ?) t(i))",
            [item, over + 1],
        ),
        # More compactions than a page renders, all before the first turn, so the line the
        # marks cap mints is part of the chrome this measures too.
        (
            "INSERT INTO compactions (SELECT 'planted-' || i, s.id, 'main',"
            " '1970-01-01T00:00:00Z', ?, 1, 1, 1 FROM sessions s, range(1, ?) t(i))",
            ["&" * queries.CHIP_CHARS, bounds.MARKS + 2],
        ),
        *DESCRIBED_AT_EVERY_CAP,
    )
    sessions = [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]
    with TestClient(build_app(path)) as planted:
        # Every session, at the defaults and at the widest single list a URL can ask for.
        pages = [
            planted.get(f"/session/{session_id}", params=sizes).text
            for session_id in sessions
            for sizes in ({}, {"turns": 1, "chips": bounds.CHIPS.ceiling})
        ]
    widest = max((chrome(page) for page in pages), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_SESSION_CHROME
    # The plant reached every cap on the page that measured worst, which is what makes the
    # number a worst case: each string cut to its head, each list cut to its first members and
    # saying how many it left, and the compaction cap's own line rendered.
    facts = fields(widest, "id", "session-header")
    assert len(facts["title"]) == len(facts["git_branch"]) == queries.HEADER_CHARS
    assert facts["skills"].count(item) == queries.HEADER_ITEMS
    assert facts["skills"].endswith("more") and facts["prs_cut"].startswith("and")
    assert values(widest, "data-pr") == [item.replace("&", "&amp;")] * queries.HEADER_ITEMS
    assert values(widest, "data-more-marks") != []
    # The enrichment reached its own two caps as well, stale tag and all — the header holds a
    # session's description and its friction, so both are part of what this measures.
    described = fields(widest, "data-enrichment", values(widest, "data-enrichment")[0])
    assert len(described["description"]) == len(described["friction"]) == queries.ENRICHMENT_CHARS
    assert described["stale"] == "stale"


# What the map's arithmetic counts node by node, which chrome is the map without: the list of
# nodes, and the list a node's children sit in. One pattern, because a node list nests inside a
# node list and the unattached tail is one more of them.
NAV_NODES = r'<ul class="nav">.*?</ul>'


def map_chrome(html: str) -> str:
    """A session's map with every node `worst_nav_node_bytes` counts separately taken out."""
    while (stripped := re.sub(NAV_NODES, "", html, count=1, flags=re.S)) != html:
        html = stripped
    # The strip is the instrument, so it is checked both ways: a node left in is a cost counted
    # twice, and the map taken out whole hides what this measures.
    assert not values(html, "data-nav") and "<nav" in html
    return html


def test_a_session_map_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A node of the map beside a session page weighs no more than the arithmetic gives it.

    The map is its own response and spends a ceiling of its own, so a template that grows a
    node past its budget puts that ceiling out `bounds.NAV.ceiling` times over. Every cap a
    label reads is planted full of `&` — the character that escapes to five bytes — because no
    recorded label is adversarial: what a pass wrote, which is the arm a described store shows,
    and the command and prompt an undescribed one falls back to. `SPINE` is the session that
    measures it because its forest nests: a run under a turn, and a run under that run.
    """
    heads_full = "&" * queries.CHIP_CHARS
    path = enriched_plant(
        (
            "UPDATE turns SET prompt = ?, command_name = ?, command_args = ? WHERE session_id = ?",
            ["&" * PROMPT_CHARS, "&" * COMMAND_NAME_CHARS, "&" * COMMAND_ARGS_CHARS, SPINE],
        ),
        (
            "UPDATE agent_runs SET agent_type = ?, description = ? WHERE session_id = ?",
            [heads_full, heads_full, SPINE],
        ),
        # Every call but one loses its price, so each node renders the mark a total missing
        # calls carries — and the one left priced keeps the session's whole above zero, so the
        # shares beside those marks print as percentages rather than as the gap a zero whole is.
        (
            "UPDATE api_calls SET cost_usd = NULL WHERE session_id = ?"
            " AND id != (SELECT max(id) FROM api_calls WHERE session_id = ?)",
            [SPINE, SPINE],
        ),
        *DESCRIBED_AT_EVERY_CAP,
    )
    with TestClient(build_app(path)) as planted:

        def served(nodes: int) -> str:
            response = planted.get(f"/fragment/nav/{SPINE}", params={"nodes": nodes})
            assert response.status_code == 200, response.text[:200]
            return response.text

        # `SPINE`'s first three nodes are turns, so one more of them is a turn node and
        # nothing else. Its third turn spawned a run, and that run spawned another: the fifth
        # node is the nested kind, which brings the `<details>` its siblings fold into.
        pages = {nodes: served(nodes) for nodes in (2, 3, 4, 5)}
        # The chrome is measured across every session rather than on `SPINE` alone: the widest
        # belongs to whichever map carries a heading over the runs no turn holds as well as a
        # cut tail, and `SPINE`'s forest hangs entirely off its turns.
        sessions = [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]
        maps = [
            planted.get(f"/fragment/nav/{session_id}", params={"nodes": 2}).text
            for session_id in sessions
        ]
    weights = {nodes: len(page.encode()) for nodes, page in pages.items()}
    # A turn node costs its markup and a label of nothing but `&`...
    assert weights[3] - weights[2] <= worst_nav_node_bytes()
    # ...and a run node costs no more, measured where a run node is dearest.
    assert weights[5] - weights[4] <= worst_nav_node_bytes()
    # ...and what the map carries whatever it holds fits the allowance the ceiling gives it.
    widest = max((map_chrome(one_map) for one_map in maps), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_NAV_CHROME
    # The plant reached the cap the map cuts a label to, which is what makes those numbers a
    # worst case, and the cap bit and said by how much — a map that renders two nodes without
    # saying how many it left out looks identical to a session of two.
    labels = re.findall(r'<span data-field="label">(.*?)</span>', pages[2])
    assert {label.count("&amp;") for label in labels} == {queries.NAV_CHARS}
    assert fields(pages[2], "id", "nav-more")["cut"] == "4"


def test_a_session_list_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A list row and the chrome around it weigh no more than the arithmetic gives them.

    The list is the page a corpus grows: every string in a row is one a transcript wrote, and
    its skills and the filter box's project suggestions both grow with what the store holds.
    So `&` is planted at every cap — the character that escapes to five bytes — and both halves
    of the ceiling are measured against it: one more row, and the page with no rows at all. The
    described store rather than the bare one, because a row of a store a pass has run over
    carries what the pass said as well, and that is the row the ceiling has to budget.
    """
    head = "&" * queries.LIST_CHARS
    name = "&" * queries.LIST_ITEM_CHARS
    kind = "&" * queries.TAG_CHARS
    over = queries.LIST_ITEMS + 2
    kinds = queries.LIST_CATEGORIES + 2
    path = enriched_plant(
        # A project path per session, each one the longest the filter box offers, so the box
        # has more suggestions than it shows. The two digits that tell them apart are the only
        # characters on the page that are not an escape...
        (
            "UPDATE sessions SET title = ?, project_dir = ? || printf('%02d', r.n)"
            " FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM sessions) r"
            " WHERE r.id = sessions.id",
            [head, head[:-2]],
        ),
        # ...and every session runs more skills than a row shows, cloning a live api call rather
        # than inventing one: `live_api_calls` is the population a row's skill list counts.
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            " ? || i AS attribution_skill)"
            " FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c,"
            " range(1, ?) t(i))",
            [name, over + 1],
        ),
        # ...and every session spawns more kinds of subagent than a row shows. The names have
        # to differ inside the head the query cuts them to, because it groups the runs after
        # the cut: two types sharing a head are one name's worth of bytes and not two. Two
        # digits tell them apart, so 18 of every 20 characters are still escapes.
        (
            "INSERT INTO agent_runs (SELECT r.* REPLACE (s.id AS session_id,"
            " s.id || '-planted-' || i AS id, ? || printf('%02d', i) AS agent_type)"
            " FROM (SELECT * FROM live_agent_runs ORDER BY session_id, id LIMIT 1) r,"
            " sessions s, range(1, ?) t(i))",
            [name[:-2], over + 1],
        ),
        *DESCRIBED_AT_EVERY_CAP,
        # The pass described every turn as the same kind of work, so the Work cell would show
        # one name; a described turn per session per kind fills it the way a real pass would,
        # cloning a described row rather than inventing one. Categories are cut and grouped
        # like the agent types, so they are told apart the same way.
        (
            "INSERT INTO turn_enrichments (SELECT e.* REPLACE (s.id AS session_id,"
            " s.id || '-planted-' || i AS turn_id, ? || printf('%02d', i) AS category)"
            " FROM (SELECT * FROM turn_enrichments ORDER BY session_id, turn_id LIMIT 1) e,"
            " sessions s, range(1, ?) t(i))",
            [kind[:-2], kinds + 1],
        ),
    )
    (sessions,) = one(store, "SELECT count(*) FROM sessions")
    assert sessions > queries.LIST_PROJECTS, "the fixture corpus no longer fills the filter box"
    with TestClient(build_app(path)) as planted:

        def served(size: int) -> str:
            response = planted.get("/sessions", params={"size": size})
            assert response.status_code == 200, response.text[:200]
            return response.text

        pages = [served(size) for size in range(1, sessions + 1)]
    one_row = pages[0]
    # One more row costs its markup and every head it shows, all `&` — priced at every row the
    # list holds rather than at whichever one lands second, because the ceiling multiplies the
    # dearest row and which session that is depends only on how the list happens to be sorted.
    weights = [len(page.encode()) for page in pages]
    assert max(b - a for a, b in pairwise(weights)) <= worst_session_row_bytes()
    # ...and what the page carries whatever its size fits the allowance the ceiling gives it,
    # with the row the arithmetic counts separately stripped out.
    chrome = re.sub(r"<tr data-session-id=.*?</tr>", "", one_row, flags=re.S)
    assert not values(chrome, "data-session-id") and 'id="sessions"' in chrome
    assert len(chrome.encode()) <= MEASURED_LIST_CHROME
    # The plant reached every cap, which is what makes those two numbers a worst case: each
    # string cut to its head, the skills cut to their first names and saying how many were
    # left, and the filter box offering as many projects as it has room for. Read off the row
    # the budget above is priced at — the dearest one — rather than off whichever sorted first.
    markup = re.findall(r"<tr data-session-id=.*?</tr>", pages[-1], flags=re.S)
    dearest = max(markup, key=lambda one: len(one.encode()))
    row = fields(dearest, "data-session-id", values(dearest, "data-session-id")[0])
    assert len(row["title"]) == len(row["project_dir"]) == queries.LIST_CHARS
    assert row["skills"].count(name) == queries.LIST_ITEMS
    assert row["skills"].endswith("more")
    # The two counted lists reached their own caps, each name cut to the head it is grouped
    # under — the last two characters of one are the digits that tell the plants apart.
    assert row["agent_types"].count(name[:-2]) == queries.LIST_ITEMS
    assert row["work"].count(kind[:-2]) == queries.LIST_CATEGORIES
    assert row["agent_types"].endswith("more") and row["work"].endswith("more")
    options = re.findall(r'<option value="([^"]*)"', one_row)
    assert len(options) == queries.LIST_PROJECTS
    # And the pass's own line reached the head the list cuts it to, with both tags beside it —
    # the whole description is on the session's page, which is a page ceiling of its own.
    assert len(row["description"]) == queries.LIST_CHARS
    assert len(row["category"]) == len(row["outcome"]) == queries.TAG_CHARS
    assert "stale" not in row


def test_a_projects_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """The landing page at its ceiling weighs no more than the arithmetic gives it.

    A project path is a directory someone named, so `&` is planted at the cap the page shows —
    the character that escapes to five bytes in a cell and to twelve in the link beside it —
    and the store is filled past the page's own ceiling. That is the page the arithmetic
    bounds and the one no corpus recorded so far comes near: the fixtures hold four projects.
    """
    over = bounds.PROJECTS.ceiling + 20
    # Three digits tell the paths apart inside the head the page shows, so 97 of every 100
    # characters are escapes — and no path is a prefix of another, so none folds into another's
    # row. The sessions are clones of a recorded one rather than invented rows.
    head = "&" * (queries.LIST_CHARS - 3)
    path = plant(
        (
            "INSERT INTO sessions (SELECT s.* REPLACE (s.id || '-planted-' || i AS id,"
            " ? || printf('%03d', i) AS project_dir) FROM (SELECT * FROM sessions LIMIT 1) s,"
            " range(1, ?) t(i))",
            [head, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        response = planted.get("/")
    assert response.status_code == 200, response.text[:200]
    page = response.text
    # A page a reader lands on stays under the ceiling with every path at its cap...
    assert len(response.content) < PAGE_BYTES
    shown = values(page, "data-project")
    assert len(shown) == bounds.PROJECTS.ceiling
    # ...the planted ones at the cap, and the corpus's own short paths beside them. The
    # attribute is read back through the escaping the page wrote it with, which is the point:
    # every character of a planted path is one of the five-byte ones.
    widest = unescape(max(shown, key=len))
    assert len(fields(page, "data-project", widest)["project_dir"]) == queries.LIST_CHARS
    # ...each row linking by the whole path it shows, which is what the encoded head budgets...
    assert inside(page, "data-project", widest, "href") == [
        f"/sessions?sort=started_at&direction=desc&project={quote(widest, safe='')}"
    ]
    # ...and what it left out said rather than dropped. Every planted path is a root of its
    # own, so the store's distinct directories are the rows the page had to choose between.
    with duckdb.connect(str(path), read_only=True) as connection:
        (projects,) = one(
            connection, "SELECT count(DISTINCT coalesce(project_dir, '')) FROM sessions"
        )
    assert values(page, "data-more-projects") == [str(projects - bounds.PROJECTS.ceiling)]
    # What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    # rows the arithmetic counts separately stripped out...
    chrome = re.sub(r"<tr data-project=.*?</tr>", "", page, flags=re.S)
    assert not values(chrome, "data-project") and 'id="projects"' in chrome
    assert len(chrome.encode()) <= MEASURED_PROJECTS_CHROME
    # ...and one row costs no more than its markup and the two copies of its path.
    row_bytes = (len(page.encode()) - len(chrome.encode())) / bounds.PROJECTS.ceiling
    assert row_bytes <= worst_project_row_bytes()


def test_the_digest_rows_no_window_reaches_are_capped_at_what_a_page_budgets(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """The rows that ride the last page outside its window are bounded, not counted afterwards.

    A digest row with no turn index cannot be windowed, so it arrives on the last page
    whatever `turns` a reader asked for — which is why the arithmetic above budgets
    `bounds.CURSORLESS_TURNS` turn rows on top of the size the route admits. `RESUME` answers turns
    that live in the session it resumed, so every one of its api calls is unattributed and
    its digest carries exactly this row. The cap is bound down to zero to reach a boundary no
    recorded digest crosses: more of these rows than the ceiling budgets raises rather than
    riding a page nothing counted them on.
    """
    rows = cursorless_rows(
        store, Page.TIMELINE, TURN_CURSOR, bounds.CURSORLESS_TURNS, session_id=RESUME
    )
    assert [row["turn_id"] for row in rows] == [queries.UNATTRIBUTED]
    with pytest.raises(ValueError, match="more than 0"):
        cursorless_rows(store, Page.TIMELINE, TURN_CURSOR, 0, session_id=RESUME)


def test_a_deeply_nested_value_is_served_at_the_size_it_was_stored(plant: Planter) -> None:
    """A per-value fetch serves the value it names, not what indenting could turn it into.

    Indenting is the one thing that can break the per-value exemption above, because it is
    quadratic in nesting: 10 KB of nothing but `[` indents to 50 MB, and past the parser's
    own stack the fragment answered 500 rather than anything at all. Both values are invented
    and have to be — nothing recorded nests remotely this deep, which is the point.
    """
    indents_huge = "[" * 5_000 + "]" * 5_000
    overflows_the_parser = "[" * 10_000 + "]" * 10_000
    path = plant(
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
            [indents_huge, indents_huge, FORK_ORIGIN],
        ),
        ("UPDATE raw_records SET raw = ? WHERE session_id = ?", [overflows_the_parser, ANCESTOR]),
    )
    with TestClient(build_app(path)) as planted:
        tool = planted.get(f"/fragment/tool/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}")
        record = planted.get(f"/fragment/record/{ANCESTOR}/main/1")
    # Each fragment answers, and weighs the values it names plus a page of chrome at most.
    for response, stored in ((tool, 2 * len(indents_huge)), (record, len(overflows_the_parser))):
        assert response.status_code == 200
        assert len(response.content) < stored + PAGE_BYTES


def test_a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every preview is truncated before it reaches a page, so no one huge value can bloat it.

    The previews the viewer renders — a session's title and project on the list, a turn's
    prompt or the command it ran on the session page, a run's own description, definition and
    model on its page, an api call's text and model and a tool call's arguments in the turn
    fragment — checked at once against one planted store. The
    oversized values are invented: redaction flattened every recorded string to a few
    characters, so no fixture reaches a cap.
    """
    # One turn of each kind, because a heading shows one arm or the other: a plain turn's
    # prompt, and a slash turn's arguments.
    turn_id, _ = one(
        store,
        "SELECT id, \"index\" FROM turns WHERE session_id = ? AND source = 'main'"
        ' AND command_name IS NULL ORDER BY "index"',
        [SPINE],
    )
    command_id, _ = one(
        store,
        "SELECT id, \"index\" FROM turns WHERE session_id = ? AND source = 'main'"
        ' AND command_name IS NOT NULL ORDER BY "index"',
        [SPINE],
    )
    # Each value is planted well past its own cap, onto the real row a fixture recorded...
    long = "x" * 5_000
    path: Path = plant(
        ("UPDATE sessions SET title = ?, project_dir = ? WHERE id = ?", [long, long, SPINE]),
        ("UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?", [long, SPINE, turn_id]),
        (
            "UPDATE turns SET command_name = ?, command_args = ? WHERE session_id = ? AND id = ?",
            [long, long, SPINE, command_id],
        ),
        (
            "UPDATE agent_runs SET description = ?, agent_type = ?, model = ? WHERE session_id = ?",
            [long, long, long, SPINE],
        ),
        ("UPDATE api_calls SET text = ?, model = ? WHERE session_id = ?", [long, long, ANCESTOR]),
        ("UPDATE tool_calls SET input = ? WHERE session_id = ?", [long, FORK_ORIGIN]),
    )
    with TestClient(build_app(path)) as planted:
        listing = planted.get("/sessions").text
        page = planted.get(f"/session/{SPINE}").text
        run = planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
        turn = planted.get(f"/fragment/turn/{ANCESTOR}/main/{DENSE_TURN}").text
        tools = planted.get(f"/fragment/tools/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}").text
    # ...and what each of them shows is its cap, not the value. The list's cuts are the
    # viewer's own composition rather than its query's, because its filters read the whole
    # values — a project path cut to a head would match no session under a longer one.
    row = fields(listing, "data-session-id", SPINE)
    assert len(row["title"]) == len(row["project_dir"]) == queries.LIST_CHARS
    # A path too long for the filter box to suggest whole is left out of it rather than cut:
    # half a path fills the filter in with a value that matches nothing.
    assert not [path for path in re.findall(r'<option value="([^"]*)"', listing) if "x" in path]
    assert len(fields(page, "data-turn", turn_id)["prompt"]) == PROMPT_CHARS
    # A slash turn's heading is cut the same way, in both the columns it reads.
    command = fields(page, "data-turn", command_id)
    assert len(command["command_name"]) == COMMAND_NAME_CHARS
    assert len(command["command_args"]) == COMMAND_ARGS_CHARS
    # A run page shows one run's strings and links to the rest, so the heads it shows are a
    # header's rather than a chip's — the definition it ran under and its model with the
    # description, because a page has no more idea what those hold than what a prompt does.
    header = fields(run, "id", "run-header")
    assert {len(header[field]) for field in ("description", "agent_type", "model")} == {
        queries.HEADER_CHARS
    }
    # A call row is one of ten on a fragment, so its model is cut where a chip's strings are.
    call = fields(turn, "data-api-call", DENSE_TURN_CALL)
    assert len(call["text_head"]) == TEXT_CHARS
    assert len(call["model"]) == queries.CHIP_CHARS
    assert len(fields(tools, "data-tool-call", DENSE_TOOL)["input_head"]) == INPUT_CHARS
