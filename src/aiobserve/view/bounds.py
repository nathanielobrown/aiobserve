"""What bounds a page: every size the viewer serves, beside the ceiling that caps it.

A size is something a reader types, so the ceiling rather than the default is the number the
payload bound is arithmetic over. The two halves used to live apart — a default in the query
manifest, a ceiling in the app, and the composed sizes in whichever module composed them — so
answering "what bounds this page?" meant visiting four files under three naming conventions.

A size a query binds keeps its default in the manifest, where the parameter is declared, and
is named here beside its ceiling; a size the viewer composes around a query is defined here
outright. `tests/view/test_bounds.py` does the arithmetic over this module.
"""

from typing import NamedTuple

from aiobserve.analyze import queries


class Bound(NamedTuple):
    """One page size: what a reader who types none gets, and the most a URL can ask for."""

    default: int
    ceiling: int


# The three sizes every node page takes, and the whole of what a reader can ask a node URL
# for. Each is its own ceiling — `?kin=`, `?log=` and `?detail=` only go down — because the
# response's bound is arithmetic over them at the default, so there is no headroom to spend.
# How many children one expanded level of the tree shows before a tail row says how many it
# left; how many rows the pane's children log lists; and how much of the one value the pane
# is about it shows before offering the rest as its own fetch.
KIN = Bound(default=25, ceiling=25)
LOG = Bound(default=queries.LOG_ROWS, ceiling=queries.LOG_ROWS)
DETAIL = Bound(default=queries.DETAIL_CHARS, ceiling=queries.DETAIL_CHARS)

# The records browser, whose row is a preview and the `hx-get` that fetches the record whole.
RECORDS = Bound(default=queries.PAGE_RECORDS, ceiling=200)
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
# were, and from 110 when the row's markup was priced at the dearest row a list holds rather
# than at whichever one sorted second: a row that costs more is a row a page holds fewer of.
SESSIONS = Bound(default=104, ceiling=104)
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

# How deep a chain the tree will open, the selection counted. A session's nesting is a
# transcript's, and a transcript can nest as far as an agent spawns: the corpus reaches five,
# and a chain past this is a store shape nothing here has seen rather than a page to render,
# so `view/tree.py:ancestry` raises instead of building it. The response's bound is arithmetic
# over this and `KIN`, which is what makes it a bound rather than a preference.
DEPTH = 16
# The turn rows a page renders that no cursor reaches. `session_digest` gives one — the calls
# that answer no turn are a single group — and the tree reads it as the unattributed bucket's
# row. Bound because a level renders it: a digest answering with more than one raises rather
# than serving a row nothing counted.
CURSORLESS_TURNS = 1
# How long a value may be and still be marked up in its own syntax (`view/highlight.py`).
# Characters rather than bytes: what the ceiling guards is the tokenizer's time and the markup
# a span per token adds — about five bytes of `<span class="…">` for every byte of value — and
# neither of those is counted in bytes. So a multibyte value under this ceiling is marked up
# even where its bytes run past it, which is deliberate: the cost follows the tokens.
HIGHLIGHT_CHARS = 256_000
# What one row of the tree may weigh, whole: its markup, a label of `queries.NAV_CHARS`
# characters that each escape to five bytes, and the knobs every link repeats. The tree is
# what multiplies — `1 + DEPTH * (KIN + 1)` rows spend this 417 times, four fifths of the
# ceiling — so it is a price to defend rather than a knob to turn: a row that grows past it
# is a page over the bound, and the answer is a slimmer row.
#
# Measured through the app rather than budgeted, at every label full of `&` and the longest
# query string a link can carry (`tests/view/test_bounds.py`). Pinned at exactly what it
# measures, with no slack, for the same reason: a byte of slack here is 417 bytes of page.
# Nearly all of the row is its URL written twice — the href a reader sees and the `hx-get`
# htmx fetches — because the swap the two of them perform is written once on `#tree-rows` and
# inherited. A store whose agent runs carry longer ids than the recorded corpus does is a
# re-measure.
TREE_ROW_BYTES = 914
