# Design: the session page's context timeline

Designed against `data/traces.duckdb`, schema 7. That store currently holds one recorded
session (`6e3e71dd-a9fe-4fcb-9471-c9bb2e2e82c9`: 15 main-thread turns, 57 api calls, 0
compactions) — small enough to prove the query's shape (verified below) but not to measure a
worst-case page. Corpus-scale numbers this design leans on (268-turn monster session, the 500
KB page ceiling, its 483 KB worst measured shape) are inherited from `plans/trace-viewer/
design.md`'s 2026-08-07 measurements against the canonical store, not reverified here — flagged
wherever they carry weight below.

## Problem

The session page counts calls and cost per turn (`view_turn_calls.sql`, `session_digest.sql`)
and shows a compaction's `pre_tokens → post_tokens` as one line of text on the turn it preceded
(`_parts.html:timeline`). Nothing plots the shape between those two numbers: how a thread's
context climbed call over call, what fraction was fresh input versus reused cache, or how a
compaction's drop compares to the climb that led to it. A reader who wants that shape today
either opens each turn's calls one at a time or drops into `aiobserve query context_reloads`,
which answers a cross-session question, not "this session, over time."

Three things fix the shape:

- **Token-type only.** Content-source composition (system prompt vs. tool results vs. files)
  is deferred — it isn't authoritative from the schema and would mean estimating from block
  content. This phase draws only the four fields `ApiCall` already carries as scalars:
  `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`
- **No CDN, no JS chart library.** The viewer vendors htmx and one stylesheet and nothing else
  (`plans/trace-viewer/design.md`'s "Decisions"). A chart here is server-rendered inline SVG or
  it doesn't belong in this codebase
- **Bounded by construction, not by corpus luck.** The session page's existing ceiling (500 KB,
  raised once already for enrichment) already sits at 483 KB in its worst measured shape — about
  17 KB of headroom. This panel adds a fixed cost to every session page regardless of session
  size, so its byte budget has to be arithmetic, not observed on today's sessions

## Call paths, current → proposed

Current: `session_page` (`view/app.py`) reads `Page.SESSION_HEADER`, `Page.TIMELINE`,
`Page.RUNS`, `Page.COMPACTIONS`, builds `threads.session_threads(...)`, and renders
`session.html`. No query touches `input_tokens`/`output_tokens`/`cache_read_tokens`/
`cache_creation_tokens` in aggregate; `view_turn_calls.sql` shows them per call only inside a
turn's own expand fragment, one turn at a time.

Proposed: `session_page` adds one more whole-session read —
`page_rows(connection, Page.CONTEXT_TIMELINE, session_id=session_id, source=MAIN_SOURCE,
max_points=queries.CONTEXT_POINTS)` — and passes the rows through a new pure module,
`view/chart.py`, alongside the compaction rows it already fetched:
`chart.build(rows, markers)`. The result is a `Chart | None` the template turns into two
`<svg>` blocks. No new route: the query and the render both ride the session page's existing
GET, the same way the header and the turn timeline do.

## File-tree diff

```
src/aiobserve/analyze/queries/view_context_timeline.sql   new query
src/aiobserve/analyze/queries.py        + CONTEXT_POINTS const, + manifest entry
src/aiobserve/view/store.py             + Page.CONTEXT_TIMELINE = "view_context_timeline"
src/aiobserve/view/chart.py             new: bucketed rows -> Chart (pure, mirrors threads.py)
src/aiobserve/view/threads.py           `_lands` promoted to a module-level helper chart.py reuses
src/aiobserve/view/app.py               session_page reads Page.CONTEXT_TIMELINE, builds Chart
src/aiobserve/view/templates/_chart.html   new: the two <svg> partials
src/aiobserve/view/templates/session.html  + context-chart section, between header and timeline
tests/view/test_chart.py                new: bucketing/geometry and the byte-budget arithmetic
tests/view/test_bounds.py               + the new section's cost folded into the page ceiling
docs/viewer.md                          + what the panel shows, in the surface-bounds table
```

