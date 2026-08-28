# Phase 1 — Direction on the genuine forks

Settled by code exploration, no question needed:

- **Emoji regression cause**: tool names are derived in two systems — the Python formatter registry (`formatters.py`, emoji + argument extraction) and the SQL macro `tool_title` (`analyze/macros.py:107`). API-call titles, the turn's calls-log column, and the tool popover's sibling list all use the SQL macro and never consult the formatters. Confirmed not DRY; Q1 asks which system should own naming.
- **JSON one-liner cause**: the pane's Arguments preview always takes the bare `<pre>` path; the pretty-print + highlight path only runs in the fetch fragment behind the "rest" link. Result gets syntax only for `Read` (file suffix). Fix: route both through the highlighter, JSON-parse-then-fallback-to-plain. Clear best answer, will just do it.
- **ToolSearch / PushNotification labels**: one registry entry each; `query` is already in `tool_fields`, `message` needs one new field there. I'll use 🧰 for ToolSearch, 🔔 for PushNotification (override in any answer if you prefer another glyph).
- **Facts cleanup**: dropping the session "Title" and "Project" fact rows is unambiguous — no question.



## 1. Should Python become the single owner of tool-call naming, with SQL reduced to shipping fields?

The emoji + formatted names live in a Python registry (`formatters.py`), fed by the SQL `tool_fields` macro. But four SQL surfaces name tool calls themselves via the `tool_title` macro and thus have no emoji: the api-call title (NavTree + header), the calls-log `tool_titles` column, and the popover sibling list. Any per-tool improvement now has to be made twice or it silently diverges — which is exactly the regression you noticed.

Options:


| Option                          | How                                                                                                                                                                   | Trade-off                                                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Python owns naming              | Add `tool_fields` to the call queries (and restructure `tool_titles` aggregation into per-row fields), delete or demote `tool_title` to a pure fallback inside Python | Queries ship more columns; the `string_agg` calls-log column becomes Python-side composition — a moderately invasive query change |
| SQL owns naming                 | Port the emoji registry into the `tool_title` macro                                                                                                                   | Emoji + argument logic in SQL string-building is miserable to write and test; the Python registry already exists                  |
| Patch just the two call queries | Add `tool_fields` to `view_nav_tree_calls` / `view_call_header` only, leave logs and popover siblings emoji-free                                                      | Smallest diff, but leaves three naming systems standing — the divergence recurs                                                   |


Stakes: this is the structural fix your "are things not DRY?" bullet points at. Half-fixing it means the next per-tool label change regresses somewhere again.

### Recommendation: Python owns naming

Consolidate: SQL macros ship fields, `formatters.py` (plus the shape-driven fallback ported from `tool_title`) is the one place a tool call gets named, and every surface — api-call titles, calls log, popover siblings — goes through it. Cost: the largest diff of the three, and a query-shape change that touches the row-byte bounds tests.

### User Response:

I agree. Python should own the naming. This is format display level concern, not data retrieval concern. We might want to add a short note about this separation of concerns convention in AI guidance file(s).

## 2. Where should markdown in labels actually render, given titles appear in five escaped surfaces plus the browser tab?

Titles print autoescaped in the NavTree row, crumbs, log rows, walk controls, and the error stepper, and as plain text in the `<title>` element. NavTree rows are themselves links, so a rendered `[PR #18](…)` link inside a row is a link-inside-a-link. The viewer already has markdown-it-py wired for prose, so the renderer exists; the question is scope.


| Option                           | Behavior                                                                                                                                                                              | Trade-off                                                                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Inline-subset everywhere visible | Render bold/italic/code in all title surfaces; links render as styled text (not anchors) in NavTree/crumbs, real anchors only in the reading-pane `<h1>`; strip to plain in `<title>` | Best result; needs a small "inline markdown, no block elements" render mode and a strip mode, and touches the title tests |
| Reading pane only                | `<h1>` and detail headers render markdown; NavTree keeps raw `**` text                                                                                                                | Cheap, but the NavTree is where you read these labels most — raw asterisks stay in your face                              |
| Strip everywhere                 | Remove markdown syntax for display, no styling                                                                                                                                        | Cleanest text, loses the boldness/link you asked for                                                                      |


Stakes: moderate — reversible, but the escape/strip story touches every surface, so better decided once.

### Recommendation: Inline-subset everywhere visible

Bold/italic/code styled in every title surface, links as plain styled text except in the reading-pane title, plain-text strip for `<title>` and any attribute. Cost: the render/strip pair is a new small module and the `data-field="title"` test assertions all change.

### User Response:

Agree with recommendation. 

## 3. Does the popover's subagent-spend breakout apply to every node whose subtree spent more than the node itself — including changing what the session popover's base numbers mean?

