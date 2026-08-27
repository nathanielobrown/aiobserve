# Phase 1 — Viewer polish batch

Grilling notes for the NavTree / layout / popover / formatter changes. A code survey settled the items below without questions; they go straight into the plan as decisions:

- **Layout**: masthead fixed, `#browser` fills the rest of the viewport, NavTree and reading pane each get their own scroller. Narrow (≤900px) layout keeps its block flow. The "scroller stays outside the swapped element" rule still holds — only the reading pane gains a scroller; the NavTree already has one
- **Breadcrumbs**: crumbs get their own cut at 40 chars (`CRUMB_CHARS`). Today crumbs reuse `nav_tree_title` (110); walk controls, error stepper, and browser tab keep 110
- **Sticky ancestors**: CSS `position: sticky` on ancestor rows, stacking below the already-sticky preset control. No depth cap initially — flag in pending if you want VS Code's cap behavior
- **Scroll selected into view**: a small static JS file (CSP forbids inline), `scrollIntoView({block: "center"})` on load
- **Run rows**: lead becomes `[implementer]` instead of `implementer —`
- **Emoji titles propagate everywhere** (crumbs, pane heading, logs) per the one-title doctrine
- **Nesting**: ◎ run rows move under their ⇄ Agent tool call, always visible when the tool row is (auto-open). The hoisting machinery (`_hoisted`, `_spawned` wiring, the `spawned = []` suppression) comes out; the `CHILDREN` kind × preset table and its test mirror change to match

## 1. With no page scroll, does the footer citation scroll with the reading pane or stay fixed on screen?

Every page ends with `footer#citation` — the "cites one query" link the project treats as load-bearing. Once the masthead is fixed and both panes scroll internally, the footer needs a home: either it becomes the last element inside the reading pane's scroller (visible only when you scroll to the end), or it becomes a thin fixed strip below both panes (always visible, permanently eating a line of vertical space).

Getting this wrong is cheap to reverse — it's a template and CSS move either way.

### Recommendation: inside the reading pane's scroller

The citation describes the reading pane's content, so it belongs at the end of that content. We give up its constant visibility, but anyone chasing the query is already reading the page, and a permanent strip shrinks every session's reading area for a link used occasionally.

### User Response:

Agree that inside the reading pane's scroller is best

## 2. Popover layout: approve this corrected mock — your draft's dollar amounts don't reconcile

In your mock, $0.0000 (the input cost) sits beside cache read, $0.0596 (the cache-read cost) sits beside new input, the $0.0089 cache-write cost disappears, and the $0.0743 total doesn't match the $0.08 the current popover reports. The rows do reconcile if the new-input row carries input cost + cache-write cost (cache write is priced on the new tokens being written to cache):

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

The `over N api calls` line appears only when N > 1 (turn, run, and session popovers aggregate several calls). The alternative is a separate cache-write line, which keeps every cost visible individually but breaks the token↔dollar pairing that makes the table read as one story.

Stakes: low — it's one template and one query. But the popover is the viewer's per-node cost story, so the pairing should be one you trust.

### Recommendation: the corrected mock above

New input's dollar merges input + cache write, and the columns sum. We give up seeing cache write as its own line; if you ever need it split out, the query page has the raw numbers.

### User Response:

I agree that the input's dollar amoutn should be the sum of input and cache write. Sorry for getting the numbers wrong. I like you addition of the `over 3 api calls`

## 3. May the viewer require modern-Chrome CSS (anchor positioning) for the popover, with a degraded fallback elsewhere?

To align the popover's top with the hovered row (left edge already sits at the NavTree's right), something has to know where that row is. Two routes:

CSS anchor positioning does it declaratively — the popover tracks its row even while the NavTree scrolls, zero JS. It's been in Chrome since 2024 but is newer elsewhere; with an `@supports` fallback, other browsers get today's fixed bottom-left spot. The alternative is ~10 lines in the JS file we're adding anyway for scroll-into-view: works in every browser, but re-positions only on mouseenter, so scrolling the tree under a stationary pointer can leave the popover misaligned until the next hover.