## Key contracts

**`view_context_timeline.sql`** (`Scope.KEYED`; params `session_id`, `source`, `max_points`).
One row per main-thread turn that made at least one api call, aggregated to at most
`$max_points` rows when the thread holds more turns than that. A turn with no api call carries
no row — there is nothing to plot for it, the same silence `session_digest`'s per-turn spend
CTE already tolerates.

```sql
WITH turn AS (
    SELECT id AS turn_id, "index" AS turn_index,
        coalesce(started_at, (SELECT min(started_at) FROM live_api_calls c
            WHERE c.session_id = $session_id AND c.source = $source AND c.turn_id = t.id)) AS started_at
    FROM live_turns t WHERE session_id = $session_id AND source = $source
), call AS (
    SELECT turn_id, "index" AS call_index, input_tokens, output_tokens,
        cache_read_tokens, cache_creation_tokens, cache_5m_tokens, cache_1h_tokens
    FROM live_api_calls
    WHERE session_id = $session_id AND source = $source AND turn_id IS NOT NULL
), per_turn AS (
    SELECT
        turn.turn_index, turn.started_at,
        row_number() OVER (ORDER BY turn.turn_index) - 1 AS rn,
        count(*) OVER () AS total_turns,
        count(*) AS api_calls,
        -- Context size does not sum across calls, so a turn's value is its last call's —
        -- the size the chart would show at that point if it drew every turn.
        arg_max(call.input_tokens + call.cache_read_tokens + call.cache_creation_tokens,
            call.call_index) AS context_tokens,
        sum(call.input_tokens) AS input_tokens,
        sum(call.output_tokens) AS output_tokens,
        sum(call.cache_read_tokens) AS cache_read_tokens,
        sum(call.cache_creation_tokens) AS cache_creation_tokens,
        sum(call.cache_5m_tokens) AS cache_5m_tokens,
        sum(call.cache_1h_tokens) AS cache_1h_tokens,
        bool_and(call.cache_5m_tokens IS NOT NULL) AS split_known
    FROM turn JOIN call ON call.turn_id = turn.turn_id
    GROUP BY turn.turn_index, turn.started_at
), bucket AS (
    SELECT *, rn // greatest(ceil(total_turns::DOUBLE / $max_points)::BIGINT, 1) AS bucket_index
    FROM per_turn
)
SELECT
    bucket_index,
    min(turn_index) AS first_turn_index, max(turn_index) AS last_turn_index,
    min(started_at) AS started_at, sum(api_calls) AS api_calls,
    arg_max(context_tokens, turn_index) AS context_tokens,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(cache_read_tokens) AS cache_read_tokens, sum(cache_creation_tokens) AS cache_creation_tokens,
    sum(cache_5m_tokens) AS cache_5m_tokens, sum(cache_1h_tokens) AS cache_1h_tokens,
    bool_and(split_known) AS split_known, any_value(total_turns) AS total_turns
FROM bucket GROUP BY bucket_index ORDER BY bucket_index;
```

`rn // greatest(ceil(total_turns / $max_points), 1)` is the whole bucketing rule and it needs
no branch: at or under `$max_points` turns, the divisor is 1 and every bucket holds exactly one
turn, so a 15-turn session (the one this design verified against) renders unbucketed — every
point is a real turn, nothing aggregated away. Above that, it groups consecutive turns in
index order. `context_tokens` takes `arg_max(..., turn_index)` — the last turn's value — because
context size is a snapshot, not a sum; the four composition columns sum because they are spend.
Verified against the recorded session: context climbed turn 3 → 14 as 51,200 → 54,105 → 56,139
→ 60,595 → 65,878 → 68,447 → 104,431 → 152,213 tokens, with `split_known = true` on every turn
(matches `docs/schema.md`'s scan: every assistant record in the mycelia corpus carries the
5m/1h split as of 2026-08-07). No compaction ran in this session, so the drop path is unverified
here — `docs/schema.md`'s `compactMetadata` fixture (`tests/fixtures/compaction/`, CC 2.1.198)
is the evidence that `pre_tokens`/`post_tokens` exist and mean what `view_compactions.sql`
already assumes.

