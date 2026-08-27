# Phase 1 — the shape of the session page

Six forks, all independent of each other. Everything downstream (tooltips, enrichment markers, syntax highlighting, bounds arithmetic, PR slicing) waits on these.

What I checked before asking, so you don't have to: the store already holds every input and output — `raw_records.raw` archives every transcript line, `tool_calls.input`/`.result` hold tool arguments and results, `offload_files.content` holds the ones Claude Code wrote to disk, and `api_calls.text`/`.thinking` hold model output. Nothing is dropped at extract. The viewer already opens most of them on demand. htmx 2.0.6 is vendored, so "the viewer ships no script of its own" means no *first-party* script. Each citation string names a real file: `src/hyphae/analyze/queries/view_session_header.sql`.

## 1. Replace the paged timeline and the run page with one uniform node view?

Today a session page renders a **paged timeline**: turn after turn down the main pane, each expandable into its api calls, with `after`/`turns`/`chips` controlling the window. An agent run gets its own page at `/session/{sid}/run/{rid}` that repeats the same shape. Your spec — "display information ONLY for the element selected" — replaces both.

The uniform rule I'd propose for every level (session, run, turn, api call, tool call):

```
[ breadcrumb of ancestors ]
[ header: the stored facts for this node ]
[ enrichment: description, category, outcome, friction ]
[ detail: what this level uniquely has — prompt text, model output, thinking, tool input/result ]
[ children: a bounded list of one-line summaries, each a link into the tree ]
[ prev / next sibling, each showing its kind and enriched description ]
```


| Option                                                         | Session page becomes                                                                                                           | Cost                                                                                                                             |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| A. Uniform node view everywhere                                | Header + enrichment + a bounded list of turn summaries. Run pages fold into the tree as run nodes. `after`/`turns`/`chips` die | You can no longer read a session by scrolling. Every turn is a click                                                             |
| B. Node view for drill-down, timeline kept as the session view | Today's paged timeline stays as what "session selected" shows; turn/call/tool get node views                                   | Two rendering models for the same data, two sets of bounds arithmetic, and the session view still scrolls past what you selected |
| C. Keep the run page as a page                                 | Runs stay at their own URL, everything else moves into the tree                                                                | Same shape rendered two ways forever                                                                                             |




### Recommendation: A

One rule covers five levels, which is what makes this cheap to build and cheap to describe in `docs/viewer.md`. It also kills the `<details>` narrow-screen decision left open in the handoff — that sidebar is being replaced.

What we give up: reading a session as a narrative. Today you scroll a session and the turns tell a story. After this, understanding a session means N clicks and N round-trips. Your prev/next control is the mitigation, and it needs to be good — arrow keys would help, which is question 5.

### User Response:

I think I agree with the general direction you're talking about, but I don't totally understand. Let me describe my ideal state. You would get a view based on what you've clicked on in the left-hand nav bar. So if you clicked on a tool call in the navbar, you would only see that tool call in the main pane with some surrounding metadata and previous/next buttons and descriptions. If you click on something higher level, like a "turn", then you would see summary information for that level along with a log of all the children (API calls, in the case of a turn) for the selected element. You should be able to click on any of the children to expand it with an option to jump to the full page for that element. So, yes, there are two views for a given node: an "expansion" view when viewing a parent of said node, and thew view you see when you've selected the specific node you want to view in the navbar. Maybe this is option B, but I'm not sure. 

## 2. Does an agent run hang under the tool call that spawned it, or under the turn?

A subagent run is spawned by a `Task` tool call. The store records that edge: `agent_runs.tool_use_id`. Today the viewer shows runs as **chips under the turn**, one click away.