The real decision is the precedent: is "recent Chrome, graceful degradation elsewhere" an acceptable support floor for this local tool? Reversible — either implementation is small.

### Recommendation: CSS anchor positioning with the @supports fallback

You browse in Chrome, the tool is local, and the declarative version has no scroll-tracking gap. We give up pixel-correct alignment on older browsers, which fall back to the current placement rather than breaking.

### User Response:

I actually browse in Firefox. I'd like this to just work in all relatively recent browsers. I'm OK with a little bit of Javascript.

## 4. Tool-call formatters: edit this table

You asked for a review of tool-call presentations. Today there is deliberately *no* per-tool casing — `tool_title` is input-shape-driven ("so a tool nobody here has heard of still names itself"), a rule stated in docs/viewer.md. This change reverses that: name-driven formatters with the shape-driven title as fallback. The plan will rewrite that doc rule and move lead derivation from the SQL macro into Python, where the SendMessage recipient lookup (against the in-memory run table) can happen.

Proposed formatters — row shows `⇄ <below>`; anything unlisted keeps `Name - <shape-driven title>`:


| Tool        | Row reads                                             | Notes                                                                                                                             |
| ----------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Read        | `📖 src/hyphae/view/nodes.py`                         | file_path, relativized (as today)                                                                                                 |
| Write       | `✏️ docs/viewer.md`                                   | file_path, relativized                                                                                                            |
| Edit        | `📝 src/hyphae/view/nodes.py`                         | file_path, relativized                                                                                                            |
| Bash        | `⚡ cd /Users/N…`                                      | first line of `command`; drops today's preference for `description`                                                               |
| Agent       | `👉 [implementer] Survey viewer implementation facts` | subagent_type + `description` field (the 3–5-word one), since the ◎ row nested right under it already shows the prompt/brief head |
| Skill       | `📕 writing`                                          | skill name, then args if present                                                                                                  |
| SendMessage | `📬 to implementer: Request the doc-sync report`      | recipient id resolved to agent_type via the session's runs; falls back to the id's head when unresolved                           |
| Grep        | `🔎 pattern`                                          |                                                                                                                                   |
| Glob        | `🗂 pattern`                                          |                                                                                                                                   |
| WebFetch    | `🌐 url`                                              |                                                                                                                                   |
| WebSearch   | `🔍 query`                                            |                                                                                                                                   |
| TodoWrite   | `☑️ 3 todos`                                          | count of items                                                                                                                    |


Stakes: purely cosmetic per row, but the fallback contract (unknown tools still name themselves) is what keeps the viewer robust to Claude Code adding tools — the plan keeps it.

### Recommendation: as tabled

Strike or reword rows here; anything you don't touch goes in the plan verbatim.

### User Response:

Make sure to update the rule in [viewer.md](http://viewer.md) to note we are OK creating formatters. I updated table to note that all file paths should be relativized. Otherwise, looks good

## 5. Which rows get 💭 "Agent said something" — api-call rows whose response was text only?

Your emoji list includes "Agent said something — 💭", but that isn't a tool. My best reading: an api-call (⇄) row where the model wrote text and called no tools — today those rows lead with the text head. Giving them 💭 makes "the model spoke" scannable in the full preset. Alternative readings: assistant text inside a mixed text+tools call (harder — one row can't carry both), or something else you had in mind.

Note the boundary: in the no-api-calls preset those rows don't exist, so text-only responses stay invisible there — unchanged by this item.

### Recommendation: text-only api-call rows get 💭

Cheap, matches the row that already represents the utterance. Tell me if you meant something different.

### User Response:

If what we display is what the model said (output text) then we should use 💭. Currently it seems if the model says something and tool calls, the NavTree node displays what was said, so I think we sh should use 💭.