Your mockup shows own-thread numbers on top, then `subagent spend 99.6% $23.03`, then `total spend`. Today the session popover's base numbers already *include* subagents (its calls filter is "everything"), while turn/run popovers are own-only — the inconsistency you flagged. Making the mockup universal means the session's cache/input/output lines change meaning to own-thread-only, with subagents only in the breakout lines. Nodes with no subagent spend would show no breakout section at all.

Stakes: low — presentation only, easy to revise. Asking mainly to confirm the session-popover semantic change is intended.

### Recommendation: Yes, universal

One rule everywhere: base lines are always own-thread; any node with subtree spend > own gets the two breakout lines (subagent spend, total spend). Session included. Cost: the session popover's token lines shrink to the main thread's own numbers, which no longer match the session-list cost column at a glance — the total line is what matches.

### User Response:

Agree with recommendation we should have a universal convention of always breaking out the subagents separately.

## 4. Compaction badge: agent-run rows only, or every NavTree row whose subtree compacted (turns, session)?

Per-run compaction counts already exist in the run query (currently used only to tint the context bar via the `maxed` class). Per-turn counts don't exist yet; the session count does. NavTree row bytes are pinned by a bounds test, so any badge added is re-measured.

Options: (a) run rows only, as your example shows — the count is already in the row's data; (b) also session and turn rows, so a compaction anywhere is visible from the root — needs a new per-turn count and more crowded rows.

Stakes: low, easily extended later.

### Recommendation: Run rows only

Matches your example, zero new queries, and the main thread's compactions are already visible as interleaved ⊟ nodes — a turn-level badge would mostly duplicate what the tree shows. Cost: a subagent-heavy session's root row won't hint that runs below compacted until you expand.

### User Response:

Let's do Run rows only as you recommend

## 5. Compaction tooltip: a full popover like other nodes, or a plain `title` attribute?

Compaction nodes are currently excluded from the popover mechanism entirely. The compactions query already carries trigger and the token counts, so a full popover (tokens before → after, freed, trigger) is straightforward; a `title=` attribute is cheaper but inconsistent with every other row.

Stakes: trivial.

### Recommendation: Full popover

Same hover/tab mechanism as every other node — consistency is the point of the popover system. Content: context before, context after, freed, trigger. Cost: one small SQL fragment + template vs a one-line attribute.

### User Response:

I agree with recommendation full popover. 

## 6. Do tool-call Arguments/Result get the quote border too, or only prose the agent wrote or was told (Prompt, Brief, Said, Thought, run Result)?

You asked for a border that reads as "quoted content" around Prompt/Result on runs and Said/Thought on api calls. Tool Arguments/Result are also content that crossed the wire, but they're machine payloads — bordering them too makes the border mean "any detail block" rather than "someone said this."

Stakes: pure CSS scope, trivially reversible.

### Recommendation: Prose only

Border the conversational surfaces (prompt, brief, said, thought, run result, user-visible messages); leave tool arguments/results in their code-block styling, which after Q-settled JSON formatting will already look visually distinct. Cost: two adjacent details on a tool page look less uniform.

### User Response:

Agree prose only for quote border. 

## 7. When a run is enriched, its header shows the enrichment description, not the brief — remove "Task brief" anyway, or only when it duplicates the header?

The run title is `enrichment description, else brief`. So for enriched runs the header and the brief differ, and dropping "Task brief" loses the actual instruction text from the page. For unenriched runs they're identical and the detail is pure duplication.

Options: (a) always remove — the brief is one click away via the spawning ⚒ tool call's Arguments; (b) remove only when unenriched (i.e., when it would duplicate the header); (c) always keep.

Stakes: low.

### Recommendation: Remove only when it duplicates the header

Conditional emission is one `if`: show "Task brief" only when the title came from enrichment. You never see the same text twice, and enriched runs keep the instruction visible. Cost: the page's field set varies by enrichment state, which is mildly less predictable.

### User Response:

Now that you point this out, let's actually not remove it. Let's leave the task brief. 

## Pending questions (depends on answers above)

- Whether the calls-log `tool_titles` column and popover sibling list adopt emoji labels in this iteration or a follow-up — depends on Q1
  - They should adopt emoji 
- Badge styling (red pill vs the existing badge part) and exact text (`2 compactions` vs `⊟ 2`) — depends on Q4; I'll default to your `2 compactions` red-badge example unless you say otherwise
  - Your default here is good. 
- Whether links in enrichment-derived titles should be clickable in the reading-pane `<h1>` only — depends on Q2
  - Where we are not already inside a link, let's make links and titles clickable. It'd be nice to have a link to PR links, for example
- ToolSearch emoji: defaulting to 🧰 unless you override
  - That's a good choice. 

