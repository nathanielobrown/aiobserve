# Viewer feedback round 3 — plan

The next viewer iteration, for the agent that runs it: tool-derived titles for silent API calls, context fullness in the navbar, cost moved from bar to badge, a hover popover with the exact numbers, and two small fixes. Every fork was settled in an interview; `questions.md` in this directory holds the decision record — read it before reopening any choice here.

Work the clusters in order — cluster 4 needs the bar cluster 3 frees, and cluster 5 needs cluster 4's math. Clusters 1 and 2 land independently; cut from the tail if the run stretches. One branch per cluster, atomic commits, `pr` skill per cluster.

## How to work this plan

- **Every file:line and behavior claim below is a hypothesis.** Verified 2026-08-26 against `main`; re-verify before building on one
- **TDD** per `.claude/rules/testing.md`. Where a test pins behavior this plan changes (the API-call title fallback, `TREE_ROW_BYTES`), flip the test first, deliberately
- **Bounds are measured, not guessed** (`view/bounds.py`). New classes on tree rows, popover triggers, and any new fragment route re-derive the affected budgets by measurement
- **Verify rendered behavior in the gallery or on a throwaway port with ephemeral Playwright.** Port 8477 is Nathaniel's live viewer; don't touch it
- **Design against recorded sessions.** The context math below assumes the usage shape in `tests/fixtures/spine/` (CC 2.1.221). `<synthetic>` calls report zero tokens (`docs/schema.md`) — the derivations must skip them or an interrupt fakes an empty window

## Decisions already made (don't re-open)

- **API-call title derivation changes everywhere**: text head → tool-derived → model name. One derivation, used by pane heading, tree, crumbs, walk controls, browser tab. The children log keeps naming API-call rows by model — the one documented exception stands
- **Tool-derived format**: first tool call's full title, then per-tool counts of the remaining calls — `Bash — Remove temp mutation clones +2(Bash) +1(Read)`. Count groups in order of first appearance among the remaining calls
- **Inline bar = context, bar-in-bar, linear**: track = the model's context window, dim fill = context at end of node, brighter tip = what the node added. Linear, not log — fullness against a limit is the story
- **Bar scale = per-model window constant.** No guessed auto-compact threshold; an observed-compaction tick (from `compactions.pre_tokens`, `trigger = 'auto'`) is a follow-up, not this round
- **Cost = badge behind the dollar value**, warm amber deepening with spend, reusing the existing log share scale (`nodes.meter`, 10 steps over 3 decades)
- **Popover on hover** carries the context numbers (cached / new input / output / window) *and* the cost legend split by price category, Artificial Analysis style
- **Tool rows get no token numbers** — a tool call carries no usage; its popover shows `result_chars`, a stored number, as the honest proxy
- **No reasoning segment.** `usage.output_tokens` includes thinking with no split; estimating from thinking chars was considered and declined

## Key contracts

**Context math**, per node kind, all from stored `api_calls` columns:

| Node | End-of-node fill | Added (the bright tip) |
|---|---|---|
| API call | `cache_read + cache_creation + input + output` | `cache_creation + input + output` |
| Turn | last non-synthetic call's fill | end fill − previous sibling turn's end fill, clamped at 0 (a mid-turn compaction makes it negative; the popover states the real delta) |
| Run | last non-synthetic call's fill on its thread | equals fill — a subagent's whole window was built during the run |
| Session | main thread's last non-synthetic call's fill | not shown; the session bar reads fullness only |
| Tool call, compaction, buckets | no bar | — |

**`CONTEXT_WINDOWS`**: a per-model token table beside `PRICES` in `extract/pricing.py`, keyed by the exact `message.model` string like `PRICES` is. A model absent from the table renders no bar and the popover says the window is unknown — mirror the `unpriced` handling, don't crash and don't default. The view imports this leaf module; it's pure constants, but it is a new view→extract import — flag in the wrap-up if a shared home (e.g. `model.py`) reads cleaner in practice.

**Cost split**: refactor `extract/pricing.py::compute_cost` to expose the per-category split (input, output, cache read, cache write) it already computes internally; the total stays the stored `cost_usd`. The popover prices a phase by querying its calls' token sums `GROUP BY model` and pricing each group in Python — a phase can mix models, so the split can't come from summed tokens times one price.

## Cluster 1 — small fixes