## 6. Subagent (◎) context bars: keep, remove, or restyle?

You flagged that run rows "should maybe not have context bars or be different in some way". Today a run row's bar is real data — the run's own window fill at its end — but it renders identically to a turn's bar, inviting a misread as main-thread fill. Since a subagent starts on an empty window, its bar is all bright tip, which already looks somewhat distinct.


| Option                           | Trade-off                                                                        |
| -------------------------------- | -------------------------------------------------------------------------------- |
| Keep as-is                       | Zero work; the misread stays possible                                            |
| Remove                           | Cleanest tree; loses an at-a-glance signal (the popover keeps the numbers)       |
| Restyle — same bar, distinct hue | Keeps the signal, marks "this is its own window"; costs a color token and a rule |


Reversible any time; pure CSS plus a class.

### Recommendation: restyle with a distinct hue

A subagent that filled its own window is worth seeing without a hover, and the hue kills the misread. We give up a little visual quiet in agent-heavy sessions.

### User Response:

Let's do a different hue, and if the agent had an auto compact, let's use a read hue to indicate that the context window got maxed out. Let's also make the bar go all the way full rather than showing context usage at the end to indicate that we maxed out the window. I think we should generally avoid having subagents auto compact by not giving them too much work to do and by having an effecient setup that doesn't waste context, so the red bars for subagents will highlight that something is not quite right.

## 7. Turn context bars: what exactly is "the increase from base prompt to the end of their run"?

Today a turn's bar is: dim = fill at turn start (everything pre-existing), bright tip = what this turn added over the previous turn. Your phrasing suggests anchoring on the *base prompt* — the fixed context (system prompt, CLAUDE.md, etc.) every turn starts from — but it can be read a few ways:


| Option               | Dim region         | Bright region                                           | What it emphasizes                     |
| -------------------- | ------------------ | ------------------------------------------------------- | -------------------------------------- |
| a. Today's semantics | fill at turn start | this turn's delta                                       | per-turn cost of the turn              |
| b. Base-anchored     | base prompt only   | everything since session start, through this turn's end | conversation growth vs. the fixed base |
| c. Three bands       | base prompt        | mid-tone: prior conversation; bright: this turn's delta | both at once                           |


The bar already stacks three CSS gradient layers, so (c) is a modest extension, not a new mechanism. "Base prompt" would be measured as the context the session's first main-thread api call started with.

Stakes: this is the NavTree's main ambient signal, and the compaction indicator (pending below) hangs off whichever semantics you pick.

### Recommendation: (c) three bands

It answers both questions a reader brings to the bar — how big has this conversation gotten, and what did this turn cost — without a hover. We give up the simplest possible bar, and dark-mode needs a third distinguishable tone.

### User Response:

Agree that three bands is the way to go. I hope there's an easy way to tell what the base context amount is, let me know if you forsee issues with this.

## 8. Dual cost badge `$own/$total`: which node kinds get it?

Today a turn's badge already *excludes* its subagents (own thread only), a run's likewise, and tool calls carry no cost at all. The new badge shows `$2.10/$20.30` — own thread / subtree total — each half with its own heatmap wash. Proposed scope:


