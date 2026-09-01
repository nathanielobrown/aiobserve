"""What bounds a page: every size the viewer serves, beside the ceiling that caps it.

A size is something a reader types, so the ceiling rather than the default is the number the
payload bound is arithmetic over. The two halves used to live apart — a default in the query
manifest, a ceiling in the app, and the composed sizes in whichever module composed them — so
answering "what bounds this page?" meant visiting four files under three naming conventions.

A size a query binds is the surface's rather than the query's — no `view_` parameter declares
a default (`view/manifest.py`) — so it is named here beside its ceiling and quoted in the
citation under whichever page bound it; a size the viewer composes around a query is defined
here outright. `tests/view/budgets.py` holds what each page and row was measured at, and the
`test_bounds*` leaves beside it do the arithmetic over this module.

Only `SESSIONS` is pinned from below there. Its ceiling is the most rows that fit under the
page, so a row that grew has to move the number rather than eat the slack silently. The other
grown-list ceilings are a reader's number that fits rather than a derived one — `PROJECTS` and
`ERRORS` are ranked lists a reader picks out of, `RECORDS` is a page of previews and `CHUNK` a
step through a file — and each sits well under what its page affords. Pinning one from below
would pin a preference to an arithmetic that never chose it.

Three responses sit outside the page bound on purpose, each priced where it is named: a fetch
of one whole value, bound by the largest single value in the store rather than by a page of
them (`view/store.py:Value`); the tail row's fetch, bound by the level it stands in (`KIN`);
and a query's citation page, which is the size of a file we ship rather than of anything a
corpus or a reader moves (`tests/view/test_bounds.py`).
"""

from typing import NamedTuple

from hyphae.analyze import queries


class Bound(NamedTuple):
    """One page size: what a reader who types none gets, and the most a URL can ask for."""

    default: int
    ceiling: int


# The three sizes every node page takes, and the whole of what a reader can ask a node URL
# for. Each is its own ceiling — `?kin=`, `?log=` and `?detail=` only go down — because the
# response's bound is arithmetic over them at the default, so there is no headroom to spend.
# How many children one expanded level of the NavTree shows before a tail row says how many it
# left; how many rows one numbered page of the pane's children log lists; and how much of the
# one value the pane is about it shows before offering the rest as its own fetch.
#
# The NavTree's is a window on a level rather than a limit on it: the tail row fetches whatever
# the window left out and stands the rows in its own place, so a reader reaches the rest of a
# level without leaving the page. That fetch is bound by the level, not by this — which is why
# the node page has a ceiling of its own (`tests/view/budgets.py:NODE_BYTES`) rather than sharing
# one every other page is weighed against. The log's is a page: it says which of how many it
# is and offers the ones either side, so a level of a hundred is read in one go and not nine.
#
# The tail's own fetch has no ceiling and is not to be given one: a reader who clicks it is
# asking for the rest of the level, and paging that would open a second window inside the one
# they just stepped out of. It costs the level less the window, at `NAV_TREE_ROW_BYTES` a row. It
# serves whichever preset the URL names, so the widest level is the widest a preset makes: in
# the canonical store on 2026-08-25 that is 1,587 tool calls under a single turn under `noapi`,
# where the api calls fold away and their tool calls hoist — 14 more than the 1,573 api calls
# the same turn shows whole. So the dearest tail fetch that corpus holds is 1.8 MB, more than
# three page ceilings, served once to a reader who asked for it, in place of rows already
# counted against that page.
#
# The window was 50, which put a tail row under most turns of a working session and made the
# NavTree a thing to expand rather than to read. Widening it spends the node page's ceiling —
# four times the rows, and the NavTree is four fifths of that page — which is why the ceiling
# moved with it rather than the window being raised inside the old one.
KIN = Bound(default=200, ceiling=200)
LOG = Bound(default=queries.LOG_ROWS, ceiling=queries.LOG_ROWS)
DETAIL = Bound(default=queries.DETAIL_CHARS, ceiling=queries.DETAIL_CHARS)

