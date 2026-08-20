"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Three mechanisms, checked separately: the queries behind the pages and fragments never select
an unbounded fat column, what they do select is truncated in SQL rather than in the template,
and every page size is a bound parameter whose production default is pinned here. Together
they are what makes the bound hold by construction rather than by the fixture corpus's luck —
a per-value fetch is the one exception, and it is exempt because its unit *is* one value.
"""

import re
from collections.abc import Iterator
from html import unescape
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from markupsafe import escape

from aiobserve.analyze import queries
from aiobserve.analyze.queries import QUERIES, VIEW_PREFIX
from aiobserve.view import bounds, nodes
from aiobserve.view.app import QUERY_URL, build_app, knobs
from aiobserve.view.format import ELLIPSIS
from aiobserve.view.listing import SHOWN
from aiobserve.view.store import TURN_CURSOR, Fragment, Page, Value, cursorless_rows
from tests.conftest import (
    ANCESTOR,
    COMPACTED,
    COMPACTED_BOUNDARY,
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
    SPINE_RUN,
)
from tests.view.conftest import (
    Planter,
    Statement,
    block,
    fields,
    inside,
    one,
    pages,
    values,
)

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
# What a node page may weigh, which is its own budget rather than the one above. The tree is
# `DEPTH` levels of `KIN` children, so the window a level opens on prices four fifths of the
# page — and it is a window, not a limit: a tail row fetches what it left out and stands the
# rows in its own place, without a page boundary anywhere. Widening the window is a reader
# reaching further per click, and pinning it here rather than against `PAGE_BYTES` keeps that
# choice off the list pages, whose ceilings are derived against the number above. The
# arithmetic under it — `worst_node_bytes`, at every ceiling at once — comes to 978,866 B
# today, and the leaf at the bottom of this file is what keeps that true. Raised from 900,000
# when the children log went from twelve rows to a hundred: 88 more log rows at 1,654 B each
# is 145,552 B of page, which buys a reader a level of a hundred read in one go.
NODE_BYTES = 1_050_000
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
# The same two for the page that lists where a session failed, whose row is a link to the
# failed tool call's own page, the thread it ran on and a timestamp. Measured through the app
# by the leaf at the bottom of this file, every label planted full of `&` and the session
# failing more calls than the page shows: 620 B a row, of which 240 B is a planted label,
# leaving 380 B of the link and the two cells after it — and 2,339 B of chrome, which is small
# for the same reason the landing page's is: no form, no pager and no suggestions.
MEASURED_ERROR_ROW_MARKUP = 400
MEASURED_ERRORS_CHROME = 2_500

# How much of a turn's prompt a digest shows, from `session_digest`'s own `substr`, and the
# same two for what a slash-command turn shows instead: the name of the command, and what was
# typed after it. A command is named by whoever wrote the file that defines it, so the cut is
# in SQL like everything else rather than assumed short. All three reach a page through a
# children log's turn row, which cuts them again to the width of a line.
PROMPT_CHARS = 300
COMMAND_NAME_CHARS = 60
COMMAND_ARGS_CHARS = 300
# What a row of the records browser really costs — the preview plus the row's own markup, most
# of it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` on
# 2026-08-08: 83,659 B for a 100-record page less 1,865 B of chrome, over the 99 rows between.
# The fixture records are redacted to a few characters, so they project nothing about this.
MEASURED_RECORD_BYTES = 826

# What the markup around one row of the pane's children log costs, with the label and the one
# string it carries taken off: three copies of the node's URL — the link, the `hx-get` behind
# it, and the mount its expansion opens through — the swap the link performs, the numbers that
# tell two children apart, and the row around them. Re-measured through the app by the leaf at
# the bottom of this file, every cap full of `&` and every knob at its longest — 1,637 B, of
# which 540 B is content at those caps and 114 B the knobs, leaving 983 B.
MEASURED_LOG_ROW_MARKUP = 1_000
# What the control under a children log costs, with both of its links rendered: the nav around
# them, the place between them, and two copies of the node's own URL carrying the page's knobs
# and a page number. Nearly all of it is those two URLs. Measured through the app by the leaf at
# the bottom of this file, on logs driven to one row a page and read at a middle page, which is
# the only page carrying both links — 533 B, the widest of the 30 that sweep renders, 20 of them
# with both links. Driving the log to one row a page is also what writes `log=1` into the suffix
# on both of those URLs, where `worst_knob_bytes()` prices two digits: the worst pager is 2 B
# wider than what was measured, inside the 67 B this leaves over it.
MEASURED_PAGER_BYTES = 600
# And what the markup around one crumb of the chain down to the selection costs: the link, the
# node's key, and the glyph that says who named it. Measured the same way: 537 B less 240 B of
# label and 38 B of knobs, leaving 259 B.
MEASURED_CRUMB_MARKUP = 280
# And what the markup around one previewed value costs — the heading, the `<pre>` and the line
# offering the rest of it — with the preview itself taken off.
MEASURED_DETAIL_MARKUP = 600
# How many fat values one pane previews at once. Two is the most any kind shows: an api call
# previews what it said and what it thought, and a tool call what it was passed and what came
# back. A third would be a kind whose pane the arithmetic below has not priced.
PANE_DETAILS = 2
# What a node page carries outside its tree rows, its log rows and its previews: the crumbs
# down to the selection, the node's own facts, and what a pass said about it. The session is
# the widest of the eight panes — every string in its header is one a transcript wrote, and its
# two lists grow with the session — so the allowance is a session header's, cut in SQL.
# The preset switcher rides here too, three links carrying the node's own URL, and — on a pane
# reading a failed tool call — the step to the failure before it and the one after.
# Re-measured through the app by the leaf at the bottom of this file at 15,666 B.
MEASURED_NODE_CHROME = 16_000

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


def worst_error_row_bytes() -> int:
    """What one row of a session's errors list can weigh: its markup, and a label of `&`.

    A row is a link to the failed tool call, labelled the way a tree row labels it — the tool's
    name and the head of what it was passed, cut to one width between them — beside the thread
    it ran on and the clock. The thread is an agent id the store minted, and the timestamp is
    as long as its type allows; only the label is text a transcript wrote.
    """
    return MEASURED_ERROR_ROW_MARKUP + queries.NAV_CHARS * ESCAPED_CHAR_BYTES


def worst_tag_bytes() -> int:
    """What the taxonomy tags beside a described item can weigh, all `&`.

    Two of them — category and outcome — and the third says the row is stale, which is words
    of ours rather than of the store's and rides in the markup measured above.
    """
    return 2 * queries.TAG_CHARS * ESCAPED_CHAR_BYTES


def worst_knob_bytes() -> int:
    """What the sizes a URL carries add to one link on the page it serves.

    Every link a node page writes repeats the knobs the request was made with, so a reader who
    narrows a page pays for the query string on every row of it. The longest one leaves `?kin=`
    at its default — a narrower tree costs a whole level of rows to save a byte a link — and
    takes the longest preset name beside the widest sizes that are not defaults. Escaped,
    because the `&` between two of them is written into an attribute.
    """
    marks = knobs(
        max(nodes.Preset, key=len),
        bounds.KIN.default,
        bounds.LOG.ceiling - 1,
        bounds.DETAIL.ceiling - 1,
    )
    return len(escape(marks).encode())


def worst_log_row_bytes() -> int:
    """What one row of the pane's children log can weigh: its markup, its label, and its one
    string.

    A log row is a link plus the numbers that tell two children apart, and at most one of
    those is a string the store wrote — the model a call ran on, the tool a call called, the
    definition a run ran. All of them are cut where a chip's strings are.
    """
    return (
        MEASURED_LOG_ROW_MARKUP
        + (queries.NAV_CHARS + queries.CHIP_CHARS) * ESCAPED_CHAR_BYTES
        # A row links where it fetches and mounts where it expands, so it carries the knobs
        # three times.
        + 3 * worst_knob_bytes()
    )


def worst_crumb_bytes() -> int:
    """What one crumb of the chain above a node can weigh: its markup, a label of `&`, and the
    knobs its link carries once."""
    return MEASURED_CRUMB_MARKUP + queries.NAV_CHARS * ESCAPED_CHAR_BYTES + worst_knob_bytes()


def worst_detail_bytes() -> int:
    """What one previewed value can weigh: its markup, and a preview of nothing but `&`."""
    return MEASURED_DETAIL_MARKUP + bounds.DETAIL.ceiling * ESCAPED_CHAR_BYTES


def worst_node_bytes() -> int:
    """The largest node page any sizes a URL can carry produce.

    A page is its chrome, the crumbs down to the selection, the tree beside it, the values the
    pane previews, and the log under it. The tree is the part that multiplies: every level of
    the open path admits `KIN` children and a tail row saying what the cap left out, and the
    path runs `DEPTH` levels deep — so `bounds.TREE_ROW_BYTES` is four fifths of the ceiling,
    and the row is pinned rather than budgeted.

    `KIN` children per level is the whole of it: `tree.windowed` keeps the child the path
    descends through *inside* the window rather than past it, and `test_tree.py` pins that. A
    rescue that added a row would put a level at `KIN + 1` and this page 16 rows over what it
    prices.

    The sizes' own defaults spend it, and each of the three knobs only goes down from there —
    but a knob a reader turns down writes itself into every link on the page, so the rows are
    priced with the longest query string one can carry rather than with none.
    """
    tree_rows = 1 + bounds.DEPTH * (bounds.KIN.ceiling + 1)
    return (
        MEASURED_NODE_CHROME
        + bounds.DEPTH * worst_crumb_bytes()
        + tree_rows * bounds.TREE_ROW_BYTES
        + bounds.LOG.ceiling * worst_log_row_bytes()
        + MEASURED_PAGER_BYTES
        + PANE_DETAILS * worst_detail_bytes()
    )


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

# What a query may wrap a fat column in and still be bounded: a fixed-width prefix of it, a
# count of what it holds, or the check that it parses. Anything else puts the whole value on
# the page. Read at any depth — `substr(coalesce(json_extract_string(input, …), …), 1, $n)`
# is a cut of whatever it wraps, so what a bounding call opens is exempt to its close.
BOUNDING = ("substr", "length", "json_valid")


def _named(sql: str) -> Iterator[str]:
    """Every word a statement names outside a bounding call, however deeply they nest."""
    # Whether each open bracket opened a bounding call, and how many of those are still open.
    opened: list[bool] = []
    bounding = 0
    word = ""
    for token in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|\(|\)", sql):
        found = token.group()
        if found == "(":
            opened.append(word in BOUNDING)
            bounding += opened[-1]
        elif found == ")":
            bounding -= opened.pop() if opened else 0
        elif not bounding:
            yield found
        word = found.lower() if found != ")" else ""


def unbounded(sql: str) -> set[str]:
    """The fat columns a statement selects outside a bounding call — what a page can't afford.

    An output name is not a selected column, so `AS` and what follows it comes out first: a
    cut column keeps the name of the column it cuts, and the cut is what the page shows. A
    quoted string is not a column either — `'$.description'` names a key inside a value.
    """
    without_comments = re.sub(r"--[^\n]*", " ", sql)
    without_strings = re.sub(r"'[^']*'", " ", without_comments)
    named = re.sub(r"\bAS\s+[A-Za-z_][A-Za-z0-9_]*", " ", without_strings, flags=re.IGNORECASE)
    return {word for word in _named(named) if word in FAT}


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
    # A cut of what a call read out of a fat column is a cut, however deep the call nests...
    parsed = "json_extract_string(t.input, '$.file_path')"
    assert (
        unbounded(f"SELECT substr(coalesce({parsed}, t.input), 1, 200) AS head FROM tools t")
        == set()
    )
    # ...and the check that a value parses hands back a flag rather than the value.
    assert unbounded("SELECT json_valid(t.input) AS ok FROM tools t") == set()
    # A key inside a JSON path is a string, not the column that happens to share its name...
    assert (
        unbounded("SELECT substr(json_extract_string(t.input, '$.description'), 1, 9) AS a FROM t")
        == set()
    )
    # ...while a fat column read by a call that is not a cut is the whole value on the page.
    assert unbounded(f"SELECT {parsed} AS path FROM tools t") == {"input"}
    assert unbounded("SELECT coalesce(substr(t.input, 1, 9), t.result) AS head FROM tools t") == {
        "result"
    }


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
    section would pass against any defaults at all — a `page_records` of 5,000 would break the
    bound in production while CI stayed green.
    """
    assert QUERIES["view_records"].params["page_records"].default == 100
    assert QUERIES["view_records"].params["preview_chars"].default == 160
    assert QUERIES["view_offload"].params["chunk_chars"].default == 50_000
    # How much of a label a row of the tree shows. Short by design: a tree row is a line in a
    # sidebar rather than a row of a table, and the tree is the one list whose rows a reader
    # sees all of. Every level cuts to the same width, whatever kind of child it holds.
    for level in ("view_tree_turns", "view_tree_calls", "view_tree_tools"):
        assert QUERIES[level].params["nav_chars"].default == 48, level
    # And how much of each string a row of the pane's children log shows, with the page it is
    # read in. Wider than a tree row: a log row is a line of a table, with room for the first
    # words of a prompt beside the numbers.
    assert QUERIES["view_turn_calls"].params["log_chars"].default == 300
    assert QUERIES["view_call_tools"].params["log_chars"].default == 300
    assert QUERIES["view_turn_calls"].params["page_calls"].default == queries.LOG_ROWS
    assert QUERIES["view_call_tools"].params["page_tools"].default == queries.LOG_ROWS
    assert queries.LOG_ROWS == 100
    # A node header cuts every string it carries to a head, and the one fat value its pane
    # previews to a detail — the four kinds that have fields of their own take the same two.
    for header in ("view_turn_header", "view_call_header", "view_tool_header", "view_run_header"):
        assert QUERIES[header].params["head_chars"].default == 100, header
        assert QUERIES[header].params["detail_chars"].default == 4_000, header
    # The session header is the widest of the panes: two of its columns are lists that grow
    # with the session, so it cuts the members and caps how many it shows.
    assert QUERIES["view_session_header"].params["head_chars"].default == 100
    assert QUERIES["view_session_header"].params["item_chars"].default == 60
    assert QUERIES["view_session_header"].params["head_items"].default == 5
    # How much of a run row's and a compaction row's three columns a tree row shows. Both
    # queries keep no LIMIT of their own — a report quotes the whole set — so what bounds them
    # on a page is the tree's own arithmetic below.
    assert QUERIES["view_runs"].params["chip_chars"].default == 60
    assert QUERIES["view_compactions"].params["chip_chars"].default == 60
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
    # And the errors list, bound the same way — a session can fail arbitrarily many calls —
    # and labelled at a tree row's width, because each of its rows leads to a node.
    assert QUERIES["view_session_errors"].params["nav_chars"].default == queries.NAV_CHARS
    assert QUERIES["view_session_errors"].params["errors"].default == 100
    # Every ceiling is projected at the largest page a URL can ask for, because a size is
    # something a reader types.
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
    # And a session's errors list, which grows the way both of those do — nothing about a
    # session caps how often its tools fail — and is not a size a URL carries either: a reader
    # jumps to a failure rather than paging through them.
    assert MEASURED_ERRORS_CHROME + bounds.ERRORS.ceiling * worst_error_row_bytes() < PAGE_BYTES
    # And the node page, the one page every node URL serves: the tree a reader walks down the
    # left, and the pane beside it. Its three sizes are each their own ceiling, so this is the
    # widest response any node URL can be asked for.
    assert worst_node_bytes() < NODE_BYTES
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
        "KIN",
        "LOG",
        "DETAIL",
        "RECORDS",
        "CHUNK",
        "SESSIONS",
        "PROJECTS",
        "ERRORS",
    }
    # The same for the bounds that are not sizes a URL carries: how deep a chain opens, how
    # many turn rows no cursor reaches, how much of a string a log row shows, how long a value
    # is marked up in its own syntax, and what one row of the tree may weigh.
    assert {name for name, value in vars(bounds).items() if isinstance(value, int)} == {
        "DEPTH",
        "CURSORLESS_TURNS",
        "LOG_CHARS",
        "HIGHLIGHT_CHARS",
        "TREE_ROW_BYTES",
    }


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
    # And every session's own node page, which is the widest of the eight the tree opens on:
    # the whole main thread is under the selection. A node page's three sizes are each their
    # own ceiling, so the defaults are also the largest response a URL can ask for.
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        page = client.get(f"/session/{session_id}")
        assert page.status_code == 200, session_id
        assert len(page.content) < PAGE_BYTES, session_id


