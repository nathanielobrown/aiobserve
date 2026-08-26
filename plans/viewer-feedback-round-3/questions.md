# Phase 1 — Viewer round 3: tool-derived labels, context in the navbar, cost as a badge

What I settled by reading the code first, so no question asks it:

- **The token data already exists per API call.** The store's `api_calls` table carries `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` and the 5m/1h split. Context at the end of a call is `cache_read + cache_creation + input` (the prompt) plus `output`; no extract work is needed.
- **Usage has no reasoning split.** `output_tokens` is one number that includes thinking (spine fixture, CC 2.1.221). A reasoning segment could only be estimated from thinking-text length, so the bar's segments are: cached (cache_read), new input (input + cache_creation), output — as in your sketch minus the reasoning line. If you want the estimate anyway, say so under Q7.
- **Tool titles are already derived in SQL.** The macro every query calls produces exactly `Bash — Remove temp mutation clones` style titles, so the API-call label reuses it rather than inventing a second derivation.
- **No questions for the small items.** `mise run gallery --port` is a flag on `tests/gallery/serve.py`, and the left-alignment fix is cosmetic; both go straight into the plan.



## 1. When an API call said nothing, does the tool-derived label become the node's one title everywhere, or only the tree row's fallback?

`docs/viewer.md` holds a one-title rule: each node has one title, printed by the pane heading, tree row, crumb, walk controls, errors list and browser tab. An API call's title today is the head of what it said, else the model name. Your note asks for `Bash — Remove temp mutation clones` in the navbar — the question is whether that's a navbar-only fallback or the node's title.

One wrinkle: children logs deliberately name an API call's row by the model, with its tools listed under the count — a documented exception to the one-title rule. If the title changes, that log row would show the first tool's title in the name column and repeat it below under the tools count.

Two options:

- **Change the one derivation** (text head → tool titles → model), used everywhere. Consistent: the words a reader clicks are the words that head the pane. The children-log exception gets removed or kept as a conscious second exception.
- **Tree-row-only fallback.** Smallest change, but a reader clicks `Bash — Remove temp mutation clones` and lands on a pane headed `claude-sonnet-5`.

Stakes: low blast radius either way; a title derivation lives in one place and is easy to revise.

### Recommendation: change the one derivation, everywhere

The one-title rule exists so clicked words match the pane; a navbar-only label breaks that on the exact rows this feature targets. Keep the children log naming its API-call rows by model (its column-shape reason still holds) as the one documented exception. What we give up: the tree and the children log name the same node differently — but they already do today.

### User Response:

Agree with recommendation: change everywhere



## 2. How does a title name an API call that made several tool calls?

Parallel tool calls are common (`tests/fixtures/parallel_tools/` exists because of them). The title has ~110 characters on a tree row.


| Option                          | Example                                           | Trade-off                                                          |
| ------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------ |
| First tool's full title + count | `Bash — Remove temp mutation clones +2`           | Most informative words survive the cut; hides what the others were |
| Join the first two titles       | `Bash — Remove temp clones · Read — mise.toml +1` | More coverage, less room per title                                 |
| Names only when plural          | `Bash ×2, Read`                                   | Uniform, but drops the descriptions that make labels readable      |


Stakes: cosmetic and reversible; the derivation is one macro.

### Recommendation: first tool's full title + count

The first call is usually the headline act, and a whole readable title beats two truncated ones. We give up seeing the sibling tools without opening the row — the row's popover and pane still list them.

### User Response:

