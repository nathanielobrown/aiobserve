# Design: viewer polish — layout, NavTree, formatters, badges, bars

A batch of viewer changes Nathaniel specified and refined in `viewer_polish_questions.md` (this directory — the decision history with his answers). Every choice below is settled there; this file is the build order and the contracts.

## Problem

The node page scrolls at the page level, tool-call rows print raw JSON, subagent runs float beside the tool call that spawned them, cost badges hide what a node caused, and context bars can't show growth against the base prompt or explain a compaction. Each fix is small; together they touch the layout, the title pipeline, the NavTree assembly, and three queries — so they land as one branch with the seams below.

## Decisions

Each was grilled; the alternative and reasoning live in the questions file under the number given.

- **Layout (—)** masthead fixed, `#browser` fills the viewport, NavTree and reading pane each own their scroll; no page scrollbar. Narrow ≤900px keeps its block flow (page scroll acceptable there)
- **Footer (Q1)** `footer#citation` moves inside the reading pane's scroller, not a fixed strip
- **Crumbs (—)** new `CRUMB_CHARS = 40` cut for crumbs only; walk, stepper, tab title keep 110. Chain gains a head: `🏠 / ~/repos/hyphae / ❖ <session> / …` — 🏠 links to the session list, the home-relativized project path to that project's filtered session list
- **Sticky ancestors (—)** the open path's ancestor rows clamp at the top of the NavTree scroller, VS Code style, via CSS `position: sticky` with per-depth offsets below the sticky preset control. No depth cap
- **Popover content (Q2)** the mock below; new-input's dollar = input + cache-write cost so columns sum; `over N api calls` only when N > 1. Per-dollar washes reuse the badge meter (log share of session whole)
- **Popover position (Q3)** top aligns to the hovered row, left stays at the NavTree's right edge — plain JS (Firefox is the daily browser), repositioning on hover and on NavTree scroll
- **Formatters (Q4)** per-tool, name-driven leads with the shape-driven title as fallback for unknown tools; table below. Reverses the docs/viewer.md "never name-driven" rule — rewrite it
- **💭 (Q5)** any api-call row whose words are model speech gets 💭, including calls that also ran tools
- **Run bars (Q6)** distinct hue from turns; a run whose thread compacted renders a full-width red bar — the "maxed its window" warning. `compactions` already keys by `source`, so "its thread compacted" is one predicate; what is missing is a fixture, cut below
- **Turn bars (Q7, Q10)** three bands: base prompt (dim) / prior conversation (mid) / this turn's delta (bright). Base = the session's first main-thread api call's input-side tokens (cache read + new input); caveats accepted (first prompt rides along; resumed sessions inherit a fat base)
- **Compaction (Q9)** the ⊟ row gets a bar: dim up to the post-compaction fill, **green** band from there to the pre-compaction fill — freed context is good. Turn deltas stay clamped at 0
- **The ⊟ bar's window (—)** `compactions` records `pre_tokens` and `post_tokens` and no model, so the denominator comes from the thread: `context_window(model)` of the last non-synthetic api call of the same `session_id` and `source` at or before the boundary's timestamp, falling back to the first one after it. Same macro the api-call and turn rows already draw against (`analyze/macros.py`), so three levels of one bar cannot disagree. A thread with no api call, or a model the price table lacks, answers NULL — **and a NULL window is a bar the viewer does not draw**, which is the rule `view_nav_tree_calls.sql` already follows rather than a new degrade
- **Dual cost badge (Q8, Q11)** `$own/$total` (each half its own wash) on any row whose subtree holds run cost: turns, runs, the session (`$main/$whole`), and the ⇄ Agent tool row, whose *own* is its invoking api call's cost with the popover naming that attribution ("the api call that spawned this run"). Parallel spawns each claim the full call cost — accepted; badges are a reading aid. Rows without subtree run cost keep a single number; non-Agent tool rows stay costless
- **Nesting (Q12, Q13 + clarification)** ◎ runs move under their ⇄ Agent tool call, and one rule replaces the hoisting machinery: **a run is always visible, naturally nested under its nearest visible ancestor row.** Closed api call → run directly beneath it, no tool row; open the api call and the tool row appears with the run one level deeper. Closed turn in no-api-calls → runs dangle beneath it. Indent shifts on open are fine
- **Run lead (—)** `[implementer]` replaces `implementer —`
- **Scroll restore (—)** on load, a static JS file scrolls `[data-selected]` into view, `block: "center"` (CSP forbids inline)
- **One title everywhere (—)** emoji leads flow to crumbs, pane heading, logs — the doctrine holds

### Popover mock

```
model                claude-fable-5
context used      60,384 / 200,000
cache read          59,643  $0.0596
new input              446  $0.0089
output                 295  $0.0147
──────────────────────────────────
total added           +741  $0.0832
over 3 api calls
```

### Formatter table

Row shows `⇄ <below>`; unlisted tools keep today's `Name - <shape-driven title>`. All paths relativized.