`CONTEXT_POINTS = 100`, beside `PAGE_CALLS` and friends in `queries.py`. Not a size a URL
carries — like `chip_chars`, the viewer always binds the default; `aiobserve query
view_context_timeline` can still override it because the manifest gives every parameter one.

**`view/chart.py`.** Pure functions over the rows above, mirroring `threads.py`: nothing here
reads a store or a request.

```python
TOKEN_TYPES = ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens")

@dataclass(frozen=True)
class Chart:
    context_line: str                          # <polyline points="…"> attribute
    bands: tuple[tuple[str, str], ...]          # (token type, <path d="…">), stacked bottom-up
    compaction_marks: tuple[tuple[float, int, int], ...]   # (x, pre_tokens, post_tokens)
    x_ticks: tuple[tuple[float, int], ...]      # (x, turn_index), sparse
    y_max: int                                  # context_tokens scale for the axis label
    bucketed: bool                              # $max_points cut a real turn to an average

def build(rows: Sequence[Row], compactions: Sequence[Row]) -> Chart | None:
    ...
```

`build` returns `None` when `rows` is empty — a session whose turns made no api call (a
`/model`-only session, or one still in its first turn) gets no panel rather than an empty one.
Compaction placement reuses the turn-landing rule `threads.py:_lands` already implements for
positioning a marker between turns by timestamp; it moves to a module both `threads.py` and
`chart.py` import; whether that module is `view/chart.py` itself or a new small one is left to
the implementer.

**Rendering carries no transcript content.** Every value the two `<svg>` blocks draw is an
integer this code computed — a pixel coordinate, a token count, a turn index. No string a
transcript wrote (a compaction's `trigger`, a model name) appears in this panel; that's already
on the turn timeline directly below it. This is a deliberate cut, not an oversight (see
Decisions): it is what keeps the byte budget arithmetic instead of subject to the five-byte
escaping factor every other panel in this viewer has to budget for.

**Insertion point.** `session.html`, a new `<section id="context-chart">` between the
`session-header` section and `{{ parts.timeline(...) }}` — an overview before the turn-by-turn
detail, the same order a reader meets cost-per-turn today. Rendered inline with the rest of the
page's first GET, not an htmx fragment: the query is one whole-session aggregate with no fat
columns, cheaper than the header query it sits beside, so lazy-loading it would spend a round
trip on a couple hundred numbers. It also ignores the turn timeline's `turns`/`chips` paging —
scoping the chart to one page of turns would answer "what does this page look like," and the
question this panel exists to answer is "what does the whole session look like."

**Payload budget**, projected rather than measured (no session in reach is large enough to
render):

| Element | Bound | Bytes |
| --- | --- | --- |
| Context line | 1 path, ≤100 points × 8 B (`"NNN,NNN "`, pixel-space so ≤3 digits a side) | ≤800 B |
| Composition bands | 4 bands × 2 edges × ≤100 points × 8 B | ≤6,400 B |
| Compaction marks | ≤20 (`MARKS`) × one line + a numeric-only title, ~50 B | ≤1,000 B |
| Axis ticks, legend, `<svg>` wrappers | fixed chrome | ~2,000 B |
| **Total** | | **≤10,200 B** |

Against the ~17 KB of headroom `docs/viewer.md`'s 483/500 KB worst-measured-shape leaves, this
fits without moving the ceiling — the same choice enrichment didn't have (it needed the raise).
**This table is arithmetic over an SVG format that has not been rendered once**, and this
codebase's own history (`plans/trace-viewer/design.md`'s "As built: the payload audit") is that
a first guess like this runs over once real markup and escaping are counted. Slice 1 has to
render this against the largest session it can reach and correct the table the same way that
audit did, before the panel is considered bounded rather than hoped-bounded.