| Row                                      | Badge                                                                                                            |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Turn with run cost in its subtree        | `$own/$total`                                                                                                    |
| Turn without                             | `$own` (as today)                                                                                                |
| Run that spawned its own runs            | `$own/$total`                                                                                                    |
| Run without                              | `$own` (as today)                                                                                                |
| Session                                  | `$main/$whole` — same rule applied at the top                                                                    |
| ⇄ Agent tool call (now the run's parent) | stays costless — the ◎ row directly under it carries the dollars, and a third copy of the same number adds noise |


The session row is the judgment call: its badge today is the whole-session total, and `$main/$whole` changes a number people may have calibrated on. The tool-call row is the other: you could argue the new parent should announce its subtree's cost.

Reversible; the totals need one new rollup per node (subtree cost), which the plan adds once for badge, popover, and future use.

### Recommendation: as tabled, including the session

Consistency: every level answers "what did *this thread* spend, and what did it *cause*". We give up the session badge's current single-number simplicity.

### User Response:

Don't worry about "what people have calibrated on", this project has not yet been used by someone other than me. I think I agree with the table except that the Agent tool call DOES have a small cost in addition to the cost of the agent. So I think we should show that small cost. Example: `$0.13 / $7.65` for the Agent call and then the agent node under that has `$7.52` as the cost. So `$7.65` is a sum of the agent tool call and the subagent cost. 

## Pending questions (depends on answers above)

- How to draw compaction on the context bar (a notch? a bar on the ⊟ row showing the before→after drop? unclamp the negative delta?) — depends on Q7
- Heatmap scale for the popover's per-row dollar amounts (reuse the badge's log-share-of-session meter, or a scale local to the popover?) — depends on Q2
- Whether the sticky-ancestor stack needs a depth cap once you feel it on deep sessions — veto the "no cap" decision above if you already know you want one

---

# Phase 2 — Compaction, base prompt, and the Agent tool call's own cost

New decisions from phase 1 and your clarification, recorded for the plan:

- **Popover positioning**: plain JS in the shared static file (Firefox is the daily browser). It re-positions on hover and on NavTree scroll, so the stationary-pointer misalignment doesn't occur
- **viewer.md**: the "input-shape-driven, never name-driven" rule is rewritten to allow per-tool formatters with the shape-driven fallback; all file paths relativized
- **💭** goes on any api-call row whose displayed words are model speech — including calls that also ran tools, since the row already prints the text in that case
- **Run bars**: distinct hue; a run that auto-compacted renders full-width red — the "this subagent maxed its window" warning light
- **Selection-dependent Agent rows**: when the invoking api call is not on the open path, the ◎ run shows directly under it with the ⇄ Agent tool row hidden; selecting the api call or anything below it inserts the tool row between them
- **Popover dollar washes**: reuse the badge meter (log share of session whole) so a wash means the same thing everywhere

## 9. How does the main thread's NavTree show a compaction's context drop?

