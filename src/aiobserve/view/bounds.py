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


# The turn fragment's two sizes, which multiply: `calls` rows, each carrying up to `tools`
# tool rows. A call row costs about 12 KB, so the two spend the ceiling at the defaults
# themselves — which is why each is its own ceiling and `?calls=` only goes down.
CALLS = Bound(default=queries.PAGE_CALLS, ceiling=queries.PAGE_CALLS)
TOOLS = Bound(default=queries.PAGE_TOOLS, ceiling=queries.PAGE_TOOLS)
# The records browser, whose row is a preview and the `hx-get` that fetches the record whole.
RECORDS = Bound(default=queries.PAGE_RECORDS, ceiling=200)
# The offload page, the one ceiling set by escaping alone rather than by a row's markup: the
# content is a file some tool wrote, and a chunk of nothing but `&` weighs five bytes a
# character. The only value the viewer serves with no row cost behind it — `offload_files.
# content` is whatever a tool wrote, and the canonical store holds one over 50 MB — so the
# page is a walk, not a fetch.
CHUNK = Bound(default=queries.CHUNK_CHARS, ceiling=60_000)
# The session timeline's two sizes, which multiply the same way: `turns` rows, each carrying
# up to `chips` run rows. `session_digest` has no LIMIT of its own — a report quotes the whole
# digest — so these are composed around it rather than bound in it. A single list reaches its
# ceiling at `?turns=1&chips=100`, which is enough for the largest forest the corpus records
# (94 runs under one turn), so no run is out of a reader's reach.
TURNS = Bound(default=20, ceiling=20)
CHIPS = Bound(default=8, ceiling=100)
# The session list, the one page a corpus grows: 575 sessions rendered whole came to 587 KB,
# past the ceiling, so the size is bound rather than assumed small. The maximum is what fits
# under that ceiling at the *worst* cost of a row rather than the measured one — every
# character of a title or a path can escape to five bytes — so the two are the same number.
# Cut from 125 when the row grew the columns that say what a session's subagents and its turns
# were: a row that says more is a row a page holds fewer of.
SESSIONS = Bound(default=110, ceiling=110)
# The landing page, which a corpus grows the way it grows sessions — one row per project it
# holds, worktrees folded in. Not a size a URL carries: a reader picks a project rather than
# paging through them, so the page shows the most recently active `PROJECTS` and says how many
# it left. The row is dearer than its own markup because it carries a link holding a whole
# project path, and percent-encoding writes three bytes for every byte of it.
PROJECTS = Bound(default=queries.PAGE_PROJECTS, ceiling=queries.PAGE_PROJECTS)

# The most run rows one page renders however `turns` and `chips` are split. The unattached
# list is capped at `chips` and rides every page, so a page buys `turns + 1` lists of that
# size and the route checks that product — 200 is what leaves the defaults room.
CHIP_BUDGET = 200
# The turn rows a page renders that no cursor reaches, on top of the `turns` it was asked for.
# `session_digest` gives one — the calls that answer no turn are a single group — and it rides
# the last page, outside the window and outside the size a reader typed. Bound because a page
# renders it: the ceiling budgets a turn row for it, and a digest answering with more raises
# rather than serving a row nothing counted.
CURSORLESS_TURNS = 1
# What one page renders of a thread's compactions. Not a size a URL carries: the most any
# session's main thread holds is 18, so this is the arithmetic's backstop rather than a knob.
# Set just above that maximum because the bytes it does not spend are the header's, which is
# the one part of a page no size a reader types bounds.
MARKS = 20