# The records browser, whose row is a preview and the `hx-get` that fetches the record whole.
RECORDS = Bound(default=queries.PAGE_RECORDS, ceiling=200)
# How long the record a page opens by itself may be. The first row arrives open, because a
# citation's cursor puts the record it names there — and a fetch nobody clicked is priced
# against the page that triggers it rather than against the value route it goes to. A record
# is the one value nothing here bounds: the canonical store archives one of 7.6 M characters,
# which renders to nine megabytes. Derived against the page ceiling at `MARKED_CHAR_BYTES` a
# character and the indentation below (`tests/view/budgets.py`), which leaves 96% of the
# canonical store's records opening on arrival and the rest one click away — where every other
# row on the page already is.
OPENED_RECORD_CHARS = 15_000
# The offload page, the one ceiling set by escaping alone rather than by a row's markup: the
# content is a file some tool wrote, and a chunk of nothing but `&` weighs five bytes a
# character. The only value the viewer serves with no row cost behind it — `offload_files.
# content` is whatever a tool wrote, and the canonical store holds one over 50 MB — so the
# page is a walk, not a fetch.
CHUNK = Bound(default=queries.CHUNK_CHARS, ceiling=60_000)
# The session list, the one page a corpus grows: 575 sessions rendered whole came to 587 KB,
# past the ceiling, so the size is bound rather than assumed small. The maximum is what fits
# under that ceiling at the *worst* cost of a row rather than the measured one — every
# character of a title or a path can escape to five bytes — so the two are the same number.
# Cut from 125 when the row grew the columns that say what a session's subagents and its turns
# were, from 110 when the row's markup was priced at the dearest row a list holds rather than at
# whichever one sorted second, and from 104 when every string a transcript wrote in a row began
# saying where it was cut. Raised to 113 when the pages became htpy components: nothing is
# written between two elements now, so a row costs its markup and its content and none of the
# whitespace a template's own shape used to leave between a row's cells. A row that costs more
# is a row a page holds fewer of.
SESSIONS = Bound(default=113, ceiling=113)
# The landing page, which a corpus grows the way it grows sessions — one row per project it
# holds, worktrees folded in. Not a size a URL carries: a reader picks a project rather than
# paging through them, so the page shows the most recently active `PROJECTS` and says how many
# it left. The row is dearer than its own markup because it carries a link holding a whole
# project path, and percent-encoding writes three bytes for every byte of it.
PROJECTS = Bound(default=queries.PAGE_PROJECTS, ceiling=queries.PAGE_PROJECTS)
# One session's failed tool calls, bound like the landing page rather than paged like the
# records browser: nothing about a session caps how often its tools fail, and a reader jumps
# to a failure rather than paging through them — so the page shows the first `ERRORS` in
# reading order and says how many it left. The prev/next stepper reads the same list, which is
# why there is one number and not two: a failure past the cap is one neither surface reaches,
# rather than one the stepper steps to and the list denies.
ERRORS = Bound(default=queries.PAGE_ERRORS, ceiling=queries.PAGE_ERRORS)

# How much of a string one row of the pane's children log carries. Not a size a URL names —
# a reader picks the next node out of a log rather than reading one — so it is the arithmetic's
# multiplicand rather than a knob. Declared with the parameter it binds (`analyze/queries.py`).
LOG_CHARS = queries.LOG_CHARS

