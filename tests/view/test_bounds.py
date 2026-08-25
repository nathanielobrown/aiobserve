"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Three mechanisms, checked separately: the queries behind the pages and fragments never select
an unbounded fat column, what they do select is truncated in SQL rather than in the template,
and every page size is a bound parameter whose production default is pinned here. Together
they are what makes the bound hold by construction rather than by the fixture corpus's luck —
a per-value fetch is the one exception, and it is exempt because its unit *is* one value.
"""

import json
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

from aiobserve.analyze import macros, queries
from aiobserve.analyze.queries import QUERIES, VIEW_PREFIX, ParamValue
from aiobserve.view import bounds, nodes
from aiobserve.view.app import QUERY_URL, build_app, knobs
from aiobserve.view.format import ELLIPSIS
from aiobserve.view.listing import SHOWN
from aiobserve.view.store import TURN_CURSOR, Fragment, Page, Value, cursorless_rows
from tests.conftest import (
    ANCESTOR,
    CONFIG_ONLY,
    DENSE_TOOL,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    OFFLOAD_FILE,
    RESUME,
    RESUME_LONG_RECORD,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import (
    ROUTES,
    Planter,
    Statement,
    block,
    fields,
    inside,
    one,
    pages,
    plain,
    suggestions,
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
# `DEPTH` levels of `KIN` children, so the window a level opens on prices about half of the
# page — and it is a window, not a limit: a tail row fetches what it left out and stands the
# rows in its own place, without a page boundary anywhere. Widening the window is a reader
# reaching further per click, and pinning it here rather than against `PAGE_BYTES` keeps that
# choice off the list pages, whose ceilings are derived against the number above. The
# arithmetic under it — `worst_node_bytes`, at every ceiling at once — is kept true by the
# leaf at the bottom of this file, and the total is in the last line below. Raised from 1,050,000
# when the children log's rows began saying what each child was asked: a row went from 1,654 B
# to 6,079 B, and 100 of them is 442,500 B more page. That is what a reader gets for it — a
# level of a hundred read without opening one, where before it was a hundred bare numbers.
# Raised again from 1,450,000 when a pane began showing a value in its own syntax: a preview
# marked up is budgeted at `MARKED_CHAR_BYTES` rather than at an escape, and a `Bash` call
# previews a third value besides. The two together are 120,600 B, all of it on one preview of
# one pane — and what a reader gets is the shell and the file read as what they are.
#
# Raised from 1,570,000 when the tree's window went from 50 children to 200 and its titles
# from 48 characters to 110: four times the rows at a quarter more each is 3.3 MB of tree, and
# the tree was already four fifths of the page. That is the whole of the increase, and what a
# reader gets for it is a level of two hundred read where a level of fifty was — the fetch a
# tail row offers is the same rows over a second request, so the bytes were already reachable;
# what moved is how many clicks reach them.
#
# Raised again from 4,900,000 for the mark every row now carries saying what kind of node it
# is, in the pattern the children log's column heads already use.
# `<span class="icon" aria-hidden="true">❖</span> ` is 45 B of markup around a 3-byte mark,
# plus the space after it: 49 B a row, and 3,217 rows of it is 157,633 B, with 800 B more on
# the crumbs and 73 B on the pane's heading and the browser tab. The old ceiling left 34,666 B,
# 10 B a row, so the raise landed before the markup rather than a template edit becoming an
# argument about a ceiling. What a reader gets for it is a tree read by shape rather than by
# title.
#
# Raised again from 5,050,000 for the prompt a pane now reads as the markdown it was written
# in. A rendered preview is budgeted at `MARKED_CHAR_BYTES` like a highlighted one, so the
# pane's dear previews went from one to two: 4,000 characters at 25 B more each is 100,000 B,
# all of it on one preview of one pane. What a reader gets is the turn's ask read as prose —
# headings, lists and fenced code — where the whole-value fetch already rendered it and the
# head beside it printed the source.
#
# Raised again from 5,150,000 for the two columns an api call's row now fills: what the call
# said, and the tools it went on to call. They make a call's the dearest row in any children
# log — `MEASURED_LOG_ROW_MARKUP` went from 1,450 B to 1,650 B — and a log is a hundred rows,
# so 20,000 B of page. What a reader gets is a turn's calls read without opening one, where
# before the column that named them said the same model a hundred times. The arithmetic under
# it comes to 5,143,767 B, and `TREE_ROW_BYTES` is pinned from below so the 26,233 B left over
# cannot be spent by a row that quietly grew instead.
#
# Raised again from 5,170,000 for the two values a run's pane now reads off the call that
# spawned it: what the run was asked, and what it sent back. Both are markdown, so a run's is
# the first pane whose three previews are all rendered — `DEAR_PANE_DETAILS` went from two to
# three, which is 4,000 characters at 25 B more, 100,000 B, on one preview of one pane. What a
# reader gets is a run read whole where the page used to show only the line it was named by.
# The arithmetic comes to 5,243,767 B, and the slack under the ceiling is the same 26,233 B.
NODE_BYTES = 5_270_000
# What one expansion may weigh: a node's body opened in place, inside someone else's children
# log. It is over `PAGE_BYTES` and declared here rather than derived against it, for the reason
# `bounds.OPENED_RECORD_CHARS` draws the same line the other way — a reader clicked. An
# expansion is a row of a hundred asking for the level under it, priced like the per-value
# fetches a click starts, and what bounds it is the `?log=` cap the reader is already reading
# under rather than a second cap under that. The arithmetic is `worst_expansion_bytes`: the
# body's own chrome plus one page of log rows at the widest a row gets, 638,000 B, which leaves
# 2,000 B over. A `bounds.LOG` ceiling raised past 100 spends it a row at a time — which is the
# point of naming the number, because a page of rows nobody budgeted is what a click can afford
# to hide.
EXPANSION_BYTES = 640_000
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
# by the leaf at the bottom of this file, every title planted full of `&` and the session
# failing more calls than the page shows: 620 B a row, of which 240 B is a planted title,
# leaving 380 B of the link and the two cells after it — and 2,375 B of chrome, which is small
# for the same reason the landing page's is: no form, no pager and no suggestions.
MEASURED_ERROR_ROW_MARKUP = 400
MEASURED_ERRORS_CHROME = 2_500

# What an expansion carries outside the rows it lists: the node's own body, the link to its
# page, and the queries it cites. The body's facts are read at `HEADER_CHARS` rather than at
# the reader's `?detail=` — an expansion previews no fat value — so this is a fraction of the
# chrome a page carries. Measured through the app by the leaf below over all three kinds a log
# opens a body for, each planted at the caps its body reads: an api call's is the dearest at
# 6,376 B, against a turn's 3,013 and a tool call's 2,484. A call's body is the one standing
# above a table, and its title is the head of what the call said.
MEASURED_EXPANSION_CHROME = 6_500

# What a row of the records browser really costs — the preview plus the row's own markup, most
# of it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` on
# 2026-08-08: 83,659 B for a 100-record page less 1,865 B of chrome, over the 99 rows between.
# The fixture records are redacted to a few characters, so they project nothing about this.
MEASURED_RECORD_BYTES = 826

# What the markup around one row of the pane's children log costs, with the strings it carries
# taken off: a cell per column of the shape's own table, three copies of the node's URL — the
# link, the `hx-get` behind it, and the mount the View button opens through — the swap the link
# performs, the numbers that tell two children apart, and the row around them. Re-measured
# through the app by the leaf at the bottom of this file, every cap full of `&` and every knob
# at its longest — 6,226 B on an api call's row, of which 4,515 B is content at those caps and
# 150 B the knobs, leaving 1,561 B. A string at its cap is 300 escapes and the mark that says
# it was cut; the arithmetic below charges the 301 escapes the cut selected, which is 2 B a
# string more than a row can really carry. The dearest row moved from a tool call's to an api
# call's when a call's row began saying what the call said and which tools it called: nine
# columns against a tool row's seven, and the same three strings.
MEASURED_LOG_ROW_MARKUP = 1_650
# How many strings one row of a children log prints, each cut to `LOG_CHARS` and selected a
# character past it. Three is the widest row there is: an api call's row is the model that
# answered, the head of what it said, and the tools it went on to call; a tool row is the
# tool's name, the head of what it was asked, and the command that head describes. A turn row
# prints one and a run two. Listed rather than counted off `nodes.COLUMNS`, because most of
# those columns are a number or a stamp; what keeps the number honest is the leaf at the bottom
# of this file, which plants every string a row can print past its cut and weighs the row.
LOG_ROW_STRINGS = 3
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
# node's key, the mark saying what kind of node the step is, and the glyph saying who named it.
# Measured the same way — 915 B less 550 B of title and 50 B of knobs, leaving 315 B.
MEASURED_CRUMB_MARKUP = 330
# And what the markup around one previewed value costs — the heading, the `<pre>` and the line
# offering the rest of it — with the preview itself taken off.
MEASURED_DETAIL_MARKUP = 600
# How many fat values one pane previews at once. Three is the most any kind shows: a `Bash`
# call previews the command it ran, the arguments it was passed and what came back, and an api
# call what it said and what it thought. A fourth would be a kind whose pane the arithmetic
# below has not priced.
PANE_DETAILS = 3
# And how many of those the page marks up rather than printing as the characters the store
# holds. Three, which is a run's pane: the brief it was named by, the prompt it was given and
# the answer it sent back, all written by a person or a model. No other kind reaches three —
# a turn previews the prompt and what followed its slash command, an api call what it said and
# what it thought — and the other kind of markup is a syntax the record named, which is the
# command a `Bash` call ran or the file a `Read` returned. No call is both tools, so a tool's
# pane marks up one of its three.
DEAR_PANE_DETAILS = 3
# What a node page carries outside its tree rows, its log rows and its previews: the crumbs
# down to the selection, the node's own facts, and what a pass said about it. The session is
# the widest of the eight panes — every string in its header is one a transcript wrote, and its
# two lists grow with the session — so the allowance is a session header's, cut in SQL.
# The preset switcher rides here too, three links carrying the node's own URL, the children
# log's own table head — a word and an icon for each column of the shape the log lists — and,
# on a pane reading a failed tool call, the step to the failure before it and the one after.
# The pane's own heading and the browser tab each carry the mark saying what kind of node the
# page is about, which is the whole of what the two of them cost here.
# Re-measured through the app by the leaf at the bottom of this file at 17,138 B. Up to five
# of its strings are tree titles — the page title, and the two steppers under the pane — so it
# moves with `queries.NAV_CHARS`.
MEASURED_NODE_CHROME = 17_500

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
# And what the mark on a cut value costs, once per cut column: the ellipsis a value carries in
# place of the rest of itself, which is three bytes of UTF-8 and no escape.
MARK_BYTES = len(ELLIPSIS.encode())
# And the most one character of it can weigh where the page marks it up in its own syntax: a
# `<span class="` of 13, a class of 3, a `">` of 2, a `</span>` of 7, and the character itself
# escaped to 5. A construction bound like the one above rather than a measurement, for the same
# reason — what a lexer makes a token of is a property of the lexer, and a value every character
# of which is its own token costs the lot.
#
# The class is three characters because `view/highlight.py:_ShortClasses` holds it there. Left
# alone the formatter joins a name for every step up to a token type Pygments has a name for
# (`l l-Scalar l-Scalar-Plain`, 25 characters), and those types are reachable — the markdown
# lexer hands a fenced block to whatever lexer the fence names. `test_highlight.py:
# test_every_class_the_markup_carries_is_one_of_pygments_short_names` is the pin.
#
# The dearest content the viewer marks up today reaches 26 bytes a character (`&;` repeated,
# read as `.sql` or `.py`) without any lexer being adversarial. A preview rendered as the
# markdown it was written in is priced at the same number and reaches it the same way: a fenced
# block goes through these lexers, and every other construct markdown has costs its tags once a
# line — the deepest of them, a quote inside a quote, is capped at markdown-it's nesting limit.
MARKED_CHAR_BYTES = 30
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
    shown = heads(SHOWN, LIST_HEAD)
    written = heads(said, LIST_HEAD)
    strings = shown * queries.LIST_CHARS
    # The skill names are cut in the composition and the agent types in the query itself —
    # a type is grouped after its cut, so the cut has to be where the grouping can see it.
    listed = heads(SHOWN, LIST_ITEM_HEAD) + heads(queries.load(Page.SESSIONS), LIST_ITEM_HEAD)
    members = listed * queries.LIST_ITEMS
    names = members * queries.LIST_ITEM_CHARS
    described = written * queries.LIST_CHARS
    kinds = heads(said, LIST_KIND_HEAD) * queries.LIST_CATEGORIES * queries.TAG_CHARS
    return (
        MEASURED_SESSION_ROW_MARKUP
        + (strings + names + described + kinds) * ESCAPED_CHAR_BYTES
        # Every value a transcript or a pass wrote is marked where it was cut — the two heads a
        # row shows, each member of its two lists, and the pass's own line — one mark per cut,
        # outside the escape, since an ellipsis is three bytes of UTF-8 and nothing escapes it.
        # The kinds of work are the one cut column with no mark: their vocabulary is closed
        # (`enrich/taxonomy.py`) and its longest member is 9 characters against `TAG_CHARS`, so
        # that cut is a bound this arithmetic needs rather than one a value reaches.
        + (shown + members + written) * MARK_BYTES
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
    """What one row of a session's errors list can weigh: its markup, and a title of `&`.

    A row is a link to the failed tool call, named the way a tree row names it — the tool's
    name and the head of what it was passed, cut to one width between them — beside the thread
    it ran on and the clock. The thread is an agent id the store minted, and the timestamp is
    as long as its type allows; only the title is text a transcript wrote.
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
    narrows a page pays for the query string on every row of it. The longest one takes the
    longest preset name beside the widest size that is not a default in each of the three —
    one under the ceiling, which is where a size stops being silent and starts being written.
    Escaped, because the `&` between two of them is written into an attribute.

    `?kin=` is priced here rather than left at its default, which is a byte a link cheaper but
    a whole level of rows dearer. That trade used to fall the other way: at a window of 50 the
    rows a narrower tree dropped outweighed the string it wrote, and at 200 they no longer do.
    So the arithmetic prices the dearest row any size produces against the most rows any size
    produces — one size cannot do both, and the gap is 57 KB of an allowance kept whole.
    """
    marks = knobs(
        max(nodes.Preset, key=len),
        bounds.KIN.ceiling - 1,
        bounds.LOG.ceiling - 1,
        bounds.DETAIL.ceiling - 1,
    )
    return len(escape(marks).encode())


def worst_log_row_bytes() -> int:
    """What one row of the pane's children log can weigh: its markup and the strings it prints.

    A log row is a link, the numbers that tell two children apart, and the strings the store
    wrote — a turn's title, the model a call ran on, the tool a call called and the head of
    what it was asked. Every one of them is cut to a log column's width in the query that
    selects it, a character past the cut so a row that fills its column says so.
    """
    return (
        MEASURED_LOG_ROW_MARKUP
        + LOG_ROW_STRINGS * (queries.LOG_CHARS + 1) * ESCAPED_CHAR_BYTES
        # A row links where it fetches and mounts where it expands, so it carries the knobs
        # three times.
        + 3 * worst_knob_bytes()
    )


def worst_crumb_bytes() -> int:
    """What one crumb of the chain above a node can weigh: its markup, a title of `&`, and the
    knobs its link carries once."""
    return MEASURED_CRUMB_MARKUP + queries.NAV_CHARS * ESCAPED_CHAR_BYTES + worst_knob_bytes()


def worst_stored_detail_bytes() -> int:
    """What one previewed value printed as stored can weigh: its markup, and a preview of `&`."""
    return MEASURED_DETAIL_MARKUP + bounds.DETAIL.ceiling * ESCAPED_CHAR_BYTES


def worst_rendered_detail_bytes() -> int:
    """What one previewed value the page marks up can weigh: its markup, and a preview whose
    every character costs an element.

    One price for the two ways a preview is marked up. A value in the syntax the record named
    is a span a token; a value rendered as the markdown it was written in reaches the same
    lexers through a fenced block, and every other construct markdown has — a heading, a list,
    a quote — costs its tags once a line rather than once a character.
    """
    return MEASURED_DETAIL_MARKUP + bounds.DETAIL.ceiling * MARKED_CHAR_BYTES


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
        + (PANE_DETAILS - DEAR_PANE_DETAILS) * worst_stored_detail_bytes()
        + DEAR_PANE_DETAILS * worst_rendered_detail_bytes()
    )


def worst_expansion_bytes() -> int:
    """What one expansion opened in a children log can weigh.

    A body where the page has its tree and its crumbs, and under it the level the node's own
    page lists — the same log, at the same `?log=` cap and one column narrower, because no row
    inside an expansion opens another. So an expansion prices as a page of log rows plus a
    body, and the cap that bounds the log on a page is what bounds it here. `EXPANSION_BYTES`
    is what this is checked against.
    """
    return MEASURED_EXPANSION_CHROME + bounds.LOG.ceiling * worst_log_row_bytes()


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
    # A character past each cap, so the page pays for the mark as well as the width: the words
    # a pass writes are the one field here that routinely runs past what a pane prints.
    payload: list[str | int] = [
        "&" * (queries.ENRICHMENT_CHARS + 1),
        "&" * queries.TAG_CHARS,
        "&" * queries.TAG_CHARS,
        "&" * (queries.ENRICHMENT_CHARS + 1),
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
# count of what it holds, the check that it parses, or one of the library's own cutting macros.
# Anything else puts the whole value on the page. Read at any depth —
# `substr(coalesce(json_extract_string(input, …), …), 1, $n)` is a cut of whatever it wraps, so
# what a bounding call opens is exempt to its close.
BOUNDING = ("substr", "length", "json_valid", *macros.BOUNDING)


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


def test_every_macro_the_scan_trusts_cuts_the_value_it_reads() -> None:
    """The scan cannot see through a macro call, so what it trusts by name is checked by body.

    Without this the trust is a list: a macro that stopped cutting would go on being read as
    bounding, and every query calling it would keep its green while serving whole values.
    The signature comes off first — a parameter named `input` is a name, not a column read.

    This says a cut is *there*, not that it is the right one: a body cutting at ten thousand
    times the width it was asked for still passes here. The width is the leaf below.
    """
    for name, statement in macros.BOUNDING.items():
        _, cut_at, body = statement.partition(") AS")
        assert cut_at, name
        assert unbounded(body) == set(), name


def test_every_macro_the_scan_trusts_answers_one_character_past_the_width() -> None:
    """Each bounding macro is run at three widths and asked how much it gives back.

    The scan's trust is a bound; this is the protocol on top of it (`view/format.py:cut`
    marks a value that came back longer than the width, so a macro that saturates *under* the
    width serves a silently truncated value, and one that saturates over it serves a fat
    column). Every arm gets a value far past the widest width, so each answer is a saturation
    rather than a whole value that happened to fit.

    The paths are invented: the shape — inside the project, outside it, no project at all —
    is the whole point, and no recorded session carries all three at these lengths.
    """
    connection = duckdb.connect(":memory:")
    macros.install(connection)
    project = "/Users/planted/repos/aiobserve"
    inside = json.dumps({"file_path": f"{project}/src/{'v' * 400}.py"})
    outside = json.dumps({"file_path": f"/opt/homebrew/{'v' * 400}.py"})
    described = json.dumps({"description": "d" * 400, "command": "c" * 400})
    stored_whole = f"not json at all {'v' * 400}"

    def answer(expression: str, *params: object) -> str:
        return connection.execute(f"SELECT {expression}", list(params)).fetchall()[0][0]

    for chars in (10, 60, 300):
        # A field read straight, and the three arms `tool_title` coalesces over.
        assert len(answer("tool_asked(?, 'file_path', ?)", inside, chars)) == chars + 1
        assert len(answer("tool_title(?, ?, ?)", inside, project, chars)) == chars + 1
        assert len(answer("tool_title(?, ?, ?)", described, project, chars)) == chars + 1
        assert len(answer("tool_title(?, ?, ?)", stored_whole, project, chars)) == chars + 1
        assert len(answer("tool_ran(?, ?)", described, chars)) == chars + 1
        # The relativized path is the arm that spends width on a prefix it then throws away:
        # what comes back is the tail, and it is as long as any other arm's.
        relative = answer("tool_path(?, ?, ?)", inside, project, chars)
        assert len(relative) == chars + 1
        assert relative.startswith("src/")
        # A path the project does not contain, and a session that has no project directory,
        # both take the absolute arm — still at the width, still marked.
        assert len(answer("tool_path(?, ?, ?)", outside, project, chars)) == chars + 1
        assert len(answer("tool_path(?, ?, ?)", inside, None, chars)) == chars + 1


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
    # How many children one open level of the tree shows. Not a manifest default — the tree
    # composes its window around the query rather than binding it — and every leaf below
    # recomputes from whatever this says, so a literal is the only thing that reds when the
    # window silently narrows back to what it was.
    assert bounds.Bound(200, 200) == bounds.KIN
    # How much of a title a row of the tree shows. Wide enough that a draggable sidebar has
    # something to show when a reader widens it — the cut is what a row can say, and CSS
    # decides how much of it fits. Every level cuts to the same width, whatever kind of child
    # it holds.
    for level in ("view_tree_turns", "view_tree_calls", "view_tree_tools"):
        assert QUERIES[level].params["nav_chars"].default == 110, level
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
    # and titled at a tree row's width, because each of its rows leads to a node.
    assert QUERIES["view_session_errors"].params["nav_chars"].default == queries.NAV_CHARS
    assert QUERIES["view_session_errors"].params["errors"].default == 100
    # Every ceiling is projected at the largest page a URL can ask for, because a size is
    # something a reader types.
    assert bounds.RECORDS.ceiling * worst_record_bytes() < PAGE_BYTES
    # And the record that page opens for a reader who did not click it, which is priced as a
    # page rather than as the per-value fetch it goes to: every character its own token, plus
    # the indentation a JSON record gains, which is whitespace and written out bare.
    assert bounds.OPENED_RECORD_CHARS * MARKED_CHAR_BYTES + bounds.INDENT_CHARS < PAGE_BYTES
    assert bounds.CHUNK.ceiling * ESCAPED_CHAR_BYTES < PAGE_BYTES
    # The list is the page a corpus grows, so its ceiling is the widest page a URL can ask for
    # plus the chrome that rides every page — both bound by construction now, not by how long
    # the titles this corpus happens to hold are.
    assert MEASURED_LIST_CHROME + bounds.SESSIONS.ceiling * worst_session_row_bytes() < PAGE_BYTES
    # And it is the most rows that fit, not merely some number that does: the ceiling is
    # derived from the row's cost, so a row that grew has to move it rather than eat the slack
    # silently. The two together are what make `bounds.SESSIONS` a measurement — an upper bound
    # alone is satisfied by any smaller page, including one a stale derivation left behind.
    # It is the only ceiling held from below, for the reason kept beside the constants.
    assert (
        MEASURED_LIST_CHROME + (bounds.SESSIONS.ceiling + 1) * worst_session_row_bytes()
        >= PAGE_BYTES
    )
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
    # And the expansion a row of a log opens in place, which is a click and so has a ceiling of
    # its own: a body, and one page of the level under it at the size the reader is reading logs
    # under. Nothing derives this from `PAGE_BYTES` — it is over it — so the number is declared
    # and the arithmetic checked against it here.
    assert worst_expansion_bytes() < EXPANSION_BYTES
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
        "INDENT_CHARS",
        "HIGHLIGHT_CHARS",
        "OPENED_RECORD_CHARS",
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


def escaping_json(chars: int) -> str:
    """Valid JSON of exactly `chars` characters, in the shape a record costs most to mark up.

    A list of one-character strings: every element is its own token, so the formatter writes a
    span around three characters, and the character inside escapes to five bytes. Indented,
    each element also lands on a line of its own. Invented for the same reason the offload's
    content is — no recorded record is adversarial, and a record that parses is the only one
    the page marks up at all.
    """
    elements = ['"&"'] * ((chars - 2) // 4)
    listed = "[" + ",".join(elements) + "]"
    # The slack goes inside the last string, which keeps it valid JSON and one more token.
    return listed[:-2] + "&" * (chars - len(listed)) + listed[-2:]


def test_the_record_a_page_opens_unasked_serves_under_the_ceiling(plant: Planter) -> None:
    """The widest record a page fetches without a click stays under a page's ceiling.

    Every other per-value fetch here is exempt from the page bound: its unit is one value, and
    a reader who clicks for a value has asked for whatever the store holds. This one is not,
    because nobody clicked — the row the browser opens on arrival is a fetch the page starts —
    so `bounds.OPENED_RECORD_CHARS` is what keeps it a page's worth. An expansion is on the
    clicked side of that line and still over a page, which is why it carries a declared ceiling
    of its own rather than an exemption: see `EXPANSION_BYTES`.
    """
    raw = escaping_json(bounds.OPENED_RECORD_CHARS)
    assert len(raw) == bounds.OPENED_RECORD_CHARS
    path = plant(
        (
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND source = ? AND line_no = ?",
            [raw, RESUME, MAIN, RESUME_LONG_RECORD],
        )
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(
            f"/session/{RESUME}/thread/{MAIN}/records", params={"after": RESUME_LONG_RECORD - 1}
        )
        served = planted.get(
            f"/fragment/record/session/{RESUME}/thread/{MAIN}/line/{RESUME_LONG_RECORD}"
        )
    # The page opens this one on arrival, so what it weighs is what the page's load costs...
    assert inside(page.text, "data-open-record", str(RESUME_LONG_RECORD), "hx-trigger") == ["load"]
    # ...and it is the marked-up path being weighed, not a record served plain because it did
    # not parse — which is the whole reason a character is priced at a span and not an escape.
    assert served.status_code == 200
    assert "<span" in block(served.text, "raw")
    assert len(served.content) < PAGE_BYTES


# What a node page's arithmetic prices row by row, which chrome is the page without: a crumb of
# the chain down to the selection, a row of the tree, a row of the pane's children log, and one
# previewed value. Each is matched rather than differenced, so what the leaf below weighs is the
# row itself and not a difference between two pages that could differ in something else.
PRICED_ROWS = {
    "crumb": r"<a data-crumb=.*?</a>",
    "tree": r'<li class="row.*?</li>',
    "log": r"<tr data-child=.*?</tr>",
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
    "kin": bounds.KIN.ceiling - 1,
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
    it is multiplied by. Every cap a title, a heading or a preview reads is planted full of `&`,
    the character that escapes to five bytes, because no recorded node is adversarial: what a
    pass wrote, and the prompt, command, agent type, model, tool name and tool payload a page
    falls back to. The sweep is every node of every session, not one page: the widest chrome
    belongs to whichever pane is dearest, and that is a question about the corpus.
    """
    head = "&" * queries.HEADER_CHARS
    # Longer than the widest cut any query makes, so every cut bites and every preview offers
    # the rest of itself: what this weighs is the page at its caps, not at the corpus's sizes.
    fat = "&" * (queries.DETAIL_CHARS + 1)
    item = "&" * queries.HEADER_ITEM_CHARS
    # And the same width of the pair every lexer here makes two tokens of, for the two previews
    # a row can name the syntax of.
    tokens = "&;" * ((queries.DETAIL_CHARS + 2) // 2)
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
        # What a turn's tree row, log row and pane read. All three go in past every cut that
        # touches them: the digest cuts each to a log line's width, and the prompt is the
        # pane's one preview as well as the row's title, which is the wider of the two.
        ("UPDATE turns SET prompt = ?, command_name = ?, command_args = ?", [fat] * 3),
        ("UPDATE agent_runs SET agent_type = ?, model = ?, description = ?", [fat, fat, fat]),
        ("UPDATE api_calls SET model = ?, text = ?, thinking = ?", [fat, fat, fat]),
        # The input parses, and says all three of the things read out of one: the two a tool row
        # reads — a log row
        # that could not find a description would print the raw input in its place and leave
        # the line under it empty, which is a row two columns short of the widest one there is.
        # Every call failed, too, which is the dearest a tool row gets: the mark the tree puts
        # on a failure is markup no other kind of row carries. It does not make a tool the
        # widest row — a turn's row measures 914 B against a tool's 830 — but it is what puts
        # the stepper on every tool page, and that is the dearest the chrome under a pane gets.
        (
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            [fat, json.dumps({"description": fat, "command": fat, "prompt": fat}), fat],
        ),
        # And the two calls whose panes show a value in its own syntax, planted after the rest
        # so they keep the widths above and take the tool names that reach the lexers. `&;` is
        # the pair the shipped lexers make the most tokens of, which is what a preview budgeted
        # at a span a character has to hold: 26 B a character through the SQL lexer today.
        (
            "UPDATE tool_calls SET name = 'Bash', input = ?"
            " WHERE id = (SELECT min(id) FROM tool_calls)",
            [json.dumps({"description": fat, "command": tokens})],
        ),
        (
            "UPDATE tool_calls SET name = 'Read', input = ?, result = ?"
            " WHERE id = (SELECT max(id) FROM tool_calls)",
            # The path is planted past the cut like every other input here, and its suffix is
            # what the page reads the result's syntax off — a name, not a length.
            [json.dumps({"file_path": f"/{fat}/planted.sql"}), tokens],
        ),
        # And one turn asked in the dearest markdown there is: a fenced block, the one
        # construct markdown hands to a lexer. The pane cuts the head inside the fence, which
        # commonmark closes at the end of what it was given — so what it renders is `&;` at an
        # element a token, which is what a preview budgeted at `MARKED_CHAR_BYTES` has to hold.
        (
            "UPDATE turns SET prompt = ? WHERE id = (SELECT min(id) FROM turns)",
            [f"```sql\n{tokens}"],
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
    for name, budget, measured in (
        ("crumb", worst_crumb_bytes(), False),
        ("tree", bounds.TREE_ROW_BYTES, True),
        ("log", worst_log_row_bytes(), False),
        ("pager", MEASURED_PAGER_BYTES, False),
    ):
        found = [row for _, rows in split for row in rows[name]]
        assert found, name
        widest_row = max(len(row.encode()) for row in found)
        # Three of the four are arithmetic over a cap, so a row that comes in under is a cap
        # with room left in it. The tree row is measured rather than budgeted, and the tree is
        # four fifths of the page, so it is held from below as well: a byte of slack there is
        # 3,217 bytes the ceiling keeps for nothing, and `NODE_BYTES` now has room to hide one.
        assert widest_row == budget if measured else widest_row <= budget, (name, widest_row)
    # A preview is priced by whether the page marked it up, which is the whole of the
    # difference between the two budgets: an element a token against an escape a character.
    # Marked up two ways — the syntax a record named, and the markdown a session wrote — and
    # both are read off the markup rather than off the route, because what the ceiling pays
    # for is what came back.
    previews = [row for _, rows in split for row in rows["detail"]]
    dear = [row for row in previews if 'class="code ' in row or 'class="prose"' in row]
    assert dear and len(dear) < len(previews)
    assert max(len(row.encode()) for row in dear) <= worst_rendered_detail_bytes()
    # And the plant reached a lexer through both of those routes, so that budget is being held
    # rather than merely not approached: the dearest preview costs more than escaping every
    # character of it would, which is the whole of the difference between the two.
    assert max(len(row.encode()) for row in dear) > worst_stored_detail_bytes()
    stored = [row for row in previews if row not in dear]
    assert max(len(row.encode()) for row in stored) <= worst_stored_detail_bytes()
    # And no pane shows more previews than the arithmetic gives it, or more marked-up ones: a
    # kind that grew a third value would otherwise spend the ceiling unpriced.
    counts = [len(rows["detail"]) for _, rows in split]
    assert max(counts) == PANE_DETAILS
    assert max(sum(row in dear for row in rows["detail"]) for _, rows in split) == (
        DEAR_PANE_DETAILS
    )
    # ...and what the page carries whatever it holds fits the allowance the ceiling gives it.
    widest = max((chrome for chrome, _ in split), key=lambda page: len(page.encode()))
    assert len(widest.encode()) <= MEASURED_NODE_CHROME
    # The plant reached the caps, which is what makes those numbers a worst case: each header
    # string cut to its head, each list cut to its first members and saying how many it left,
    # every tree title cut to a nav width, and every preview offering the rest of itself.
    session = next(chrome for chrome, _ in split if 'data-body="session"' in chrome)
    facts = fields(session, "data-body", "session")
    assert len(facts["git_branch"]) == len(facts["version"]) == queries.HEADER_CHARS
    escaped = {
        found.count("&amp;")
        for _, rows in split
        for row in rows["tree"]
        for found in re.findall(r'<span data-field="title">(.*?)</span>', row, flags=re.S)
    }
    # No title got past the cut, and one reached it. Not every row's title is planted — a
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
    marked = "&" * queries.ENRICHMENT_CHARS + ELLIPSIS
    assert described["description"] == described["friction"] == marked
    assert described["stale"] == "stale"


def test_an_expansion_weighs_a_body_and_the_one_page_of_rows_it_lists(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """An expansion is bounded by the same cap its node's own page is, and by nothing else.

    An api call's expansion lists the tools it called, so a call that called two hundred is
    where the bound has to hold: the fragment reads one page of the level at the reader's
    `?log=`, and the way past that page is the link to the call's own page rather than more
    rows. Planted, because the densest call the corpus recorded made four tool calls — and
    planted at every cap, with `&` in each string a row prints, so what this weighs is the
    fragment at its ceiling rather than at the fixture's sizes.

    The body above those rows is weighed over all three kinds a log can open, not just the
    call's: a turn's is the dearest of them, because a turn's body is the one that carries
    what an enrichment pass wrote. So the described store, planted at the enrichment's caps
    as well.
    """
    fat = "&" * (queries.LOG_CHARS + 1)
    # The body's own strings are cut at the width a title is, not at the reader's `?detail=`.
    head = "&" * (queries.HEADER_CHARS + 1)
    session_id, source, api_call_id, recorded = one(
        store,
        "SELECT session_id, source, api_call_id, count(*) FROM live_tool_calls"
        " GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3 LIMIT 1",
    )
    turn_id, tool_id = one(
        store,
        "SELECT c.turn_id, t.id FROM live_api_calls c JOIN live_tool_calls t"
        "  ON t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id"
        " WHERE c.session_id = ? AND c.source = ? AND c.id = ?",
        [session_id, source, api_call_id],
    )
    clones = bounds.LOG.ceiling * 2
    path = enriched_plant(
        # One recorded tool call, cloned past the cap: the clone keeps every column the row
        # reads except the two that have to differ, so the rows are the store's own shape.
        (
            "INSERT INTO tool_calls (SELECT t.* REPLACE (t.id || '-planted-' || i AS id,"
            ' 90000 + i AS "index") FROM (SELECT * FROM tool_calls WHERE session_id = ?'
            " AND source = ? AND api_call_id = ? LIMIT 1) t, range(1, ?) g(i))",
            [session_id, source, api_call_id, clones + 1],
        ),
        # Then every string a tool row prints, planted past its cut: the name, the title the
        # input is read for, the command under it, and the failure that marks the row.
        (
            "UPDATE tool_calls SET name = ?, input = ?, result = ?, is_error = true",
            [fat, json.dumps({"description": fat, "command": fat}), fat],
        ),
        # And the call's own facts, which are the body above those rows: every string the
        # header cuts, planted past its cut, so the chrome is weighed at the width the body
        # reads rather than at the fixture's.
        # And the facts the bodies themselves print, planted past the cut each is read at: a
        # call's model and what it fell back from, a turn's ask and the command it was typed
        # as. A body reads them at `HEADER_CHARS`, not at the reader's `?detail=`.
        # What it said and what it thought go in too: a body previews neither, but the head of
        # what a call said is what its title falls back to.
        (
            "UPDATE api_calls SET model = ?, fallback_from = ?, text = ?, thinking = ?",
            [head] * 4,
        ),
        ("UPDATE turns SET prompt = ?, command_name = ?", [head, head]),
        *DESCRIBED_AT_EVERY_CAP,
    )
    at = f"/session/{session_id}/thread/{source}"
    mount = f"{nodes.BODY_URL}{at}/call/{api_call_id}"
    knobs = {**WORST_KNOBS, "log": bounds.LOG.ceiling}
    with TestClient(build_app(path)) as planted:
        served = planted.get(mount, params=knobs)
        # Every other kind a log opens a body for, for the widest chrome of the three.
        others = [
            planted.get(f"{nodes.BODY_URL}{at}/{kind}/{node_id}", params=knobs)
            for kind, node_id in (("turn", turn_id), ("tool", tool_id))
        ]
    assert served.status_code == 200, mount
    rows = re.findall(PRICED_ROWS["log"], served.text, flags=re.S)
    # The cap bit: the level holds twice what came back, and what came back is one page of it.
    assert len(rows) == bounds.LOG.ceiling
    assert fields(served.text, "data-log", "tools")["children"] == str(recorded + clones)
    # The fragment weighs its rows and a body, and neither part is over what it is budgeted...
    assert len(served.content) <= worst_expansion_bytes()
    assert max(len(row.encode()) for row in rows) <= worst_log_row_bytes()
    bodies = [re.sub(PRICED_ROWS["log"], "", served.text, flags=re.S)]
    for other in others:
        assert other.status_code == 200
        assert not re.findall(PRICED_ROWS["log"], other.text, flags=re.S), "it listed a level"
        bodies.append(other.text)
    assert max(len(body.encode()) for body in bodies) <= MEASURED_EXPANSION_CHROME, [
        len(body.encode()) for body in bodies
    ]
    # A turn's body is the one whose title a pass can have written, so the described store is
    # what makes that title the widest it gets rather than the prompt's own head.
    assert fields(bodies[-2], "data-body", "turn")["title"].startswith("&" * queries.TAG_CHARS)
    # ...and an expansion opens no expansion: not one of those rows carries a button that
    # would fetch another body under it.
    assert "data-view" not in served.text


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
    # Every string goes in one character past what a row prints, because that character is the
    # whole of how the page knows a value was stopped rather than ended: at the cap exactly,
    # nothing is marked and the row costs less than the arithmetic gives it.
    head = "&" * (queries.LIST_CHARS + 1)
    # Except a project path, which the filter box offers whole or not at all
    # (`view_projects.sql`): the paths that fill the box sit exactly at the width, and every
    # path past those goes one over it, where the row's own mark is. Two digits tell them
    # apart, so both halves of the page are measured against the same plant.
    root = "&" * (queries.LIST_CHARS - 2)
    name = "&" * queries.LIST_ITEM_CHARS
    kind = "&" * queries.TAG_CHARS
    over = queries.LIST_ITEMS + 2
    kinds = queries.LIST_CATEGORIES + 2
    path = enriched_plant(
        # A project path per session, each one longer than the filter box offers, so the box
        # has more suggestions than it shows. The two digits that tell them apart are the only
        # characters on the page that are not an escape...
        (
            "UPDATE sessions SET title = ?, project_dir = ? || printf('%02d', r.n)"
            " || CASE WHEN r.n > ? THEN '&' ELSE '' END"
            " FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM sessions) r"
            " WHERE r.id = sessions.id",
            [head, root, queries.LIST_PROJECTS],
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
        # to differ inside the *shown* width, not merely inside the one the query cuts to:
        # the query groups the runs after its cut, and the row cuts a character off that again
        # to make room for the mark. So two digits sit at the end of what a row shows, with one
        # more escape behind them — which puts every name past the cut and still tells them
        # apart, at 19 escapes in every 21 characters.
        (
            "INSERT INTO agent_runs (SELECT r.* REPLACE (s.id AS session_id,"
            " s.id || '-planted-' || i AS id, ? || printf('%02d', i) || '&' AS agent_type)"
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
    # Each of the row's own strings cut to its head and marked there, which is what says the
    # cut bit rather than the plant happening to end at the width.
    assert row["title"] == "&" * queries.LIST_CHARS + ELLIPSIS
    assert len(row["project_dir"]) == queries.LIST_CHARS + len(ELLIPSIS)
    assert row["project_dir"].endswith(ELLIPSIS)
    assert row["skills"].count(name + ELLIPSIS) == queries.LIST_ITEMS
    assert row["skills"].endswith("more")
    # The two counted lists reached their own caps, each name cut to the head it is grouped
    # under — the last two characters of one are the digits that tell the plants apart, and
    # the mark behind them is the escape the plant put past the cut.
    assert row["agent_types"].count(name[:-2]) == queries.LIST_ITEMS
    assert row["agent_types"].count(ELLIPSIS) == queries.LIST_ITEMS
    # The kinds of work are the one cut column with no mark: the taxonomy is closed and its
    # longest member is nine characters, so a name there is never stopped. Planted past the
    # cut all the same, because what the ceiling budgets is the width and not the vocabulary.
    assert row["work"].count(kind[:-2]) == queries.LIST_CATEGORIES
    assert ELLIPSIS not in row["work"]
    assert row["agent_types"].endswith("more") and row["work"].endswith("more")
    assert len(suggestions(one_row)) == queries.LIST_PROJECTS
    # And the pass's own line reached the head the list cuts it to, with both tags beside it —
    # the whole description is on the session's page, which is a page ceiling of its own.
    assert row["description"] == "&" * queries.LIST_CHARS + ELLIPSIS
    assert len(row["category"]) == len(row["outcome"]) == queries.TAG_CHARS
    assert ELLIPSIS not in row["category"] + row["outcome"]
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
    page's own ceiling and every title planted full of `&` — the character that escapes to
    five bytes. The failures are clones of a recorded tool call rather than invented rows;
    what is planted on each is the flag the store already records on two of them.
    """
    over = bounds.ERRORS.ceiling + 20
    # A title longer than the width a row cuts it to, so the cut bites on every row. The index
    # differs per clone because it is half of what orders the list: a page showing the first
    # `ERRORS` of a partial order is a page that cannot say what it cut.
    title = "&" * (queries.NAV_CHARS + 1)
    path = plant(
        (
            "INSERT INTO tool_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            ' ? AS name, ? AS input, true AS is_error, 9000 + i AS "index")'
            " FROM (SELECT * FROM live_tool_calls WHERE session_id = ? LIMIT 1) c,"
            " range(1, ?) g(i))",
            [title, title, FORK_ORIGIN, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        response = planted.get(f"/session/{FORK_ORIGIN}/errors")
    assert response.status_code == 200, response.text[:200]
    page = response.text
    # A page a reader jumps to stays under the ceiling with every title at its cap...
    assert len(response.content) < PAGE_BYTES
    shown = values(page, "data-error")
    assert len(shown) == bounds.ERRORS.ceiling
    # ...every one of them a planted failure cut to the width a row reads it at...
    titles = {len(fields(page, "data-error", key)["title"]) for key in shown}
    assert max(titles) == queries.NAV_CHARS + len(ELLIPSIS)
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
    # ...and one row costs no more than its markup and the title it carries.
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
    bound: dict[str, ParamValue] = {"session_id": RESUME, "log_chars": queries.LOG_CHARS}
    rows = cursorless_rows(store, Page.TIMELINE, TURN_CURSOR, bounds.CURSORLESS_TURNS, **bound)
    assert [row["turn_id"] for row in rows] == [queries.UNATTRIBUTED]
    with pytest.raises(ValueError, match="more than 0"):
        cursorless_rows(store, Page.TIMELINE, TURN_CURSOR, 0, **bound)


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
        tool = f"/fragment/{{}}/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
        fetched = [
            (planted.get(tool.format("input")), len(indents_huge)),
            (planted.get(tool.format("result")), len(indents_huge)),
            (
                planted.get(f"/fragment/record/session/{ANCESTOR}/thread/main/line/1"),
                len(overflows_the_parser),
            ),
        ]
    # Each fragment answers, and weighs the value it names plus a page of chrome at most.
    for response, stored in fetched:
        assert response.status_code == 200
        assert len(response.content) < stored + PAGE_BYTES


def printed(html: str) -> list[str]:
    """Every value a children log's rows print, as a reader sees it — the marks and all.

    Any attribute may sit in front of the field's own: the second line of a wide column is
    classed as well as named, and a pattern anchored on `data-field` reads past it.
    """
    return [
        value
        for row in re.findall(r"<tr data-child=.*?</tr>", html, flags=re.S)
        for value in re.findall(r'<span [^>]*data-field="[^"]*">(.*?)</span>', row, flags=re.S)
    ]


def test_a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every preview is truncated before it reaches a page, so no one huge value can bloat it.

    The four widths the viewer cuts to, checked at once against one planted store: a list
    row's, a tree row's title, a children log row's, and a pane's — a header's strings at one
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
    # And one tool call to dress as a command, on a page of its own: what a tool row shows is
    # read out of the input JSON rather than selected, so the two strings a command row prints
    # are cut on the way out and nowhere else. It has to be a second call, because the one
    # below keeps an input that is not JSON — the arm that shows the input as stored.
    asked_session, asked_source, asked_call, asked_id = one(
        store,
        "SELECT session_id, source, api_call_id, id FROM live_tool_calls WHERE session_id <> ?"
        ' ORDER BY session_id, source, api_call_id, "index"',
        [ANCESTOR],
    )
    # And one tool call whose own page the sweep below reads, on the session whose tool rows
    # the plant overflows.
    named_source, named_id = one(
        store,
        'SELECT source, id FROM live_tool_calls WHERE session_id = ? ORDER BY source, "index"',
        [ANCESTOR],
    )
    # Each value is planted well past its own cap, onto the real row a fixture recorded...
    long = "x" * (queries.DETAIL_CHARS + 5_000)
    path: Path = plant(
        (
            "UPDATE sessions SET title = ?, project_dir = ?, git_branch = ?, version = ?,"
            " entrypoint = ? WHERE id = ?",
            [long, long, long, long, long, SPINE],
        ),
        ("UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?", [long, SPINE, turn_id]),
        (
            "UPDATE turns SET command_name = ?, command_args = ? WHERE session_id = ? AND id = ?",
            [long, long, SPINE, command_id],
        ),
        (
            "UPDATE agent_runs SET description = ?, agent_type = ?, model = ? WHERE session_id = ?",
            [long, long, long, SPINE],
        ),
        (
            "UPDATE api_calls SET text = ?, model = ?, fallback_from = ? WHERE session_id = ?",
            [long, long, long, ANCESTOR],
        ),
        ("UPDATE tool_calls SET input = ?, name = ? WHERE session_id = ?", [long, long, ANCESTOR]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Bash", json.dumps({"description": long, "command": long}), asked_id],
        ),
    )
    with TestClient(build_app(path)) as planted:
        listing = planted.get("/sessions").text
        session = planted.get(f"/session/{SPINE}").text
        turn = planted.get(f"/session/{SPINE}/thread/main/turn/{turn_id}").text
        slash = planted.get(f"/session/{SPINE}/thread/main/turn/{command_id}").text
        run = planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
        call = planted.get(f"/session/{ANCESTOR}/thread/main/call/{DENSE_TURN_CALL}").text
        asked = planted.get(
            f"/session/{asked_session}/thread/{asked_source}/call/{asked_call}"
        ).text
        ran = planted.get(f"/session/{asked_session}/thread/{asked_source}/tool/{asked_id}").text
        named = planted.get(f"/session/{ANCESTOR}/thread/{named_source}/tool/{named_id}").text
    # ...and what each of them shows is its cap, not the value. The list's cuts are the
    # viewer's own composition rather than its query's, because its filters read the whole
    # values — a project path cut to a head would match no session under a longer one.
    row = fields(listing, "data-session-id", SPINE)
    # Marked as cut, not merely short enough: a row's strings are the ones a page multiplies,
    # so a value that ended at the width and one that was stopped there have to read apart.
    assert row["title"] == row["project_dir"] == "x" * queries.LIST_CHARS + ELLIPSIS
    # And each member of the lists beside them, at the narrower width a member takes.
    assert row["agent_types"].startswith("x" * queries.LIST_ITEM_CHARS + ELLIPSIS)
    # A path too long for the filter box to suggest whole is left out of it rather than cut:
    # half a path fills the filter in with a value that matches nothing. Bounded by the box
    # still being full — an absence read off an empty list is no absence at all.
    offered = suggestions(listing)
    assert offered and not [path for path in offered if "x" in path]
    # A tree row is a line in a sidebar, so its title takes the narrowest cut of the four —
    # the same one whatever kind of node the row stands for. Read off the tree half of the
    # page: the same `title` field names the node in three places, each at its own width.
    tree, pane = session.split('<article id="pane">')
    titles = re.findall(r'<span data-field="title">(.*?)</span>', tree, flags=re.S)
    # Cut and marked as cut: every column a title is composed from comes back one character
    # past the width, so a row that fills the line says the value went on.
    assert max(titles, key=len) == "x" * queries.NAV_CHARS + ELLIPSIS
    # A children log row is a line of a table, so it takes the next cut up — and every value
    # the plant reached is marked where it was cut, not merely short enough. Per value and not
    # at the maximum: a maximum is satisfied by whichever sibling overflowed furthest, which
    # is how a whole column of silently-truncated values hid behind a marked neighbour here.
    # What the three pages between them print: a plain turn's prompt and a slash turn's command
    # with its arguments, a tool's name, the head of what it was asked read out of an input
    # that is not JSON and out of one that is, and the command that head describes.
    reached = [value for value in printed(pane) + printed(call) + printed(asked) if "x" in value]
    assert len(reached) == 6
    assert set(reached) == {"x" * queries.LOG_CHARS + ELLIPSIS}
    # And the pane heads the node it is about at the widest of the three, because nothing on
    # the page repeats it. Every kind, not the session alone: the tree built the row the pane
    # stands on and cut its words to a tree row's width, and a title that took the tree's
    # word for it would head a turn with a third of the prompt it is about.
    #
    # Every string a header prints is cut at that width and says so, whether it heads the pane
    # or sits in the facts under it — a value that ends at the width with no mark is one a
    # reader cannot tell from a value that simply ended there.
    #
    # Swept over the whole header rather than field by field: which fields a header prints
    # grows with the store, and a list written out here would go on passing while the field
    # added beside it truncated in silence.
    headed = "x" * queries.HEADER_CHARS + ELLIPSIS
    for shown, kind in (
        (session, "session"),
        (turn, "turn"),
        (slash, "turn"),
        (call, "call"),
        (run, "run"),
        (named, "tool"),
    ):
        filled = {
            field: value
            for field, value in fields(shown, "data-body", kind).items()
            if "x" in value
        }
        # The plant reached this pane at all, so a sweep finding nothing is a sweep that
        # proves nothing...
        assert filled, kind
        # ...and everything it reached is cut to the header's width and marked there.
        assert set(filled.values()) == {headed}, (kind, filled)
    # A pane reads one node, so its strings take a header's cut — and the one value the node
    # is about takes the widest of the four, with the rest of it offered as its own fetch.
    assert fields(turn, "data-detail", "prompt")["prompt"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert inside(turn, "data-detail", "prompt", "data-whole") == ["prompt"]
    # A slash turn shows the same two widths on one page: the command it ran is a word the
    # pane leads with, cut to a header's width, and what followed it is a second value of the
    # turn, cut to a pane's and offering the rest of itself like the prompt does.
    assert fields(slash, "data-command", command_id)["command_name"] == headed
    arguments = fields(slash, "data-detail", "command_args")
    assert arguments["command_args"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert inside(slash, "data-detail", "command_args", "data-whole") == ["command_args"]
    brief = fields(run, "data-detail", "description")["description"]
    assert brief == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert fields(call, "data-detail", "text")["text"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    # A detail the page marks up is cut the same way and says so the same way, which no other
    # assertion here reaches: the mark lands inside the highlighted block, where it is one
    # more character for the lexer to make of what it will. Read back through the markup,
    # because a value that came back marked up is only cut if a reader still sees the cut.
    assert plain(block(ran, "command")) == "x" * queries.DETAIL_CHARS + ELLIPSIS