## Chosen test seam

Two levels, matching the trace-viewer design's own pattern: `tests/view/test_chart.py` unit
tests `view_context_timeline.sql`'s bucketing (`rn // …`) against planted row counts —
under, at, and over `$max_points` — and `chart.build`'s geometry against invented rows (a
climbing series, a series with a compaction between two points, a series with a null-split
turn), checked at the level of "the polyline has the right point count and monotonic x." A
route-level test over `FastAPI TestClient` (the seam `plans/trace-viewer/design.md` already
uses) asserts the section renders in `session_page`'s response for the one fixture session with
turns, is absent for a session whose turns made no call, and that
`tests/view/test_bounds.py`'s all-`&`-planted page stays under the ceiling with the panel
included — the same discipline that measures the header and the timeline today, extended to
cover this section rather than treated as a rounding error against their budgets.

## Slices

1. `view_context_timeline.sql` + manifest entry, `Page.CONTEXT_TIMELINE`, `view/chart.py` with
   the context-size line only (no composition bands yet), rendered into `session.html`. Proves
   the seam: query → bucket → geometry → template, on the one session this design verified
   against and on a planted large-turn-count fixture that forces bucketing. Verified by
   `test_chart.py`'s bucketing/geometry tests, a route test asserting the section appears, and
   `mise run check`
2. Composition bands (stacked area, four token types), compaction markers on both charts, the
   `cache_5m_tokens`/`cache_1h_tokens` split — and the payload-budget re-measurement the table
   above calls for, folded into `test_bounds.py`

## Decisions

- **Turn granularity over per-api-call** — a turn's last-call context size is what an operator
  actually asks ("how big was my context by the time each of my turns finished"), it's what the
  existing turn timeline already indexes by, and it keeps the point count near the corpus's
  historical turn maximum (268) rather than its call maximum (single turns already hold up to
  774 calls per `plans/trace-viewer/design.md`) — bucketing would bite almost every session
  instead of almost none. Rejected: per-call, which shows intra-turn tool-loop growth the
  turn-level line smooths over; named as an open question below rather than built, since nothing
  here establishes that smoothing loses something an analysis needs
- **Server-rendered inline SVG over vendoring a JS chart library** — matches the viewer's
  standing rule (no CDN, vendored assets only); a few hundred numbers don't justify a new
  dependency and a new client-side execution surface when every other panel in this viewer
  renders server-side
- **Bucketing in the query, not in `chart.py` or the browser** — keeps the payload bound where
  every other bound in this viewer already lives: in SQL, arithmetic, checked by a test, not a
  client-side decision the server can't verify before it ships a response
- **Numeric-only rendering, no transcript strings in this panel** — a compaction's `trigger` and
  a call's model name already appear on the turn timeline directly below; repeating them here
  would reintroduce the five-byte escaping factor into a budget that specifically doesn't carry
  it. Rejected: putting per-point `<title>` tooltips with exact figures on every point — at
  ≤100 points × ~45 B that's another ~4.5 KB, spending most of the remaining headroom on
  hover text a click into the turn below already gives for free
- **Whole-session scope, not scoped to the visible `turns`/`chips` page** — the overview is the
  point; a chart that only covered the visible page would need its own paging story and would
  answer a narrower question than the one this panel exists for
- **Composition includes `output_tokens`** even though it isn't part of "context size" (that's
  `input_tokens + cache_read_tokens + cache_creation_tokens` only) — Nathaniel's token-type list
  named it explicitly. The two charts' totals are deliberately different numbers; the panel
  should say so once, near the composition legend, so a reader doesn't expect the bands to sum
  to the line

## Out of scope

- Content-source composition (system prompt vs. tool results vs. files) — not authoritative
  from the schema without estimating from block content; deferred per the brief