1. **`mise run gallery --port`.** `tests/gallery/serve.py` pins `PORT` one past the viewer's; add an argparse flag defaulting to it. `mise run gallery --port 9001` must pass through (flags after the task name reach the task)
2. **Left-align all navbar text.** No `text-align: center` exists in `style.css`, so the centering is inherited or browser-default (a `<button>` centers its label unless told otherwise — `li.more > button` says `inherit`, but check what it inherits). Reproduce in the gallery, find every centered case, and assert left alignment on `#tree` text generally rather than per-element

## Cluster 2 — tool-derived API-call titles

Today `view_tree_calls.sql` selects `text_head` and `model`, and `nodes.call_node` falls back `text_head → model`. The new middle step aggregates the call's tool calls: the `tool_title` macro (`analyze/macros.py`) already derives `Bash — Remove temp mutation clones` per tool call; the API-call title needs the first tool's title plus grouped counts of the rest.

- One derivation, shared by every query that titles an API call — the tree query, the call pane header, and the walk controls' level reads. Whether it's a SQL macro over a correlated subquery, a join each query repeats, or Python composition over a `tool_titles` list the queries already return (`view_turn_calls.sql` aggregates one today) is yours to choose — name the choice in your wrap-up
- The count suffix must survive the `$nav_chars` cut: budget the leading title at nav width minus the rendered suffix, not the other way round
- Update the title rules in `docs/viewer.md` (the "One title names a node everywhere" section) in the same PR

## Cluster 3 — cost moves to a badge

- Replace the spend meter (`style.css` "spend meter" block, classes `s1`–`s10` on `li.node`) with a badge: the `data-field="cost_usd"` span in `_tree.html` gets a background tinted by the same step class, amber deepening with the step. The step machinery (`nodes.meter`, `_share`) is untouched — only the CSS changes what a step looks like
- The policy blocks inline `style`, so the badge tint is class-per-step like the bar was
- Keep the badge legible against both themes (the accent already lightens for dark rows — follow that pattern)
- `docs/viewer.md` documents the bar-as-share today; rewrite that paragraph for the badge

## Cluster 4 — context bar

- **Rendering under the no-inline-style policy**: two custom-property class families on the row — fill steps and added steps (5% linear steps; `.f14 { --ctx-fill: 70% }`) — composed by one rule into the three-layer background the current meter already uses: track full width, bright layer sized `var(--ctx-fill)`, dim layer sized `calc(var(--ctx-fill) - var(--ctx-added))` painted over it, so the visible bright part is the tip. Small additions render invisibly at 5% steps; accepted — the popover carries the numbers
- **Derivations** per the contract table above, computed in the tree SQL (`view_tree_calls.sql`, `view_tree_turns.sql`, and the run/session levels), skipping synthetic calls. Turn deltas are a lag over sibling turns in the same query
- **`CONTEXT_WINDOWS`** per the contract. Seed it with the models the corpus records (the `PRICES` keys are the census); a `[1m]`-suffixed or otherwise unknown model string gets no bar
- Re-measure `TREE_ROW_BYTES` (`tests/view/test_bounds.py`) — two new classes per row and the popover trigger both grow the pinned row

## Cluster 5 — the popover

- **Fetch-on-hover, not inlined.** Up to 3,217 tree rows multiply any per-row payload past the page budget, so the popover is an htmx fragment fetched on `mouseenter` (delayed, once) and on `focus` — keyboard reaches what hover reaches
- **Route**: one new fragment route per the URL descriptor rule (no two adjacent ids without a static word between). Reusing the node URL shape with a trailing static segment fits. The route joins `test_bounds.py`'s sweep like every route
- **Content**: the context bar large with exact numbers — cached, new input, output, window, and for turns and runs the added-since-previous figure including a negative delta across a compaction — then the cost legend: input, cache read, cache write, output, each with its dollar figure, summing to the badge's total. On tool rows: the tool's `result_chars` and its siblings' titles
- The popover shows numbers the store holds; where the window is unknown it says so rather than scaling to a guess

## Out of scope (considered, deferred)

- **Auto-compact tick** on the bar, derived from observed `compactions.pre_tokens` per model — fast follow, needs its own evidence pass
- **Session list badge/context column** — this round's notes are about the node page; the list's cost cell has no session-relative share to reuse
- **Per-tool token attribution** (splitting the next call's delta by result-char share) — prints numbers the store can't back
- **Reasoning-token estimate** from thinking chars

## Open questions

- Whether the popover needs a click-to-pin affordance for copying numbers — decide in the gallery when it's rendered, not before