# How deep a chain the NavTree will open, the selection counted. A session's nesting is a
# transcript's, and a transcript can nest as far as an agent spawns: the corpus reaches five,
# and a chain past this is a store shape nothing here has seen rather than a page to render,
# so `view/nav_tree.py:ancestry` raises instead of building it. The response's bound is arithmetic
# over this and `KIN`, which is what makes it a bound rather than a preference.
DEPTH = 16
# The turn rows a page renders that no cursor reaches. `session_timeline` gives one — the calls
# that answer no turn are a single group — and the NavTree reads it as the unattributed bucket's
# row. Bound because a level renders it: a timeline answering with more than one raises rather
# than serving a row nothing counted.
CURSORLESS_TURNS = 1
# How much indentation a JSON value may gain before it is served as stored instead
# (`view/text/highlight.py`). Indenting is quadratic in nesting — 10 KB of nothing but `[` indents
# to 50 MB — while real values gain very little: across the canonical store on 2026-08-07, the
# worst of a 2,000-record sample gained 3,418 characters and the largest values in it gained
# 352. What it adds is whitespace, which the formatter writes out bare, so a page pays a byte
# a character for it rather than a span.
INDENT_CHARS = 20_000
# How long a value may be and still be marked up in its own syntax (`view/text/highlight.py`).
# Characters rather than bytes: what the ceiling guards is the tokenizer's time and the markup
# a span per token adds — about five bytes of `<span class="…">` for every byte of value — and
# neither of those is counted in bytes. So a multibyte value under this ceiling is marked up
# even where its bytes run past it, which is deliberate: the cost follows the tokens.
HIGHLIGHT_CHARS = 256_000
# What one row of the NavTree may weigh, whole: its markup, a title of `queries.NAV_CHARS`
# characters that each escape to five bytes, and the knobs every link repeats. The NavTree is
# what multiplies — `1 + DEPTH * (KIN + 1)` rows spend this 3,217 times, four fifths of the
# ceiling — so it is a price to defend rather than a knob to turn: a row that grows past it
# is a page over the bound, and the answer is a slimmer row.
#
# Measured through the app rather than budgeted, at every title full of `&` and the longest
# query string a link can carry (`tests/view/test_bounds__node.py`). Pinned at exactly what it
# measures, with no slack, for the same reason: a byte of slack here is 3,217 bytes of page.
# That leaf holds it from below as well as from above, so slack cannot hide in the room the
# node page's ceiling keeps for this row's next addition.
# Most of the row is its URL, written three times: the href a reader sees, the `hx-get` htmx
# fetches, and the popover's own path under a prefix. The click's swap is written once on
# `#nav-tree-rows` and inherited; the popover's cannot be, because a swap written on the row would
# be inherited by the link inside it — so its five attributes are spelled out on every row.
# The rest is the title, the mark saying what kind of node the row is, the spend beside it, and
# the three classes the context bar is drawn from — one an edge, twelve bytes at their
# widest. A store whose agent runs carry longer ids than the recorded corpus does is a
# re-measure.
#
# Up 185 B from 1,681 when the templates went under djLint (`docs/ui-development.md`): the
# formatter writes each attribute of a tag on its own line, and Jinja renders that indentation
# into the row. Over 3,217 rows it is 595,145 B of the node page's ceiling, and it buys a
# reader nothing — what it buys is one formatter over the templates and an editor that agrees
# with `check`.
# Up 4 B from 1,866 when the row's key attribute became `data-nav-tree`.
# Up 55 B from 1,870 for the dual cost badge: the wash moved off the row and onto the value it
# washes, because a row with agent runs under it draws two of them (`_nav_tree.html`). The
# dearest row is one that draws both, so what this counts is a second badge whole.
# Up 4 B from 1,925 for the context bar's third edge: a turn stands its growth on the context
# the session opened on, so its row names a base as well as a fill and where its own share
# begins (`view/nodes.py`).
# Held at 1,929 through the marked-up titles a row now carries and the compaction badge a run's
# row draws: the row this counts is a turn's, whose URL is the longest any node has and is
# written three times, so what a run's row gained cannot overtake it. Re-measured rather than
# assumed — the leaf pins this from below as well as above, so a row that shrank would red too.
# Down 226 B from 1,929 when the row became a component: htpy writes nothing between elements,
# so the djLint indentation above and the newlines the template's own source carried are both
# gone. The markup a reader gets is the same one — what left the row is whitespace
# (`src/hyphae/view/components/nav_tree.py`).
NAV_TREE_ROW_BYTES = 1703