- **Under the tool call** (`turn → api call → tool call → run → its turns → …`): the tree edge is the real causal edge, and the recursion is honest — a run's turns look exactly like a session's turns. But reaching a subagent takes four expansions instead of one, and subagent work is a lot of what you look at
- **Under the turn** (`turn → run`, today's chips): one click, matches your habit — but it puts a run beside api calls as if the turn spawned it directly, which isn't what happened, and it leaves the `Task` tool call showing a result whose work lives somewhere else in the tree



### Recommendation: under the tool call in the tree, with a shortcut in the main pane

The tree carries the causal structure. The turn's node view additionally lists the runs spawned anywhere inside it, so one click still gets you there when you're browsing.

What we give up: tree and main pane no longer agree exactly on what a turn's children are. That's a small honesty cost paid to keep the tree from lying about causation.

### User Response:

Let's just promote tool calls that launch sub-agents to be a little bit special in that they display differently in the navbar. Keep in mind that you almost always want to just view the sub-agent run and don't actually care about viewing the tool call, but sometimes you might want to view exactly what the tool call was. I think we should optimize for the case that's more common, which is jumping to the sub-agent, and then there should be some way to view the specific tool call.

Potentially the tool call actually goes under the sub-agent in the navbar. So if you select a subagent in the nav bar, you can see the tool call that was hung under it. Obviously, this is an inversion of how things actually work, but practically speaking, I think it would give us the best of both worlds. 

## 3. Is a tree node's expansion derived from the selection, or independently toggleable?

Your spec reads as derived — "when a turn is selected, it should display the API calls." That means one selected node implies exactly one open path: the ancestor chain expands, everything else stays shut. One URL, one tree state, nothing to remember.

The alternative is a twisty separate from the label: expanding a node without changing the main pane, so several branches sit open at once for comparison. That state has to live somewhere — an `open=` list in the URL (ugly, and it grows) or in JS (question 5).

### Recommendation: derived from selection

Every tree row is one hit target. The URL fully determines what's on screen, which keeps the viewer's paste-a-link-into-a-report property intact.

What we give up: comparing two turns side by side in the tree, and the tree collapsing behind you as you move across branches.

### User Response:

I agree. You can only have one path in the tree open at one time. That will make it simpler because there's no toggling on and off. There's just clicking which expands and then you can further click on children of the node you just clicked on. That's all we need for now 

## 4. Does every node get its own URL and a full-page render, or only a fragment?

If clicking swaps the right pane in place, a naive implementation leaves the address bar pointing at the session — and then a link you paste into a report doesn't open what you were looking at. `docs/viewer.md` currently promises the opposite: "Every page is a plain GET that you can paste into a report or message."

htmx can push a URL on swap (`hx-push-url`). Making that URL work on a cold load means each node view renders two ways: as a fragment for the swap, as a full page (tree + pane) for a fresh GET. With Jinja that's one template and a thin wrapper.

### Recommendation: one URL per node, both renders

Roughly `/session/{sid}/{kind}/{source}/{id}`. Fragment for swaps, full page for cold loads and pastes.

What we give up: a small amount of plumbing on every route, and a rule the implementer has to hold — nothing renders in the pane that isn't reachable cold.

### User Response:

Every view should update the URL, so I think I basically agree with your recommendation. You should be able to reload the page and view the same thing 

## 5. Does this iteration spend the "no first-party JS" property?

The viewer ships htmx and nothing else. Every interaction is a server round-trip returning HTML. That property is why the handoff's narrow-screen question was left for you, and it constrains several things you asked for.

What stays possible without our own JS: the whole tree, click-to-swap, prev/next, `title=` tooltips, server-rendered syntax highlighting. htmx's `hx-preserve` even keeps the tree's scroll position across swaps.

What a small script would buy: keyboard navigation (j/k/arrows through the tree — the real mitigation for question 1's cost), styled tooltips that work on touch, remembering scroll per node, collapse-on-mobile.

### Recommendation: keep it htmx-only this iteration

Nothing you listed strictly requires a script, and the tree rewrite is a big enough change to land without also changing the viewer's architecture underneath it. Revisit keyboard nav as its own change once you've used the tree and know whether clicking hurts.

What we give up: navigating a session by keyboard, which is exactly the workflow question 1 makes slower. If you expect to live in this UI daily, spending the property now may be the better trade — say so and I'll fold it in.

### User Response:

I agree. Let's stick with HTMX for now. 

## 6. Citations at the page bottom: delete, collapse, or make them show the query?

They exist because of the project's own rule: *a claim carries its query*. The intent is that a page you screenshot into a report can be re-run. `queries.citation()` mints them, and the analysis CLI uses the same function, so the format is shared with reports.

Your read is fair — `-- queries/view_session_header.sql session_id=cb76…` names a file you'd have to go find. But the file is real and on disk, so serving its text is a route and a template, not a project.


| Option                     | What the reader sees                                                                          | Cost                                                                                                                                                                          |
| -------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Delete                     | Nothing                                                                                       | The viewer stops honoring the evidence rule; a screenshot becomes unreproducible. Reversible, but the citation plumbing threads through every route and would rot once unused |
| Collapse into `<details>`  | "Queries behind this page (3)", closed                                                        | Still just filenames when opened                                                                                                                                              |
| Collapse and serve the SQL | Same, but each name links to `/query/{name}` showing the file with the bindings the page used | One route, one template, plus highlighting for SQL                                                                                                                            |




### Recommendation: collapse and serve the SQL

It answers your actual complaint — you want to see the query — and it costs about a day less than it sounds like, because the file is already on disk and the bindings are already in hand.

What we give up: a bottom-of-page element that isn't pulling its weight most of the time, and one more surface for the implementer to keep bounded.

### User Response:

Agree, let's collapse and serve the SQL. That will help me build a better understanding for how the data is structured and queried, which would be helpful

### Settled in phase 1's pending list

- Tooltip mechanism: my call
- "Store everything, redact nothing" applies to the store only. Fixtures committed to the repo stay redacted
- One PR, well-scoped commits, and it closes the handoff's leftovers too



# Phase 2 — how a node renders, and how you move between nodes

Your answer to question 1 named a model I hadn't proposed: **every node renders two ways** — an *expansion* when you're looking at its parent, and a *full view* when it's the selection. That's neither A nor B, and it's better than both. These six questions work out what it implies.

## 7. Does an inline expansion show its own children, or stop at its own detail?

Under your model, a turn's full view lists its api calls, and clicking one expands it in place. The open question is depth. Today expansion recurses: opening a turn shows its api calls, and each api call already carries a page of its tool calls.

The design rule I'd propose: **one node body, two mounts**. A node's summary and detail render from one template. The full view wraps that body with a children log, a breadcrumb, and prev/next. An inline expansion is the same body with none of those — so it stops, and its children appear only as a count and a "open this api call" link.


| Option                 | Expanding an api call inside a turn shows                             | Cost                                                                                    |
| ---------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Stop at the body       | Its model, tokens, cost, full text and thinking, and "4 tool calls →" | Reaching a tool result from a turn is: expand call, jump to call, expand tool           |
| Recurse one more level | The above plus a page of its tool calls, each expandable              | Two nodes' worth of children on screen, and the "only what you selected" property blurs |
| Recurse without limit  | The whole subtree, accordion-style                                    | Today's behavior, and the thing you asked to stop                                       |




### Recommendation: stop at the body

It's the rule that makes your two-renderings model hold: one template per node, mounted twice. It also keeps every page's byte cost predictable, which matters because the 500 KB bound is enforced by a test that reasons about exactly this.

What we give up: the quick scan where you open a turn and see everything it did at once. Three clicks to a tool result instead of one.

### User Response:

I agree, stopping at the body is fine for now. Potentially later we can consider recursing one more level, but let's keep it simple to start 

## 8. Where does the promoted sub-agent node sit — in the Task tool call's slot, or hoisted under the turn?

Your inversion is settled: the run node carries the agent type and description, and the `Task` tool call that spawned it hangs *under* the run as a child. The remaining fork is where that run node sits in the tree.

```
in the tool call's slot                hoisted under the turn
turn                                    turn
├─ api call 1                           ├─ 🤖 auditor run
├─ api call 2                           │  ├─ (the Task call that spawned it)
│  ├─ Read                              │  └─ its turns…
│  └─ 🤖 auditor run                    ├─ api call 1
│     ├─ (the Task call)                └─ api call 2
│     └─ its turns…                        └─ Read
```

- **In the slot**: the run sits where its tool call sat, in order beside its sibling tool calls. Two expansions from the turn. Nothing about the api call's contents is hidden or reordered
- **Hoisted**: one expansion from the turn, matching today's chips. But the api call that made the `Task` request now shows a tool call list with a hole in it, and a turn's children become two kinds of thing



### Recommendation: in the slot, with the turn's full view listing every run spawned inside it

The tree stays honest about ordering and the api call stays complete. You still get one-click access, just from the main pane rather than the tree — and per question 7 the turn's full view is where you'd be anyway.

What we give up: one extra expansion in the tree, on the path you said you take most.

### User Response:

Let's hoist but put below the API call that made the tool call to launch the subagent and try to add a visual indicator to note that it came from that previous API call We can still show the agent call under the API call > tool call so it ca can be accessed both ways. Also, maybe we can add a toggle to the navbar to turn off/onn showing API calls made by that model or to just view the subagent calls. Not sure what's best there, but I feel like this would be nice, and the pi coding agent export already contains something like this (filter navbar)

## 9. Do prev/next walk siblings, or walk the whole session depth-first?

You asked for prev/next with the neighbor's kind and enriched description. Two readings, and the choice decides whether the narrative loss from question 1 is recovered.

- **Siblings**: next from turn 3 is turn 4; next from the last api call in a turn is disabled. Simple, predictable, matches the tree
- **Depth-first walk**: next from the last api call in turn 3 is turn 4. Holding "next" walks the entire session in the order it happened, subagent work included — you get the narrative back, one node per screen



### Recommendation: depth-first over the whole session

It's the answer to "you can no longer read a session by scrolling," and it costs nothing extra at the data layer since the tree order already is the walk order. Show the neighbor's kind so a level change is never a surprise ("next: turn 4 of 19").

What we give up: predictability. Next sometimes moves sideways, sometimes pops up two levels. A sibling-only pair beside it would fix that, but that's four controls and I'd rather ship two.

### User Response:

I agree with the recommendation that if there's no next sibling, we should go to the next higher level node. 

## 10. "Transcript details below the summary" — the modeled value, or the raw JSON record?

Your spec says the main pane carries summary information and then the transcript details. Two things could mean:

- **The modeled value**: for a turn, the prompt text; for an api call, its text and thinking; for a tool call, its input and result. This is what the viewer shows today, cut to a head with a control to open the whole thing
- **The raw archived line**: the JSON object Claude Code wrote, exactly as recorded. The store keeps it in `raw_records.raw`, and `view_turn_records.sql` already joins a turn to its line by uuid, so the link exists at every level



### Recommendation: both, with the raw line in a closed `<details>` under the modeled value

The modeled value is what you read; the raw line is what you check the model against, and it's the thing that settles "does the store actually have this." Closed by default so it costs one request only when opened.

What we give up: a little vertical space, and a control on every node view that most visits won't touch.

### User Response:

Yes, definitely modeled values. We want to create the best visual display and then allow the user to drill into view details as you've described. 

## 11. Syntax highlighting: Pygments server-side, JSON and SQL only, plain text above a size ceiling?

Highlighting can run server-side (Pygments emits `<span>`s, no script) or client-side (highlight.js, which spends the property you just kept). Server-side is the only option consistent with question 5.

The trap is size. Tool results and offloads run to tens of megabytes, and highlighting inflates markup three to five times while burning server CPU. So a ceiling: below it, highlight; above it, serve plain with a line saying why.

Scope I'd propose: **JSON** (tool inputs, tool results, raw records) and **SQL** (the query view from question 6). Markdown already renders. Diffs, Python, and shell output stay plain.

### Recommendation: Pygments, those two languages, plain above 256 KB with a notice

What we give up: highlighted `Edit` diffs and `Bash` output, which are common tool payloads. Add them later if the plain rendering annoys you.

### User Response:

Agree with recommendation. 

## 12. What marks a string as model-written, and what does its tooltip say?

Every enrichment row carries `model`, `prompt_version`, `taxonomy_version`, `enriched_at`, and a computed staleness. Nothing on screen currently distinguishes a model-written line from a recorded one.

One collision to fix regardless: `agent_runs.description` is Claude Code's own recorded Task description — what the parent agent typed when spawning the subagent — and enrichment's `description` is model-written. Same word, two sources. I'll relabel the recorded one "task brief" in the UI so "description" means enrichment everywhere, unless you'd rather it read differently.


| Option                                 | On screen                                                                                  | Cost                                                                                                |
| -------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Glyph prefix                           | `✨ Audited the bounds tests and found…` with a `title` giving model, date, and stale state | A glyph on every enriched string, including tree rows — bytes in the nav budget                     |
| Styling only                           | Enriched text in a distinct color or italic, with a legend                                 | Invisible in a screenshot pasted into a report, and color is currently spent on the spend bar alone |
| Glyph in the pane, styling in the tree | Full marker where there's room, cheap marker where there isn't                             | Two conventions to remember                                                                         |




### Recommendation: glyph prefix everywhere, tooltip only in the main pane

The tree gets the glyph without the `title` so nav rows stay cheap; the main pane carries the full source line. A screenshot then shows what was generated and what was recorded.

What we give up: knowing which model wrote a tree row's description without clicking into it.

### User Response:

Agree with recommendation.

### Settled without a question

- Viewer UI conventions (tooltips, the enrichment glyph, the two-mount rule) go in a new `.claude/rules/viewer-ui.md` — `docs/documentation.md` puts conventions for a set of files there, and `docs/viewer.md` stays the reader-facing guide
- "Store everything, redact nothing for now" goes in `CLAUDE.md`'s privacy section with a line in `docs/store.md`
- The tree renders the selected node's ancestor chain plus each ancestor's children, nothing else — bounded by construction
- A session node's children are its main-thread turns, plus the two buckets that exist today: unattributed calls and unattached runs



# Phase 3 — the hoisted run, the filter, and what else lives in the tree

Your answer to question 8 changed the shape: the run hoists under the turn, sits below the api call that spawned it, and stays reachable through the tool call too. That's a node reachable two ways, which needs a rule. The filter you floated needs a shape. Then three loose ends.

## 13. When a run is reachable two ways, is it one tree node with a pointer, or two tree nodes?

You asked for both paths: the run hoisted under the turn, and still gettable through api call → tool call. Two ways to arrive is fine; two *nodes* is not — the tree would show the same run twice, the URL would have to say which path you took, and selecting one leaves the other unhighlighted.

The way out is that the two paths aren't the same node. One is the run; the other is the `Task` tool call, which is a real node with real content — the brief the parent wrote.

```
turn 4
├─ api call 1
├─ api call 2
│  ├─ Read src/hyphae/view/app.py
│  └─ Task → auditor            ← a tool call node; its view links to the run
├─ 🤖 auditor run  ↖ from api call 2
│  ├─ turn 1
│  └─ turn 2
└─ api call 3
```


| Option                                     | The tree holds                                                                                                                                                                           | Cost                                                                                                                                  |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| One run node, tool call keeps its own slot | The run under the turn; the `Task` tool call under its api call, its view leading with "launched auditor run →". The run's view shows the brief inline, so you rarely need the tool call | Two rows mention the same event. The tie is the `↖ from api call 2` marker and the link                                               |
| Two run nodes, one canonical               | The run appears under the turn and under the tool call; one URL, the other position is an alias                                                                                          | Selection highlights one place and not the other, and the depth-first walk from question 9 must skip the alias or visit the run twice |
| Run only under the tool call               | No hoisting                                                                                                                                                                              | You rejected this in question 8                                                                                                       |




### Recommendation: one run node, tool call keeps its own slot

Nothing is duplicated, the depth-first walk stays a walk, and the rare "what exactly did it ask for" case is one row away in its natural position — plus the brief is on the run's own view anyway.

What we give up: the run does not literally appear beneath the tool call in the tree, so "both ways" means one tree row and one link rather than two tree rows.

### User Response:

Agree with recommendation 

## 14. What shape does the nav filter take?

You want to hide api calls, or see only subagents. That state is a query parameter — it survives navigation and pastes into a report, same as everything else.

The question is how many modes, because each one has to produce a tree where every node still has a parent. Hiding api calls means their tool calls and runs hoist to the turn.


| Option                    | Controls                                 | What the tree shows                                                                                       |
| ------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| One toggle: api calls off | A checkbox                               | Turn → tool calls and runs, hoisted. Everything else unchanged                                            |
| Three presets             | Radio: full / no api calls / agents only | Adds a mode where the tree is just the run hierarchy, no turns at all — the org chart of a session        |
| Per-kind checkboxes       | Four boxes                               | Every combination, including ones that make no sense (tool calls without api calls but with turns hidden) |




### Recommendation: three presets

"Agents only" is the view you'll actually reach for on a session that spawned twenty subagents, and it's cheap once the hoisting logic exists for mode two. Presets keep the combinations to three trees the implementer must get right, instead of sixteen.

What we give up: an api-calls-hidden-but-tool-calls-shown-only-under-runs kind of mix. Presets can't express every taste.

### User Response:

Agree with recommendation. 

## 15. Do tree rows keep the log-scale spend bar?

Today's map draws a bar under every node whose length is that node's share of session spend, logarithmic over three orders of magnitude. It's the one thing the viewer spends color on, and it's the fastest way to spot where a session's money went. The map is being replaced by the tree.

Turns, runs, and api calls all have a cost. Tool calls don't — cost lives on the api call that requested them.

- **Keep the bar** on every row that has a cost: the tree becomes the spend map as well as the navigation. Costs bytes on every nav row, and the nav fragment is the one with the tightest budget
- **Drop it**: the tree stays text, and spend lives only in each node's header. Simpler, and the tree renders in less than half the markup



### Recommendation: keep it

You lose the only at-a-glance signal in the viewer otherwise, and finding an expensive turn goes back to reading numbers. Tool-call rows simply have no bar, which reads correctly — they didn't spend anything.

What we give up: roughly a third of the nav fragment's byte budget, which then constrains the glyphs and title attributes from question 12.

### User Response:

Agree with recommendation. 

## 16. Do compaction markers appear in the tree?

A compaction is Claude Code discarding conversation history mid-session. The store records each one with its trigger, token counts before and after, and duration. Today they render as markers along the timeline, capped with a "+N more" line.

They matter because a compaction explains why an agent forgot something — often the most interesting thing in a session. But a compaction isn't a node in the turn hierarchy; it happens *between* turns.

- **A row in the tree**, sitting between the turns it separates, selectable, with a small node view showing trigger and token drop
- **A band in the turn list only**, drawn in the session's children log but not in the tree — visible where you'd notice it, absent from navigation
- **Drop them** from the session page and leave them to the raw records browser



### Recommendation: a row in the tree

A compaction is exactly the kind of thing you navigate to when reading a session, and the depth-first walk from question 9 should stop on it — otherwise the narrative you get by holding "next" skips the moment the agent lost its memory.

What we give up: the tree's rows are no longer all one kind of thing, so the walk and the filter both need a case for markers.

### User Response:

Agree with recommendation. 

## 17. Do the handoff's leftovers land before the rewrite, or after?

One PR, well-scoped commits, all leftovers closed. The order matters because the leftovers straddle the rewrite:

- **Survives the rewrite**: the page-2 pager link test and the `narrowing` boundary bug — both in `listing.py`, the session *list*, which this change doesn't touch. Also the testing-plan doc fix and the age-out guard
- **Lands in the rewrite's blast radius**: the `threads.`* mutation survivors, and most of `listing.py`'s 24 unclassified ones. `threads.py` renders the session page's timeline, so triaging its survivors before the rewrite means triaging code that's about to be deleted
- **Made moot**: the narrow-screen `<details>` decision — that sidebar is being replaced
- **Unrelated**: the empty `project_dir` fail-fast, which belongs at extract, not in the viewer



### Recommendation: list-layer fixes first, viewer mutation triage last

Commits one through three close the list-layer gaps and the doc fix against today's code. Then the rewrite. Then run `mise run mutate` against the new view layer and triage what survives — which is the honest version of that task anyway, since the surviving mutants will be different ones.

What we give up: the PR's first commits and last commits are on unrelated code, which makes it a slightly odd read. The alternative — a second PR for the leftovers — you already ruled out.

### User Response:

Agree with recommendation 

## Pending questions (depends on answers above)

- Byte budget arithmetic for the nav fragment: spend bars, glyphs, and `title` attributes against 500 KB, and whether the deferred truncation-ellipsis fork fits in this PR — depends on Q15
  - Do whatever you think is best. 
- Whether the filter's "agents only" mode needs its own depth-first walk order, or reuses the full one — depends on Q14
  - Do whatever you think is 
- What the prompt for the implementing agent must carry beyond these decisions: which docs to update, what the testing plan owes, and how much of the existing view layer it may delete outright
  - The agent can have full autonomy and do whatever it thinks is best. 