Turns get three-band bars (base / prior conversation / this turn's delta), and today the negative delta a compaction causes is clamped to zero — the bar simply shrinks next turn with no explanation. The ⊟ compaction row already sits interleaved at the right spot in the tree, but carries no bar at all. Subagent runs got their answer in phase 1 (full-width red); the main thread still needs one.


| Option                      | How it looks                                                                                                        | Trade-off                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| a. Bar on the ⊟ row         | dim band up to the post-compaction fill, red band from there to the pre-compaction fill — the red is what was freed | The drop lives where the event lives; costs making ⊟ a bar-carrying kind       |
| b. Red tip on the next turn | unclamp the negative delta, draw it red                                                                             | No new row mechanics, but blames the wrong row — the turn after the compaction |
| c. Both                     |                                                                                                                     | Redundant; two reds for one event                                              |


Reversible; CSS plus one query change either way.

### Recommendation: (a) bar on the ⊟ row

The compaction row exists precisely to mark this event, and a red "freed" band reads instantly next to its neighbors' bars. We give up bar-free simplicity on a kind that never had numbers — its popover stays absent for now.

### User Response:

Let's put a bar on the compaction row but let's not make the "freed" section red, let's make it green because free context is good, not bad and we are using red for "bad".

## 10. Measure "base prompt" as the first main-thread api call's pre-existing context?

You asked whether the base amount is easy to get. It is: the store records each api call's context fill, so *base = the session's first main-thread api call's fill minus what that call added* — the system prompt, tools, and memory files that were on the window before any conversation. One value per session, computed once in the nav-tree query.

Two caveats I foresee, neither disqualifying:

- **Resumed and forked sessions** open with replayed history, so their "base" includes the inherited conversation. Arguably correct — it *is* pre-existing context for every turn that follows — but it will dwarf the true base prompt
- **After a compaction** the base band keeps meaning "the original base," even though the window now holds a summary instead. The bands stay a growth story, not a window-contents map

The alternative is something cleverer (e.g., re-measuring base after each compaction), which buys precision nobody asked for.

### Recommendation: yes — first call's pre-existing context, caveats accepted

One number, one query, honest bands. We give up precision on resumed sessions, where the base band will be fat and mean "inherited context."

### User Response:

Agree with recommendation.

## 11. The Agent tool call's own cost: where does the `$0.13` come from?

You want the ⇄ Agent row to read `$0.13 / $7.65` with the ◎ run's `$7.52` below. But the store prices api calls only — a tool call has no cost of its own, which is why those rows are costless today. The $0.13 has to be attributed from somewhere:


| Option                                               | Own cost =                                        | Trade-off                                                                                                                                                                                                                                    |
| ---------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| a. The invoking api call's cost                      | the price of the model turn that decided to spawn | Real number, right intuition ("what did deciding cost"); but one api call can request several tools in parallel, so each parallel Agent row would claim the same full amount, and in the full preset the api-call row above already shows it |
| b. Same, but only when the call requested one tool   | blank/single-number when parallel                 | No double-counting, but the badge's meaning becomes conditional                                                                                                                                                                              |
| c. None — single `$7.52`-style total on the tool row |                                                   | Honest to the store, but drops the split you asked for                                                                                                                                                                                       |


Parallel Agent spawns are common in your sessions, so (a)'s double-counting is not a corner case. Whichever way, the popover can spell out the attribution.

### Recommendation: (a), with the popover naming the attribution

"$0.13 — the api call that spawned this run" is a true sentence even when three parallel rows each say it; the badge is a reading aid, not an accounting ledger, and the session totals people sum come from turn and session rows. We give up strict no-double-counting inside one api call's tool list.

### User Response:

Agree with option A, the API call cost

## 12. When the hidden tool row appears on selection, may the ◎ run's indent shift by one level?

Your clarification hides the ⇄ Agent tool row until the api call's subtree is selected. The run's indent can behave two ways. Natural nesting: the run indents one level under whatever row is visibly its parent — under the api call when closed, one level deeper when the tool row appears. Stable depth: the run always sits at tool-row depth, leaving a visible half-step of extra indent under a closed api call.

Natural nesting is how every tree the eye knows behaves (VS Code included), but rows shift horizontally as you click around. Stable depth keeps rows put and makes the "subagents stick out" indent even more pronounced, at the cost of an orphan indent level that looks like a rendering bug to fresh eyes.

Purely visual; trivially reversible.

### Recommendation: natural nesting

Trees that indent by visible parent never surprise anyone, and the selected state — where you're actually studying the spawn — shows the full chain. We give up horizontal stability for the one row kind that moves.

### User Response:

Agree with recommendation of natural nesting. It's OK if the agent row shifts write when clicked

## 13. Do always-visible runs generalize to every preset — runs showing under closed turns in no-api-calls too?

Your rule so far is stated for the full preset: a ◎ run is visible whenever its invoking api call is. The general form is "a run is always visible under its nearest visible ancestor" — which in the no-api-calls preset means a closed turn would show its runs dangling beneath it, and the agents preset is already this by definition. Without the general rule, subagents are prominent in one preset and hidden in another, which undercuts the "make subagents obvious" goal.

Cost: more rows on no-api-calls pages of agent-heavy sessions (bounded by the existing per-level cap), and the kind × preset table gains the rule in two cells instead of one.

### Recommendation: yes — generalize

One rule, every preset, subagents always findable. We give up some quiet in the no-api-calls preset, which is the preset people use to skim.

### User Response:

Let's just show the subagent without the parent API call. No missing indent, it is just indented one less stage

## Pending questions (depends on answers above)

- None foreseen — phase 3 will exist only if these answers open something new