| Tool | Row reads | Source |
|---|---|---|
| Read | `📖 src/hyphae/view/nodes.py` | file_path |
| Write | `✏️ docs/viewer.md` | file_path |
| Edit | `📝 src/hyphae/view/nodes.py` | file_path |
| Bash | `⚡ cd /Users/N…` | first line of `command` (not `description`) |
| Agent | `👉 [implementer] Survey viewer facts` | subagent_type + `description` field |
| Skill | `📕 writing` | skill, then args if present |
| SendMessage | `📬 to auditor: Request the doc-sync report` | `to` looked up in the session's `agent_runs.id` → that run's `agent_type`; anything else printed as recorded, cut to the head width |
| Grep | `🔎 pattern` | |
| Glob | `🗂 pattern` | |
| WebFetch | `🌐 url` | |
| WebSearch | `🔍 query` | |
| TodoWrite | `☑️ 3 todos` | item count |

## Call paths, current → proposed

- **Titles.** Now: SQL macro `tool_title` (`src/hyphae/analyze/macros.py:107`) builds shape-driven words; `tool_node` (`src/hyphae/view/nodes.py:556`) sets `lead = name`. Proposed: the macro (or the nav-tree queries) additionally extracts the per-tool fields above; a Python formatter registry in `nodes.py` maps name → emoji lead + words, resolves SendMessage's `to` against `Corpus.runs` (already in memory per page, `browse.py:176`), and falls back to the shape-driven title. `run_node:489` emits `[type]`; `call_node:529` emits 💭 when words are speech
- **NavTree.** Now: `_hoisted` (`src/hyphae/view/nav_tree.py:329-349`) splices runs beside their spawn call; `_tools_level:412` suppresses nesting; `CHILDREN:514-539` maps kind × preset. Proposed: hoisting deleted; `CHILDREN` gains the always-visible-run rule (runs render under their nearest visible ancestor in every preset); `(Kind.TOOL, FULL/NO_API)` nests the run like `_agent_tool:470` already does for AGENTS
- **Bars.** Now: `view_nav_tree_turns.sql` returns `fill`/`added`; `Node.bar` (`nodes.py:400`) emits `fN tN`. Proposed: the query also returns the session base (first main api call, cache read + new input); `view_compactions.sql` returns each boundary's pre/post fill beside the window it drew in, joined off the thread's nearest call; `bar` emits a third band class; run rows a hue class plus red-full when their thread compacted; ⊟ rows a green freed band
- **Badges.** Now: a node's `cost_usd` is its own thread only; tool rows carry none. Proposed: one subtree-cost rollup (runs attach to turns via spawn tool_use, to runs via `parent_agent_id`), computed once per page and read by badge and popover; `Node.meter` renders per half
- **Layout.** Now: `#nav-tree` alone scrolls (`style.css:118`), the document scrolls the rest. Proposed: `#browser` fills the viewport under a fixed masthead; `#reading-pane` gets `overflow: auto` with the footer as its last child. The "scroller stays outside the swapped element" rule (`.claude/rules/viewer-ui.md`) still holds — `#nav-tree` scrolls, `#nav-tree-rows` swaps

## File-tree diff

```
src/hyphae/view/
  nodes.py                 formatters, 💭, [brackets], CRUMB_CHARS cut, dual meter, third band
  nav_tree.py              hoisting out; always-visible-run rule; CHILDREN table
  browse.py                subtree-cost rollup beside Corpus
  fragments.py             popover layout data, attribution line
  templates/node.html      crumb head (🏠, project), footer into reading pane, scripts block
  templates/_nav_tree.html dual badge spans, band classes, sticky-ancestor hooks
  templates/fragments/numbers*.html   new popover layout
  static/style.css         viewport layout, sticky ancestors, bands, hues, washes
  static/nav-tree.js       NEW: scroll-into-view + popover alignment (CSP-clean)
src/hyphae/analyze/
  macros.py, queries/view_nav_tree_*.sql, view_numbers*.sql, view_runs.sql   per-tool fields, base, compaction fills, rollup inputs
tests/fixtures/          parallel_tools (keep `to`, add addressed runs), compaction (add the calls around each boundary), NEW dir for the compacted run
tests/view/               nav_trees.py cell table, meters, numbers, node tests; budgets re-measured
docs/viewer.md            titles rule rewrite; badges, bars, nesting
CONTEXT.md                Cost badge, Context bar, Crumb chain definitions
```

## Key contracts