Agree with recommendation, but count should be showing per tool call: `<first tool call> +2(Bash) +1(Read)

## 3. What does the inline bar on a navbar row show once context takes it over from cost?

Today every row with spend carries a log-scale bar along its edge showing its share of session cost. Your notes lean toward moving cost off the bar (Q4), which frees the bar for context. Two numbers matter per row: how full the window is at the end of the node, and how much the node added.

- **Bar-in-bar, linear scale**: full track = the limit (Q6), dim fill = context at end of node, brighter tip = what this node added. One glance gives both numbers; linear because fullness against a limit is the story (log was needed for cost's three orders of magnitude, not here).
- **Fullness only**: simpler, but adjacent rows look identical and the "what did this add" question needs the popover.
- **Added only**: shows the delta but loses the approach-to-compaction read, which is the headline.

Stakes: this is the visual readers scan most; wrong choice means a redo, but it's CSS and one number per row — cheap to redo.

### Recommendation: bar-in-bar, linear

It answers both of your stated questions in one glyph, and it's how Claude Code's own context meter reads. The cost: small additions render near-invisible at tree-row width, so the exact numbers live in the popover (Q7).

### User Response:

Agree with recommendation: bar-in-bar, linear

## 4. Cost badge: which color ramp behind the dollar value?

Your notes pick the badge over stacked bars — agreed, and I'll treat that as settled. The open choice is the ramp. Your suggestion was brighter green = more expensive; I'd push back: green reads as "good/cheap" almost everywhere, so an expensive turn glowing green sends the opposite signal.


| Option                                       | Read                | Trade-off                                              |
| -------------------------------------------- | ------------------- | ------------------------------------------------------ |
| Warm single hue (amber deepening with spend) | Hot = expensive     | Intuitive; at full depth may brush against "error red" |
| Heat ramp (yellow → orange → red)            | Familiar heat-map   | Two hues to keep distinct from the error styling       |
| Neutral (gray deepening)                     | No semantic baggage | Weakest at catching the eye, which is the badge's job  |
| Green as sketched                            | Bright = expensive  | Reads inverted to most eyes                            |


Either way the ramp reuses the existing log scale over three orders of magnitude — that choice was made for cost once and still holds.

Stakes: pure CSS, trivially reversible.

### Recommendation: warm single hue, amber

One hue keeps it apart from error styling while still saying "hot spot." We give up the instant familiarity of a full heat ramp.

### User Response:

Agree with recommendation

## 5. Do tool-call rows get context info, or only API calls and above?

Your note lists tool call as a phase, but a tool call carries no usage: the context its result adds lands in the *next* API call's prompt, mixed with everything else that call carried — and when one API call made three tool calls, all three results land there together. Per-tool attribution would be an estimate (split the next call's delta by result-character share).

- **Skip tool rows in v1.** Their addition is visible one row down, on the following API call. Honest numbers only.
- **Estimate per tool** by result-char share. Answers "which tool blew the context" directly, but prints numbers the store can't back — against the house rule that a claim carries its query.
- **Show result characters** on tool rows as a proxy: a real stored number, roughly proportional to tokens, clearly labeled as chars.

Stakes: scoping only; adding tool rows later doesn't disturb the rest.

### Recommendation: skip tool rows in v1, show result chars in their popover

The chars number is real, already stored, and usually answers "which tool was fat" without inventing token counts. We give up a direct token answer on the row the reader is hovering.

### User Response:

Agree with recommendation

## 6. Where does the auto-compact limit — the bar's full scale — come from?

Nothing in the transcript states the limit. Claude Code's auto-compact threshold varies by version and isn't documented; the model's context window (200k, 1M) is a published constant.


| Option                                  | How                                                                                                              | Trade-off                                                                                      |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Per-model window constant               | Hardcode 200k/1M per model family; bar scale = window                                                            | Honest and simple; the bar won't say *when* compaction fires                                   |
| Window constant + observed compact tick | Add a tick where the corpus's auto-compactions for that model cluster (`compactions.pre_tokens`, trigger = auto) | Evidence-backed "compaction fires about here"; models with no observed compactions get no tick |
| Hardcoded auto-compact threshold        | Guess the fraction CC uses                                                                                       | A wrong line confidently misleads — worst option                                               |


Stakes: this scales every bar; a wrong constant distorts every fullness read. The constant table is a new maintained fact (new model → new row), so where it lives matters more than which option we pick first.

### Recommendation: window constant now, observed-compaction tick as a fast follow

Start with the number we can cite. The tick is the honest version of "auto compact limit" — derived from this corpus's own compactions rather than a guess — and it can land separately. We give up the compaction line on day one.

### User Response:

Agree with recommendation

## 7. Does the popover carry the cost breakdown too, or context numbers only?

The popover (hover on a navbar row) definitely shows the context bar with exact numbers: cached, new input, output, limit. Your Artificial Analysis screenshot shows the richer version — a stacked bar with a legend pricing each category (Input / Cache Hit / Cache Write / Answer). Our price table already splits cost by those same categories per call, so both fit.

- **Context + cost legend**: one popover answers both "how full" and "where the dollars went." Matches the screenshot. More content per row — and with up to 3,217 tree rows, per-row popover payload multiplies against the page's byte budget, which likely forces fetch-on-hover rather than inlined content.
- **Context only**: lighter, and cost keeps its badge + the pane's existing fields. The dollar *breakdown* stays one click away on the pane.

Stakes: mostly payload engineering; content can grow later without redesign.

### Recommendation: context + cost legend

The screenshot is the right instinct: hover is where exact numbers belong, and the data is already in every query's reach. The cost is implementation weight — the popover almost certainly becomes an htmx fetch, not inline markup, to stay inside the tree's byte budget.

### User Response:

Agree with recommendation

## Pending questions (depends on answers above)

- Popover mechanics: CSS-hover over inlined data vs htmx fetch-on-hover; keyboard and touch access — depends on Q7 (payload size decides)
- Where the per-model window-constant table lives and how a new model gets a row — depends on Q6
- Whether turn/run/session rows show "added" as delta-since-previous-sibling or sum-of-own-calls (differs once compaction shrinks context mid-phase) — depends on Q3
- Does the session list page pick up the cost badge for consistency — depends on Q4

