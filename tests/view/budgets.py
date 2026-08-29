"""What every page, row and mark of the viewer was measured at, and the arithmetic over it.

A plain module rather than a fixture file: each number here is a measurement taken against a
rendered page, and the leaves that spend them read them as constants — behind a fixture the
arithmetic would be a request rather than a sum you can follow. `tests/view/test_bounds.py`
and its neighbours are the callers; `src/hyphae/view/bounds.py` holds the caps the app
itself enforces.
"""

import os
import re

from markupsafe import escape

from hyphae.analyze import queries
from hyphae.view import bounds, nodes
from hyphae.view.format import ELLIPSIS
from hyphae.view.knobs import knobs
from hyphae.view.listing import SHOWN
from hyphae.view.store import Page
from tests.view.conftest import (
    Statement,
)

# The columns that hold whatever the agent read or wrote: one of them can be megabytes, and
# none of them belongs on a page whole. `raw` is a transcript line, `result` a tool's output,
# `input` its arguments, `text` and `thinking` a model's answer, `brief` the line a run was
# spawned with and `description` the one a pass wrote about an item — prose either way, and
# nothing bounds what a caller passes the Agent tool. `agent_type` and `model` are short in every
# session recorded so far and short by nothing: an agent definition is named by whoever writes
# it, and a model name is a string an api request carried.
# `prompt` is whatever was typed or pasted at a turn, and `command_args` whatever followed a
# slash command — the canonical store holds one of 7,947 characters. Both reach a page through
# a turn's heading, and both are cut by the timelines that select them.
FAT = (
    "raw",
    "text",
    "thinking",
    "result",
    "input",
    "content",
    "brief",
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
# What a node page may weigh, which is its own budget rather than the one above. The NavTree is
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
# Raised from 1,570,000 when the NavTree's window went from 50 children to 200 and its titles
# from 48 characters to 110: four times the rows at a quarter more each is 3.3 MB of tree, and
# the NavTree was already four fifths of the page. That is the whole of the increase, and what a
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
# it comes to 5,143,767 B, and `NAV_TREE_ROW_BYTES` is pinned from below so the 26,233 B left over
# cannot be spent by a row that quietly grew instead.
#
# Raised again from 5,170,000 for the two values a run's pane now reads off the call that
# spawned it: what the run was asked, and what it sent back. Both are markdown, so a run's is
# the first pane whose three previews are all rendered — `DEAR_PANE_DETAILS` went from two to
# three, which is 4,000 characters at 25 B more, 100,000 B, on one preview of one pane. What a
# reader gets is a run read whole where the page used to show only the line it was named by.
# The arithmetic comes to 5,243,767 B, and the slack under the ceiling is the same 26,233 B.
#
# Raised again from 5,270,000 for the context bar every row of the NavTree now draws: a fill class
# and a tip class, eight bytes a row at their widest spelling, which over 3,217 rows is
# 25,736 B — the whole of what the ceiling had spare and a thousand more. What a reader gets is
# how full the model's window was at every node of the walk, read down the NavTree rather than a
# node at a time. The arithmetic comes to 5,271,003 B, and the 28,997 B over it is what the
# next thing a row grows by is measured against.
#
# Raised again from 5,300,000 for the popover every row of the NavTree now fetches: the trigger is
# 362 B a row — its own URL, the trigger's two events, and the five attributes of the swap it
# cannot inherit — which over 3,217 rows is 1,164,554 B, forty times the slack the ceiling had.
# It is the dearest thing the NavTree has ever grown, and the row it is measured on is the one
# where the URL is longest. What a reader gets is the numbers behind the bar and the badge on
# every row without leaving the node they are reading: what a phase held in the window, what it
# added, and where its dollars went. The arithmetic comes to 6,435,557 B, and the 29,443 B over
# it is what the next thing a row grows by is measured against.
#
# Raised again from 6,465,000 when the templates went under djLint (`docs/ui-development.md`).
# The formatter writes each attribute of a tag on its own line and indents every block it
# opens, and Jinja renders that whitespace into the page: a NavTree row went from 1,681 B to
# 1,866 B, which over 3,217 rows is 595,145 B, and a log row, the chrome and the crumbs
# together add 18,000 B more. It is the second dearest thing the NavTree has ever grown, and a
# reader gets nothing at all for it — what the repo gets is one formatter over the templates
# and an editor whose output `check` agrees with. The arithmetic comes to 7,047,702 B, and the
# 32,298 B over it is what the next thing a row grows by is measured against.
#
# The row then grew 4 B when its key attribute became `data-nav-tree`, which over 3,217 rows
# is 12,868 B.
#
# Raised again from 7,080,000 for the dual cost badge: a row with agent runs under it draws two
# numbers rather than one, and the wash moved off the row onto the value it washes, so each half
# carries a step class of its own. The dearest row draws both, which is 55 B, and over 3,217 rows
# 176,935 B. What a reader gets is what a turn drove beside what it spent, without opening it.
# The arithmetic comes to 7,233,105 B, and the 26,895 B over it is what the next thing a row
# grows by is measured against.
#
# The row then grew 4 B for the context bar's third band class, which stands a turn's own growth
# apart from the context it opened on (`view/nodes.py`): 12,868 B over 3,217 rows, spent out of
# that slack rather than by raising the ceiling. The arithmetic then came to 7,245,973 B, with
# 14,027 B left.
#
# Cut to 6,500,000 when the pages became htpy components. htpy writes nothing between two
# elements, so the whitespace a formatted template put on every row of the page went with the
# templates: a NavTree row fell from 1,929 B to 1,703 B, which over 3,217 rows is 727,042 B, and
# the chrome, the crumbs, the log rows and the previews give back 34,635 B more. The arithmetic
# comes to 6,484,296 B, and the 15,704 B over it is what the next thing a row grows by is
# measured against.
NODE_BYTES = 6_500_000
# What one expansion may weigh: a node's body opened in place, inside someone else's children
# log. It is over `PAGE_BYTES` and declared here rather than derived against it, for the reason
# `bounds.OPENED_RECORD_CHARS` draws the same line the other way — a reader clicked. An
# expansion is a row of a hundred asking for the level under it, priced like the per-value
# fetches a click starts, and what bounds it is the `?log=` cap the reader is already reading
# under rather than a second cap under that. The arithmetic is `worst_expansion_bytes`: the
# body's own chrome plus one page of log rows at the widest a row gets, 621,164 B, which leaves
# 3,836 B over. A `bounds.LOG` ceiling raised past 100 spends it a row at a time — which is the
# point of naming the number, because a page of rows nobody budgeted is what a click can afford
# to hide.
EXPANSION_BYTES = 625_000

# Set this to read every measured pin below as the measurement itself rather than as a ceiling
# over it. Everyday runs read them one-sidedly on purpose (`view/bounds.py`): a change that
# shrinks a page should not have to re-pin the page. A change that moves bytes deliberately runs
# the suite under `HYPHAE_PIN_EXACT=1` instead, where a pin a page no longer reaches is a
# failure naming both numbers, and re-pins what the run named. That is what makes a re-pin
# something a run reds on rather than a step someone remembers.
EXACT_PIN = "HYPHAE_PIN_EXACT"


def exact_pins() -> bool:
    """Whether this run reads a measured pin as the measurement itself rather than a ceiling."""
    return bool(os.environ.get(EXACT_PIN))


def fits(*, measured: int, budget: int) -> bool:
    """Whether a measurement fits its pin — and under `HYPHAE_PIN_EXACT`, whether it is its pin."""
    return measured == budget if exact_pins() else measured <= budget


# What the markup around one row of the list costs, with the content the row carries taken off.
# Re-measured through the app by `test_bounds__lists.py`, every cap full of `&`, at the dearest
# row the list holds rather than at whichever one sorted second: one more row cost 4,288 B,
# against the 2,833 B of content and marks the arithmetic below prices at those caps, leaving
# 1,455 B of stacked cells, counted lists, the enrichment block and the row around them — which
# is what this and the allowance under it are pinned at together. Down 274 B when the pages
# became components, and `bounds.SESSIONS` rose from 97 rows to 113 on it.
MEASURED_SESSION_ROW_MARKUP = 1_200
# What the markup around one row's enrichment costs on top of that, with the model's own words
# taken off — the share of the 1,455 B above that the block holds, rather than a measurement of
# its own: the row is weighed whole and the pair is what the arithmetic spends. The list never
# renders the stale tag — it joins what a pass wrote
# and not the versions that would judge it — so this is the two tags and the block around them.
MEASURED_LIST_ENRICHMENT_MARKUP = 300
# What a list page weighs apart from its rows: the filter form, the project suggestions, the
# table head and the two pagers. Measured through the app by the leaf at the bottom of this
# file, with `&` planted in every suggestion and the box at its cap — 8,996 B, a worst case
# rather than a corpus observation, because the box is bound in SQL like everything else.
# Pinned at what it measured rather than over it: `HYPHAE_PIN_EXACT=1` is what re-pins it.
MEASURED_LIST_CHROME = 8_996
# What the markup around one row of the landing page costs, with the path it carries taken off,
# and what that page weighs apart from its rows: the table head, and the line saying how many
# projects it left out. Both re-measured through the app by the leaf at the bottom of this
# file, every project path planted full of `&` and the store filled past the page's ceiling:
# 1,994 B a row, of which 782 B is a planted path in its cell and in its link, leaving 1,212 B
# of stacked window cells and the row around them — and 1,225 B of chrome, which is small
# because the page carries no form, no pager and no suggestions.
MEASURED_PROJECT_ROW_MARKUP = 1_300
MEASURED_PROJECTS_CHROME = 1_225
# The same two for the page that lists where a session failed, whose row is a link to the
# failed tool call's own page, the thread it ran on and a timestamp. Measured through the app
# by the leaf at the bottom of this file, every title planted full of `&` and the session
# failing more calls than the page shows: 904 B a row, of which 550 B is a planted title at
# `NAV_CHARS`, leaving 354 B of the link and the two cells after it — and 1,278 B of chrome,
# which is small for the same reason the landing page's is: no form, no pager, no suggestions.
MEASURED_ERROR_ROW_MARKUP = 400
MEASURED_ERRORS_CHROME = 1_278

# What an expansion carries outside the rows it lists: the node's own body, the link to its
# page, and the queries it cites. The body's facts are read at `HEADER_CHARS` rather than at
# the reader's `?detail=` — an expansion previews no fat value — so this is a fraction of the
# chrome a page carries. Measured through the app by the leaf below over all three kinds a log
# opens a body for, each planted at the caps its body reads: an api call's is the dearest at
# 4,664 B, against a turn's 2,886 and a tool call's 2,386. A call's body is the one standing
# above a table, and its title is the head of what the call said.
MEASURED_EXPANSION_CHROME = 4_664

# What a row of the records browser really costs — the preview plus the row's own markup, most
# of it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` on
# 2026-08-29 over every thread it holds of a hundred records or more, as the difference between
# a page of a hundred and a page of one: the dearest thread costs 765 B a row. It is the
# canonical store this needs, because the preview is a transcript line — the fixture records are
# redacted to a few characters and project nothing about a real one.
MEASURED_RECORD_BYTES = 800

# What the markup around one row of the pane's children log costs, with the strings it carries
# taken off: a cell per column of the shape's own table, three copies of the node's URL — the
# link, the `hx-get` behind it, and the mount the View button opens through — the swap the link
# performs, the numbers that tell two children apart, and the row around them. Re-measured
# through the app by the leaf at the bottom of this file, every cap full of `&` and every knob
# at its longest — 6,112 B on an api call's row, of which 4,515 B is content at those caps and
# 150 B the knobs, leaving 1,447 B. A string at its cap is 300 escapes and the mark that says
# it was cut; the arithmetic below charges the 301 escapes the cut selected, which is 2 B a
# string more than a row can really carry. The dearest row moved from a tool call's to an api
# call's when a call's row began saying what the call said and which tools it called: nine
# columns against a tool row's seven, and the same three strings.
MEASURED_LOG_ROW_MARKUP = 1_500
# How many strings one row of a children log prints, each cut to `LOG_CHARS` and selected a
# character past it. Three is the widest row there is: an api call's row is the model that
# answered, the head of what it said, and the tools it went on to call; a tool row is the
# tool's name, the head of what it was asked, and the command that head describes. A turn row
# prints one and a run two. Listed rather than counted off `view/columns.py:COLUMNS`, because
# most of those columns are a number or a stamp; what keeps the number honest is the leaf at
# the bottom of this file, which plants every string a row can print past its cut and weighs
# the row.
LOG_ROW_STRINGS = 3
# What the control under a children log costs, with both of its links rendered: the nav around
# them, the place between them, and two copies of the node's own URL carrying the page's knobs
# and a page number. Nearly all of it is those two URLs. Measured through the app by the leaf at
# the bottom of this file, on logs driven to one row a page and read at a middle page, which is
# the only page carrying both links — 565 B, the widest of the 30 that sweep renders, 20 of them
# with both links. Driving the log to one row a page is also what writes `log=1` into the suffix
# on both of those URLs, where `worst_knob_bytes()` prices two digits: the widest pager there
# can be is 2 B over this, which the node ceiling above absorbs many thousand times.
MEASURED_PAGER_BYTES = 565
# And what the markup around one crumb of the chain down to the selection costs: the link, the
# node's key, the mark saying what kind of node the step is, and the glyph saying who named it.
# Measured the same way — 556 B less 200 B of title at `CRUMB_CHARS` and 50 B of knobs,
# leaving 306 B.
MEASURED_CRUMB_MARKUP = 330
# And what the markup around one previewed value costs — the heading, the `<pre>` and the line
# offering the rest of it — with the preview itself taken off. Measured through the app by
# `test_bounds__node.py` at 531 B, on the preview it prints as the characters the store holds.
MEASURED_DETAIL_MARKUP = 550
# How many fat values one pane previews at once. Three is the most any kind shows: a `Bash`
# call previews the command it ran, the arguments it was passed and what came back, and an api
# call what it said and what it thought. A fourth would be a kind whose pane the arithmetic
# below has not priced.
PANE_DETAILS = 3
# And how many of those the page marks up rather than printing as the characters the store
# holds. Three, which is a run's pane: the brief it was named by, the prompt it was given and
# the answer it sent back, all written by a person or a model. A turn previews the prompt and
# what followed its slash command, an api call what it said and what it thought. A tool's pane
# asks for a syntax on all three — the command as shell, the arguments as JSON, and the result
# as the file's syntax where the record names one and JSON where it does not — but a value that
# parses as none of them prints as the characters the store holds, so what a tool's pane spends
# is a question about the call rather than about the kind.
DEAR_PANE_DETAILS = 3
# What a node page carries outside its NavTree rows, its log rows and its previews: the crumbs
# down to the selection, the node's own facts, and what a pass said about it. The session is
# the widest of the eight panes — every string in its header is one a transcript wrote, and its
# two lists grow with the session — so the allowance is a session header's, cut in SQL.
# The preset control rides here too, three links carrying the node's own URL, the children
# log's own table head — a word and an icon for each column of the shape the log lists — and,
# on a pane reading a failed tool call, the step to the failure before it and the one after.
# The pane's own heading and the browser tab each carry the mark saying what kind of node the
# page is about, which is the whole of what the two of them cost here.
# What a pass wrote sits here too, and each of its two lines carries the fetch that offers the
# rest of it — a URL written twice, the way every other value a pane previews offers its own.
# The two steps above the chain sit here too, for the reason the crumbs do not: they are once a
# page rather than once a level. The project step is the dear one — it prints a project path
# and links to the session list narrowed by the whole of it, so the path is paid for twice,
# once escaped and once URL-encoded.
# Re-measured through the app by `test_bounds__node.py` at 17,750 B, which is what it is pinned
# at: `HYPHAE_PIN_EXACT=1` is what re-pins it. Up to five of its strings
# are tree titles — the page title, and the two steppers under the pane — so it moves with
# `queries.NAV_CHARS`.
MEASURED_NODE_CHROME = 17_750

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

    A row is a link to the failed tool call, named the way a NavTree row names it — the tool's
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
    knobs its link carries once.

    A crumb's own width and not a row's: a chain is many nodes on one line and cuts narrower
    than anything else that names one (`analyze/queries.py:CRUMB_CHARS`).
    """
    return MEASURED_CRUMB_MARKUP + queries.CRUMB_CHARS * ESCAPED_CHAR_BYTES + worst_knob_bytes()


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


def nav_tree_rows() -> int:
    """How many rows the NavTree of one node page holds: the root, and every level at its window.

    `KIN` children per level is the whole of it: `nav_tree.windowed` keeps the child the path
    descends through *inside* the window rather than past it, and `test_nav_tree.py` pins that. A
    rescue that added a row would put a level at `KIN + 1` and the page below 16 rows over what
    it prices.
    """
    return 1 + bounds.DEPTH * (bounds.KIN.ceiling + 1)


def worst_nav_tree_bytes() -> int:
    """What the NavTree of one node page can weigh — five sixths of the page it opens."""
    return nav_tree_rows() * bounds.NAV_TREE_ROW_BYTES


def worst_crumbs_bytes() -> int:
    """What the chain above the reading pane can weigh: a crumb a level, all the way down."""
    return bounds.DEPTH * worst_crumb_bytes()


def worst_log_bytes() -> int:
    """What one page of a children log can weigh, on a node page or inside an expansion."""
    return bounds.LOG.ceiling * worst_log_row_bytes()


def worst_details_bytes() -> int:
    """What the values one reading pane previews can weigh, each priced by how it is printed."""
    return (PANE_DETAILS - DEAR_PANE_DETAILS) * worst_stored_detail_bytes() + (
        DEAR_PANE_DETAILS * worst_rendered_detail_bytes()
    )


def worst_node_bytes() -> int:
    """The largest node page any sizes a URL can carry produce.

    A page is its chrome, the crumbs down to the selection, the NavTree beside it, the values the
    pane previews, and the log under it. The NavTree is the part that multiplies: every level of
    the open path admits `KIN` children and a tail row saying what the cap left out, and the
    path runs `DEPTH` levels deep — so `bounds.NAV_TREE_ROW_BYTES` is four fifths of the ceiling,
    and the row is pinned rather than budgeted.

    The sizes' own defaults spend it, and each of the three knobs only goes down from there —
    but a knob a reader turns down writes itself into every link on the page, so the rows are
    priced with the longest query string one can carry rather than with none.
    """
    return (
        MEASURED_NODE_CHROME
        + worst_crumbs_bytes()
        + worst_nav_tree_bytes()
        + worst_log_bytes()
        + MEASURED_PAGER_BYTES
        + worst_details_bytes()
    )


def worst_expansion_bytes() -> int:
    """What one expansion opened in a children log can weigh.

    A body where the page has its NavTree and its crumbs, and under it the level the node's own
    page lists — the same log, at the same `?log=` cap and one column narrower, because no row
    inside an expansion opens another. So an expansion prices as a page of log rows plus a
    body, and the cap that bounds the log on a page is what bounds it here. `EXPANSION_BYTES`
    is what this is checked against.
    """
    return MEASURED_EXPANSION_CHROME + worst_log_bytes()


def worst_session_list_bytes() -> int:
    """What a full page of the session list can weigh: its chrome, and a ceiling of dear rows."""
    return MEASURED_LIST_CHROME + bounds.SESSIONS.ceiling * worst_session_row_bytes()


def worst_projects_page_bytes() -> int:
    """The same for the landing page, whose rows are paths someone named."""
    return MEASURED_PROJECTS_CHROME + bounds.PROJECTS.ceiling * worst_project_row_bytes()


def worst_errors_page_bytes() -> int:
    """The same for a session's errors, which grows with how often its tools failed."""
    return MEASURED_ERRORS_CHROME + bounds.ERRORS.ceiling * worst_error_row_bytes()


def worst_records_page_bytes() -> int:
    """The same for the records browser, which is rows of previews and nothing else."""
    return bounds.RECORDS.ceiling * worst_record_bytes()


def node_spare() -> int:
    """What `NODE_BYTES` leaves over the arithmetic: the rounding every ceiling here carries."""
    return NODE_BYTES - worst_node_bytes()


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