- Per-api-call granularity — see Decisions; a later phase if turn-level smoothing turns out to
  hide something worth seeing
- Per-point tooltips with exact figures — the shape and the compaction markers are v1; exact
  numbers for one turn are a scroll away on the timeline below
- The run page. This design scopes to the session page only, per the brief; a run's own thread
  is a smaller version of the same query and template, but nothing here builds it
- Any client-side interactivity beyond what a static `<svg>` gives for free — no zoom, no pan,
  no JS

## As built: the payload audit

Slices 1 and 2 landed together. The projected table above ran about 13% under, in the place
the design predicted it would: the compaction rules cost more than a guess at "one line and a
numeric title" allowed, because a rule is drawn on *both* charts and each carries markup a
first estimate leaves out. Measured through the app at the widest shape the panel can take —
`CONTEXT_POINTS` points on both charts, `bounds.MARKS` rules across each, nine-digit token
labels, six-digit turn indices, and the grouping note a bucketed thread carries:

| Element | Bound | Projected | Measured |
| --- | --- | --- | --- |
| Context line | 1 `<polyline>`, 100 points × 8 B | ≤800 B | 645 B |
| Composition bands | 4 `<path>` × 2 edges × 100 points × 8 B | ≤6,400 B | 5,931 B |
| Compaction rules | 20 × 2 charts, titled on the context chart only | ≤1,000 B | 3,144 B |
| Axis ticks | 5 × 2 charts | (in chrome) | 466 B |
| Legend, captions, `<svg>` wrappers, grouping note | fixed chrome | ~2,000 B | 1,326 B |
| **Total** | | **≤10,200 B** | **11,512 B** |

Budgeted at 12,000 B in `tests/view/test_bounds.py`, which folds it into `worst_session_bytes`
as a fixed cost beside the compaction markers'. The largest legal session page moves from
483 KB to 495 KB of the 500 KB ceiling — it fits, and the ceiling did not move, but the
headroom is now 5 KB rather than 17 KB. The next surface added to this page needs its own
audit before it is written, not after.

The measurement is planted rather than recorded, and that is the remaining weakness: no
session in reach of the test suite or of `data/traces.duckdb` answers in more than three turns,
so the shape at the cap is a store built to have it. What makes it evidence anyway is that the
panel carries no transcript content — every byte it draws is a coordinate or a digit this code
computed, so a planted thread and a real one of the same length render identically. What is
*not* verified here is the 483 KB baseline this sits on top of, which remains
`plans/trace-viewer/design.md`'s 2026-08-07 arithmetic over the canonical store.

Three deviations from the contracts above, each small enough to reverse:

- `build` returns None below two points rather than only on no rows. One point draws a
  one-vertex polyline and a zero-width band, which is an empty panel with axes — the same
  thing the design's None exists to avoid. Eight of the nine fixture sessions land here
- The `turn` CTE drops the design's `coalesce(started_at, …)`: `turns.started_at` is NOT NULL
  in the store's schema, so the fallback was a branch nothing can reach
- `TOKEN_TYPES` is a `StrEnum` and the three tuple members of `Chart` are `NamedTuple`s
  (`Band`, `Mark`, `Tick`), per `.claude/rules/python.md`. `Chart` also carries `spend_max`
  beside `y_max`: the two charts are drawn against different scales, and one field could only
  serve one of them

## Open questions

- Whether per-turn smoothing hides a shape worth seeing (a long tool-loop turn's context growth
  mid-turn) — no session in reach is large enough to show whether this matters. Settle it by
  reading one multi-hundred-call turn's `view_call_tools` output against this panel's turn-level
  line once both exist, on the canonical store
- Whether `_lands`'s promotion out of `threads.py` belongs in `view/chart.py` or a third module
  — an implementation detail slice 1 can settle without changing this design
- Whether the payload table above survives contact with a real large session — flagged in
  Key contracts; slice 1 must re-measure before this section is bounded rather than hoped