- `CRUMB_CHARS = 40` beside the other cuts in `analyze/queries.py`
- Formatter registry: `dict[str, Formatter]` where a `Formatter` gets the tool's extracted fields (+ `Corpus.runs` for SendMessage) and returns `(lead, words)`; missing name → today's path untouched
- SendMessage's `to` is **one lookup and one fallback**. The recorded field holds two populations: an opaque run id (`a1cdace9d02e123ce`) and a teammate name already fit to print (`architect`, `team-lead`, `main`). An id in `Corpus.runs` prints its `agent_type`; everything else prints `to` as recorded. A second arm matching `to` against `agent_type` would return the same word the fallback already prints, so there isn't one
- The ⊟ bar's denominator: `{'pre': …, 'post': …, 'window': …}` off `view_compactions.sql`, `window` from the thread's nearest non-synthetic api call by timestamp. NULL window → no bar, no invented scale
- Subtree cost: one function, `node → (own_usd, total_usd)`, `total ≥ own`, computed from store rows already on the page; the Agent tool row's `own` is its invoking api call's `cost_usd`
- Bar classes stay pure CSS hooks (`fN tN` + new base/hue/freed classes) — no inline styles (CSP)
- `bounds.NAV_TREE_ROW_BYTES` (1870, no slack) and the byte budgets in `tests/view/budgets.py` are re-measured, not loosened blindly

## Test seam

Unchanged: served-HTML assertions through `TestClient` over redacted fixtures, values read from `data-` attributes; `tests/view/nav_trees.py` holds the expected kind × preset table. Visual-only changes (sticky clamp, bands, popover alignment) get their class/structure asserted in HTML and their look checked on `mise run gallery`.

Three behaviours reach past what the corpus records, so slice 0 cuts their evidence first. Every count below was read off the local store on 2026-08-27; the implementer re-runs the query rather than trusting the number.

- **`tests/fixtures/parallel_tools` — keep `to`.** Its three `SendMessage` calls address two runs — `general-purpose` and `auditor`, by id — but the README's tightening blanks every string under `input`, so the served page can only prove the fallback. Loosen that rule to keep `to`, and add each addressed run's opening records and `meta.json` so the id has a row to resolve against. **The sensitivity call, stated in the README:** a run id is an opaque token Claude Code minted for one session — no path, no prompt, no credential, and meaningless outside a transcript the store already keeps whole; an `agent_type` is a role word out of the repo's own `.claude/agents/`. `summary`, `message` and every other string under `input` stay redacted, because those are prose an agent wrote
- **`tests/fixtures/compaction` — add the calls around each boundary.** Four records today and zero api calls, so its session names no model and the bar has no denominator. Re-cut from the same recording (`1de7cf38-…jsonl`, CC 2.1.198, still on disk) to add the last assistant record before each `compact_boundary` and the first after — proving the nearest-prior-call rule and the rebuild in the same excerpt. That session answered on several models, which is why the window is a per-thread lookup and not a session average
- **A new fixture — a run whose own thread compacted.** No excerpt has one; the store holds 1,124 (`SELECT count(*) FROM compactions WHERE source <> 'main'`). Redact the smallest: session `6eea741c-1a7e-42a1-b242-ef3f8f02cb6b`, run `a3ad46668652aaa4f`, agent_type `general-purpose`, CC 2.1.220 — 29 records on the run thread, 9 api calls on `claude-sonnet-5`, one `auto` boundary at 174,580 → 21,486. It is a new session, so it gets its own directory; a second excerpt of a session already in the corpus would export twice under one id (`tests/conftest.py:build_store` keys by file stem)

Every model in `CONTEXT_WINDOWS` is 200,000 today, so no served value can tell the per-thread window apart from a session-wide one. The rule is chosen for meaning; what the tests can hold is the arithmetic against 200,000 and the no-bar degrade.

## Slices

Each lands green on `mise run check`; named tests are the proof.

0. **Fixture cuts.** The three excerpts under §Test seam, redacted and committed before any query reads them. Proof: `mise run check` green on the re-cut corpus, and one store query per cut — a `SendMessage` row whose `input.to` equals an `agent_runs.id` of its session, a `compactions` row whose thread holds a priced api call, and a row where `source <> 'main'`
1. **Layout + footer.** Viewport-filling panes, footer moved. Proof: existing scroller-position test still passes; gallery pages show no document scrollbar
2. **Nesting rework.** Hoisting out, always-visible runs, `[brackets]`. Proof: `test_nav_tree*` against the updated `cell` table
3. **Formatters + 💭 + crumb head + 40-char cut.** Proof: row/crumb title assertions per tool in `test_nav_tree__rows` / `test_node`
4. **Cost rollup + dual badges.** Proof: `test_nav_tree__meters` on both halves' classes and values
5. **Bars: three bands, run hue, red-full, green ⊟.** Proof: meter/band class assertions; compaction fixture
6. **Popover: content, washes, JS alignment + scroll-into-view.** Proof: `test_numbers`; alignment eyeballed in gallery
7. **Docs + budgets.** viewer.md rewrite, CONTEXT.md terms, budget re-measure. Proof: `mise run check` freshness and bounds tests

## Out of scope

- Reading-pane bodies, logs, details — untouched
- Popovers for ⊟ and bucket rows — still absent
- A sticky-ancestor depth cap — deferred until deep sessions demand one
- Preventing subagent auto-compaction — the red bar only surfaces it
- Touch/mobile beyond the existing ≤900px block layout

## Open questions

- The session list's project-filter URL parameter for the project crumb — read it off the filter form at implementation
- Hue tokens (run bar, green freed band, red full) need dark-mode variants — pick at implementation