# One real URL per route the app exposes, keyed by the route's own path template. The sweep
# below reads this as a set, so a route added with no entry fails rather than going unswept.
ROUTES: dict[str, str] = {
    "/": "/",
    "/sessions": "/sessions",
    # The eight node kinds, each at a session that records the shape: a compaction at the
    # session with two of them, a bucket at each of the two sessions that has one.
    "/session/{session_id}": f"/session/{SPINE}",
    "/session/{session_id}/turn/{source}/{turn_id}": f"/session/{ANCESTOR}/turn/main/{DENSE_TURN}",
    "/session/{session_id}/run/{run_id}": f"/session/{SPINE}/run/{SPINE_RUN}",
    "/session/{session_id}/call/{source}/{api_call_id}": (
        f"/session/{FORK_ORIGIN}/call/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/session/{session_id}/tool/{source}/{tool_call_id}": (
        f"/session/{FORK_ORIGIN}/tool/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
    ),
    "/session/{session_id}/compaction/{source}/{compaction_id}": (
        f"/session/{COMPACTED}/compaction/main/{COMPACTED_BOUNDARY}"
    ),
    "/session/{session_id}/unattributed/{source}": f"/session/{RESUME}/unattributed/main",
    "/session/{session_id}/unattached": f"/session/{FORK_ORIGIN}/unattached",
    # The three pages that are not nodes: where a session failed, a thread's raw transcript,
    # and a file a tool wrote.
    "/session/{session_id}/errors": f"/session/{FORK_ORIGIN}/errors",
    "/session/{session_id}/records/{source}": f"/session/{ANCESTOR}/records/main",
    "/session/{session_id}/offload/{name:path}": f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}",
    # And the per-value fetches a pane's previews offer: one per fat column a node can hold.
    "/fragment/text/{session_id}/{source}/{api_call_id}": (
        f"/fragment/text/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/thinking/{session_id}/{source}/{api_call_id}": (
        f"/fragment/thinking/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_CALL}"
    ),
    "/fragment/input/{session_id}/{source}/{tool_call_id}": (
        f"/fragment/input/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
    ),
    "/fragment/result/{session_id}/{source}/{tool_call_id}": (
        f"/fragment/result/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
    ),
    "/fragment/prompt/{session_id}/{source}/{turn_id}": (
        f"/fragment/prompt/{ANCESTOR}/main/{DENSE_TURN}"
    ),
    "/fragment/brief/{session_id}/{run_id}": f"/fragment/brief/{SPINE}/{SPINE_RUN}",
    "/fragment/record/{session_id}/{source}/{line_no}": f"/fragment/record/{ANCESTOR}/main/1",
    # And a node's body alone, the way a log row expands its child. Two shapes: a run's URL
    # carries its id where every other kind carries a thread.
    "/fragment/body/{kind}/{session_id}/{source}/{node_id}": (
        f"/fragment/body/turn/{ANCESTOR}/main/{DENSE_TURN}"
    ),
    "/fragment/body/run/{session_id}/{run_id}": f"/fragment/body/run/{SPINE}/{SPINE_RUN}",
    # And the rest of a level, the way a `+N more` row opens one. Two shapes again, plus the
    # thread the reader is on and the depth the rows are going to, neither of which the level
    # itself can say. The window is turned down because what these serve is whatever a window
    # left out — at the default, over this corpus, nothing at all.
    "/fragment/kin/{kind}/{session_id}/{source}/{node_id}": (
        f"/fragment/kin/turn/{ANCESTOR}/main/{DENSE_TURN}?kin=1&thread=main&depth=2"
    ),
    "/fragment/kin/{kind}/{session_id}/{node_id}": (
        f"/fragment/kin/session/{SPINE}/{SPINE}?kin=1&thread=main&depth=1"
    ),
    # And the statement behind a citation, which every page's footer links to.
    f"{QUERY_URL}/{{name}}": f"{QUERY_URL}/view_sessions",
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
    """Every route answers under the ceiling at the sizes its URL carries.

    A smoke check rather than the proof: the fixture corpus is far smaller than a page, so
    what makes the bound hold is the fat-column scan and the page-size arithmetic above. What
    this catches is the route that ships a whole column anyway.
    """
    response = client.get(path)
    assert response.status_code == 200, path
    assert len(response.content) < PAGE_BYTES, path


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_every_query_the_library_ships_serves_under_the_ceiling(
    name: str, client: TestClient
) -> None:
    """A query page weighs its file marked up, and no library file is near the ceiling.

    The one page whose size is a file's rather than a bound's: the SQL is served whole, because
    a statement a reader cannot run is not a citation. Marking it up multiplies it about
    fourfold, so what this pins is that no query in the library is long enough for that to
    matter — and that a query added later is measured rather than assumed.
    """
    page = client.get(f"{QUERY_URL}/{name}")
    assert page.status_code == 200, name
    assert len(page.content) < PAGE_BYTES, name


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
    # Served whole — the chunk is not silently cut — and still under the ceiling. Counted
    # inside the block rather than over the page, which also carries escaped `&` in its links.
    assert block(page.text, "content").count("&amp;") == bounds.CHUNK.ceiling
    assert len(page.content) < PAGE_BYTES


# What a node page's arithmetic prices row by row, which chrome is the page without: a crumb of
# the chain down to the selection, a row of the tree, a row of the pane's children log, and one
# previewed value. Each is matched rather than differenced, so what the leaf below weighs is the
# row itself and not a difference between two pages that could differ in something else.
PRICED_ROWS = {
    "crumb": r"<a data-crumb=.*?</a>",
    "tree": r'<li class="row.*?</li>',
    "log": r"<li data-child=.*?</li>",
    # The control under the log, which is once a page rather than once a row — priced apart
    # from the chrome because it renders only where the level runs past one page, so a page
    # that happens to hold every child of its node would otherwise weigh it at nothing.
    "pager": r'<nav class="pager".*?</nav>',
    "detail": r'<section class="detail".*?</section>',
}


# The sizes that make every link on a node page longest, which is what `worst_knob_bytes`
# prices. Written as a request rather than derived from `knobs`, so the leaf below fails if the
# app stops accepting one of them rather than quietly measuring a page with no knobs at all.
WORST_KNOBS = {
    "nav": max(nodes.Preset, key=len).value,
    "log": bounds.LOG.ceiling - 1,
    "detail": bounds.DETAIL.ceiling - 1,
}


def priced(html: str) -> tuple[str, dict[str, list[str]]]:
    """A node page split into the rows the arithmetic prices and the chrome it does not."""
    rows: dict[str, list[str]] = {}
    for name, pattern in PRICED_ROWS.items():
        rows[name] = re.findall(pattern, html, flags=re.S)
        html = re.sub(pattern, "", html, flags=re.S)
    # The split is the instrument, so it is checked both ways: a row left in is a cost counted
    # twice, and a wrapper taken out hides part of the page this measures.
    assert not values(html, "data-crumb") and not values(html, "data-tree")
    assert not values(html, "data-child") and not values(html, "data-detail")
    assert 'id="tree-rows"' in html and 'id="pane"' in html
    return html, rows


def test_a_node_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every part of a node page weighs no more than the arithmetic above gives it.

    The node page is the one page `worst_node_bytes` multiplies four ways — a crumb per level
    open, a tree row per child of each, a log row per child of the selection, and the values the
    pane previews — so a template that grows any of them puts the ceiling out by whatever size
    it is multiplied by. Every cap a label, a heading or a preview reads is planted full of `&`,
    the character that escapes to five bytes, because no recorded node is adversarial: what a
    pass wrote, and the prompt, command, agent type, model, tool name and tool payload a page
    falls back to. The sweep is every node of every session, not one page: the widest chrome
    belongs to whichever pane is dearest, and that is a question about the corpus.
    """
    label = "&" * queries.CHIP_CHARS
    head = "&" * queries.HEADER_CHARS
    # Longer than the widest cut any query makes, so every cut bites and every preview offers
    # the rest of itself: what this weighs is the page at its caps, not at the corpus's sizes.
    fat = "&" * (queries.DETAIL_CHARS + 1)
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
        # What a turn's tree row, log row and pane read: the prompt is the pane's one preview
        # and the row's label both, so it goes in at the wider of the two cuts.
        (
            "UPDATE turns SET prompt = ?, command_name = ?, command_args = ?",
            [fat, "&" * COMMAND_NAME_CHARS, "&" * COMMAND_ARGS_CHARS],
        ),
        ("UPDATE agent_runs SET agent_type = ?, model = ?, description = ?", [label, label, fat]),
        ("UPDATE api_calls SET model = ?, text = ?, thinking = ?", [label, fat, fat]),
        # Every tool call failed, which is the dearest a tool row gets: the mark the tree puts
        # on a failure is markup no other kind of row carries, and a tree row is the one thing
        # on the page multiplied 417 times. It is also what puts the stepper on every tool
        # page, which is the dearest the chrome under a pane gets.
        (
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            [label, fat, fat],
        ),
        *DESCRIBED_AT_EVERY_CAP,
    )
    with TestClient(build_app(path)) as planted:
        served = []
        # Twice over the store: once at the defaults, where the tree holds a row of every kind
        # there is, and once at the knobs that make every link on the page longest. A reader
        # who narrows a page pays for the query string on every row of it, and the two sweeps
        # together hold the widest row of each kind beside the dearest link.
        for marks in ({}, WORST_KNOBS):
            for url in pages(store):
                response = planted.get(url, params=marks)
                assert response.status_code == 200, (url, response.text[:200])
                served.append(response.text)
        # And once more one child to a page, at the second page of each level: no recorded
        # node has children enough to page at a size a reader would type, and the control
        # under the log is what a level running past its page costs. A level of fewer than
        # three has no second page and no middle page, and answers 404 by design.
        for url in pages(store):
            response = planted.get(url, params={**WORST_KNOBS, "log": 1, "page": 2})
            if response.status_code == 200:
                served.append(response.text)
    # The list and the two pages that are not nodes come back too; only a node page splits.
    split = [priced(page) for page in served if 'id="tree-rows"' in page]
    # A crumb, a tree row, a log row and a preview each weigh what the arithmetic budgets...
    for name, budget in (
        ("crumb", worst_crumb_bytes()),
        ("tree", bounds.TREE_ROW_BYTES),
        ("log", worst_log_row_bytes()),
        ("pager", MEASURED_PAGER_BYTES),
        ("detail", worst_detail_bytes()),
    ):
        found = [row for _, rows in split for row in rows[name]]
        assert found, name
        assert max(len(row.encode()) for row in found) <= budget, name
    # ...and what the page carries whatever it holds fits the allowance the ceiling gives it.
    widest = max((chrome for chrome, _ in split), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_NODE_CHROME
    # The plant reached the caps, which is what makes those numbers a worst case: each header
    # string cut to its head, each list cut to its first members and saying how many it left,
    # every tree label cut to a nav width, and every preview offering the rest of itself.
    session = next(chrome for chrome, _ in split if 'data-body="session"' in chrome)
    facts = fields(session, "data-body", "session")
    assert len(facts["git_branch"]) == len(facts["version"]) == queries.HEADER_CHARS
    escaped = {
        found.count("&amp;")
        for _, rows in split
        for row in rows["tree"]
        for found in re.findall(r'<span data-field="label">(.*?)</span>', row, flags=re.S)
    }
    # No label got past the cut, and one reached it. Not every row's label is planted — a
    # bucket is named by the viewer and a compaction by its trigger — so the widest is what
    # says the cut bit rather than every row being the same width.
    assert max(escaped) == queries.NAV_CHARS
    cuts = {row.count("more character(s)") for _, rows in split for row in rows["detail"]}
    assert cuts == {1}
    # And the mark a failed call carries reached the rows the tree priced, so `TREE_ROW_BYTES`
    # is a price for the dearest tool row rather than for one that happened to succeed.
    assert any('data-field="is_error"' in row for _, rows in split for row in rows["tree"])
    # The enrichment sits in the chrome, stale tag and all, so it is planted with the rest.
    described = fields(session, "data-enrichment", values(session, "data-enrichment")[0])
    assert len(described["description"]) == len(described["friction"]) == queries.ENRICHMENT_CHARS
    assert described["stale"] == "stale"


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


def test_an_errors_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """A session's errors list at its ceiling weighs no more than the arithmetic gives it.

    Nothing about a session caps how often its tools fail, so the store is filled past the
    page's own ceiling and every label planted full of `&` — the character that escapes to
    five bytes. The failures are clones of a recorded tool call rather than invented rows;
    what is planted on each is the flag the store already records on two of them.
    """
    over = bounds.ERRORS.ceiling + 20
    # A label longer than the width a row cuts it to, so the cut bites on every row. The index
    # differs per clone because it is half of what orders the list: a page showing the first
    # `ERRORS` of a partial order is a page that cannot say what it cut.
    label = "&" * (queries.NAV_CHARS + 1)
    path = plant(
        (
            "INSERT INTO tool_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            ' ? AS name, ? AS input, true AS is_error, 9000 + i AS "index")'
            " FROM (SELECT * FROM live_tool_calls WHERE session_id = ? LIMIT 1) c,"
            " range(1, ?) g(i))",
            [label, label, FORK_ORIGIN, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        response = planted.get(f"/session/{FORK_ORIGIN}/errors")
    assert response.status_code == 200, response.text[:200]
    page = response.text
    # A page a reader jumps to stays under the ceiling with every label at its cap...
    assert len(response.content) < PAGE_BYTES
    shown = values(page, "data-error")
    assert len(shown) == bounds.ERRORS.ceiling
    # ...every one of them a planted failure cut to the width a row reads it at...
    labels = {len(fields(page, "data-error", key)["label"]) for key in shown}
    assert max(labels) == queries.NAV_CHARS + len(ELLIPSIS)
    # ...and what it left out said rather than dropped, against the store's own count.
    with duckdb.connect(str(path), read_only=True) as connection:
        (failures,) = one(
            connection,
            "SELECT count(*) FROM live_tool_calls WHERE session_id = ? AND is_error",
            [FORK_ORIGIN],
        )
    assert values(page, "data-more-errors") == [str(failures - bounds.ERRORS.ceiling)]
    # What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    # rows the arithmetic counts separately stripped out...
    chrome = re.sub(r"<li data-error=.*?</li>", "", page, flags=re.S)
    assert not values(chrome, "data-error") and 'id="errors"' in chrome
    assert len(chrome.encode()) <= MEASURED_ERRORS_CHROME
    # ...and one row costs no more than its markup and the label it carries.
    row_bytes = (len(page.encode()) - len(chrome.encode())) / bounds.ERRORS.ceiling
    assert row_bytes <= worst_error_row_bytes()


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
        tool = f"/fragment/{{}}/{FORK_ORIGIN}/{FORK_ORIGIN_RUN}/{DENSE_TOOL}"
        fetched = [
            (planted.get(tool.format("input")), len(indents_huge)),
            (planted.get(tool.format("result")), len(indents_huge)),
            (planted.get(f"/fragment/record/{ANCESTOR}/main/1"), len(overflows_the_parser)),
        ]
    # Each fragment answers, and weighs the value it names plus a page of chrome at most.
    for response, stored in fetched:
        assert response.status_code == 200
        assert len(response.content) < stored + PAGE_BYTES


def test_a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every preview is truncated before it reaches a page, so no one huge value can bloat it.

    The four widths the viewer cuts to, checked at once against one planted store: a list
    row's, a tree row's label, a children log row's, and a pane's — a header's strings at one
    cut and the one value it is about at another, wider one. The oversized values are
    invented: redaction flattened every recorded string to a few characters, so no fixture
    reaches a cap.
    """
    # One turn of each kind, because a pane shows one arm or the other: a plain turn's prompt,
    # and a slash turn's command with its arguments.
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
    long = "x" * (queries.DETAIL_CHARS + 5_000)
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
        ("UPDATE tool_calls SET input = ?, name = ? WHERE session_id = ?", [long, long, ANCESTOR]),
    )
    with TestClient(build_app(path)) as planted:
        listing = planted.get("/sessions").text
        session = planted.get(f"/session/{SPINE}").text
        turn = planted.get(f"/session/{SPINE}/turn/main/{turn_id}").text
        slash = planted.get(f"/session/{SPINE}/turn/main/{command_id}").text
        run = planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
        call = planted.get(f"/session/{ANCESTOR}/call/main/{DENSE_TURN_CALL}").text
    # ...and what each of them shows is its cap, not the value. The list's cuts are the
    # viewer's own composition rather than its query's, because its filters read the whole
    # values — a project path cut to a head would match no session under a longer one.
    row = fields(listing, "data-session-id", SPINE)
    assert len(row["title"]) == len(row["project_dir"]) == queries.LIST_CHARS
    # A path too long for the filter box to suggest whole is left out of it rather than cut:
    # half a path fills the filter in with a value that matches nothing.
    assert not [path for path in re.findall(r'<option value="([^"]*)"', listing) if "x" in path]
    # A tree row is a line in a sidebar, so its label takes the narrowest cut of the four —
    # the same one whatever kind of node the row stands for.
    labels = re.findall(r'<span data-field="label">(.*?)</span>', session, flags=re.S)
    # Cut and marked as cut: every column a label is composed from comes back one character
    # past the width, so a row that fills the line says the value went on.
    assert max(labels, key=len) == "x" * queries.NAV_CHARS + ELLIPSIS
    # A children log row is a line of a table, so it takes the next cut up.
    log = re.findall(r"<li data-child=.*?</li>", session, flags=re.S)
    assert (
        max(len(field) for row in log for field in values(row, "data-field")) <= queries.LOG_CHARS
    )
    # A pane reads one node, so its strings take a header's cut — and the one value the node
    # is about takes the widest of the four, with the rest of it offered as its own fetch.
    assert fields(turn, "data-detail", "prompt")["prompt"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert inside(turn, "data-detail", "prompt", "data-whole") == ["prompt"]
    # A slash turn's own two strings are facts of its pane rather than the value it is about,
    # so both take a header's cut instead.
    command = fields(slash, "data-body", "turn")
    assert len(command["command_name"]) == len(command["command_args"]) == queries.HEADER_CHARS
    header = fields(run, "data-body", "run")
    assert {len(header[field]) for field in ("agent_type", "model")} == {queries.HEADER_CHARS}
    brief = fields(run, "data-detail", "description")["description"]
    assert brief == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert len(fields(call, "data-body", "call")["model"]) == queries.HEADER_CHARS
    assert fields(call, "data-detail", "text")["text"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
