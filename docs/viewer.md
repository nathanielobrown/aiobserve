# The trace viewer

`aiobserve view` opens the trace store in a local browser. Everything a session recorded is a node with a page of its own — the session, its turns, the runs it spawned, the api calls, the tool calls, the compactions between them — and you read one node at a time, with a tree beside it showing where that node sits. Copy the URL of anything you want to cite.

The server binds only to `127.0.0.1`, opens the store read-only, and serves only vendored assets. Run `aiobserve view --help` for flags. [The node-browser design](../plans/viewer-node-browser/design.md) holds the choices behind the tree, and [the trace-viewer design](../plans/trace-viewer/design.md) the ones behind the pages around it. Editing a template is governed by `.claude/rules/viewer-ui.md`.

## Follow a session down to any record it holds

```mermaid
flowchart LR
    projects["projects"] -->|"a project row"| session_list["session list"]
    session_list -->|"a row"| session["a session"]
    session -->|"a turn of its main thread"| turn["a turn"]
    session -->|"a context rewrite between turns"| compaction["a compaction"]
    session -->|"what attached to nothing"| bucket["a bucket"]
    turn -->|"an api call"| api_call["an api call"]
    turn -->|"a run spawned under it"| run["an agent run"]
    api_call -->|"a tool call"| tool["a tool call"]
    tool -->|"the run a Task call started"| run
    run -->|"its own turns"| turn
    bucket -->|"a call or a run it holds"| api_call
    turn -->|"the transcript line it was read from"| records["raw records"]
    tool -->|"a result written to a file"| offload["an offloaded result"]
    turn -.->|"a value the pane only previews"| value["one whole value"]
    api_call -.-> value
    tool -.-> value
    records -.->|"opening a row"| value
    turn -.->|"a child's body, in place"| body["a node's body"]
```

Solid edges lead to pages with their own URLs. Dotted edges fetch a fragment into the open page.

| Page | Route |
| --- | --- |
| Projects | `/` |
| Session list | `/sessions` |
| A session | `/session/{session_id}` |
| A turn | `/session/{session_id}/turn/{source}/{turn_id}` |
| An agent run | `/session/{session_id}/run/{run_id}` |
| An api call | `/session/{session_id}/call/{source}/{api_call_id}` |
| A tool call | `/session/{session_id}/tool/{source}/{tool_call_id}` |
| A compaction | `/session/{session_id}/compaction/{source}/{compaction_id}` |
| A thread's calls under no turn | `/session/{session_id}/unattributed/{source}` |
| The runs nothing placed | `/session/{session_id}/unattached` |
| Raw records | `/session/{session_id}/records/{source}` |
| An offloaded result | `/session/{session_id}/offload/{name}` |
| The SQL behind a page | `/query/{name}` |

`src/aiobserve/view/app.py` declares every route, fragments included. Nothing renders in the pane that a cold GET of its own URL doesn't render whole, tree and all.

## The landing page counts projects

`/` lists the projects the store holds sessions for, most recently active first, with sessions and spend over the last 7 days, the last 30, and all time. The page is [bounded](#hard-bounds-keep-every-page-below-500-kb) like every other, so a store holding more projects than it shows ends with the number it left out. A row opens the session list filtered to that project. Sessions recorded from a checkout's worktrees count under the checkout, and sessions with no recorded directory gather into an unlinked `(no project)` row. The footer cites the query and `as_of`, the date both windows were measured back from, so the page reproduces tomorrow.

## The session list keeps the query visible

The list has one row per session. A row shows how long ago the session started over its timestamp, its title and project, rollup counts, its tool errors as a rate over the count, cost over output tokens, wall time over active time, the agent types it spawned with a count of each, and its skills. A column showing two values is one cell read as two lines: the value the column is scanned for, and the texture under it. Over a store an enrichment pass has run against, a Work column says what kinds of turn the pass found. Every column heading sorts by that column; click it again to reverse the order. Sessions the store has no value for sort last either way — a session it knows nothing about is neither the newest nor the oldest. Errors sorts by the count rather than the rate: one failure in one call is 100% and not the session someone ranking by errors is looking for. A `*` after the cost means the session called a model missing from the price table, so the shown total is a floor. A project directory under the home of whoever is reading prints with `~` in its place, on this page and on the landing page and the session header; the link, the filter and the suggestion box still carry the path the store holds, because that is what a filter matches.

Rows show only the head of long text: 100 characters for the title and project path, four skill names and four agent types followed by the number omitted, and three kinds of work. The session header shows five skill names of up to 60 characters along with its other fields; it too stays bounded.

The form above the list filters by project, date range, skill, or a minimum number of failed tool calls. The project filter matches a path prefix, the same rule as the CLI's `--project`: filtering by a checkout keeps the sessions its worktrees recorded.

Filters survive sorting and paging. The `clear` link beside the form drops them. The footer prints a citation after paging and names every active filter, so it describes the rows on screen.

## The tree opens one path and nothing else

Beside every node page is the session's tree, with one path open: the selection, its ancestors, each ancestor's children, and the selection's own children. Clicking a row selects it, which opens that row's path and closes the one you left. There are no independent twisties and no way to open two branches at once, so the tree is bounded however deep the session goes.

A session's children are the main thread's turns, its compactions in the place they happened, its calls that answer no turn, and the runs nothing placed. A turn's children are its api calls; an api call's are its tool calls. An agent run reads like a session: its children are its own turns. A run renders under the *turn* it belongs to, right after the api call that spawned it, and a `↖ from api call 2` tie says which call that was — the run is the turn's child, not the call's, and a `Task` tool call keeps its own slot with a link to the run at the head of its page.

Above the rows is the fold: **full**, **no api calls**, **agents only**, with the one in force marked. Each is the node you are reading under a different tree, so a switch keeps your place and your knobs — the fold is [`?nav=`](#urls-preserve-the-query-behind-what-you-saw), and the control is the only link on the page that changes it.

Every row with spend carries a bar along its edge: its share of what the session cost, logarithmic over three orders of magnitude, because a session's cheapest turn and its dearest are that far apart and a linear scale draws all but the largest as nothing. Tool calls have no bar; what a tool call took is the api call's.

A level shows at most 25 children, and a `+N more` row says what the cap left out and links to the parent's own page, which pages its children rather than capping them. The row the open path descends through is always kept, inside the cap.

## The pane reads one node

The pane leads with the crumb chain down to the node, then the node's label and the facts the store holds for it. Under those:

- What an enrichment pass said about the node, when a pass has reached it
- The node's own fat values, cut to 4,000 characters, each with a link that fetches the rest: a turn's prompt, a run's task brief, an api call's text and thinking, a tool call's input and result
- The thread's transcript, and — for a turn — the archived line it was read from, in a `<details>` that fetches on open
- The children log: a page of 12 children, each a link to its own page and a `body` toggle that opens the child's own pane in place, without leaving the parent. An opened body stops there; what's under it is a count and a link
- `←` and `→`, which walk the whole session depth-first — into a node's children, on to its next sibling, then out. Each names the neighbour's kind and its label, so a change of level is never a surprise. The buckets and the compactions are stops like any other, and the walk ignores what the tree was capped to: a reading order that shortened with the sidebar would skip nodes silently

JSON and SQL are marked up in their own syntax; everything else a transcript wrote is prose, and Markdown renders as Markdown. A value past 256,000 characters prints as stored with a line saying why. Every page's footer cites the queries behind it, and each citation links to `/query/{name}`, which shows that query's SQL under the bindings the page used.

`agent_runs.description` shows as **task brief**: it is the brief Claude Code recorded for the run, not a description of what the run did. On this screen "description" always means enrichment.

## Enrichment appears beside the recorded trace

After [an enrichment pass](enrichment.md), the viewer places its output beside the stored telemetry. A `✨` marks every string a model wrote rather than a session — on the tree row, the crumb, the log line, and the walk control, wherever a description stands in for a label. The pane carries the one glyph that explains itself: hover it for the model, when it ran, the prompt and taxonomy versions, and whether the row is stale. `stale` means the pass used an older prompt or taxonomy version, so rerun the pass; it does not mean the saved description is false.

The session list adds each session's one-line description and two tags, cutting the line to the same 100-character head as the title. It does not show `stale` because the list joins the words written by a pass without loading the versions needed to judge them.

A store that has never been enriched has none of the enrichment tables. The viewer then shows no enrichment fields, and cites no enrichment query. An item the current pass has not reached looks the same.

## URLs preserve the query behind what you saw

Every page is a plain GET you can paste into a report or message. A node URL names the kind before the id — `/session/{session_id}/turn/{source}/{turn_id}` — and `source` is the thread the node was recorded on, `main` or a run's id. A run is the exception: its id is also the thread its rows carry, so `/session/{session_id}/run/{run_id}` says it once.

Node pages take four knobs, and every link on a page carries the ones that aren't defaults, so a click serves the URL it displays:

| Knob | What it does |
| --- | --- |
| `?nav=full` | The whole tree. The default |
| `?nav=noapi` | The api calls folded away, each turn's tool calls standing directly under it |
| `?nav=agents` | The runs alone, each under the run that spawned it — the session's org chart |
| `?kin=` | Children per open level, at most 25 |
| `?log=` | Rows in the pane's children log, at most 12 |
| `?detail=` | Characters of each value the pane previews, at most 4,000 |

The three sizes only go down. Each default is also its ceiling, because the page's byte bound is arithmetic over the defaults and there is no headroom to spend. A size outside its range or a `nav` the viewer doesn't have returns 400 rather than a guess.

The presets are the [fold above the tree](#the-tree-opens-one-path-and-nothing-else), and typing one into the URL does the same thing. Every preset leaves every visible node with a visible parent, and a level whose preset would hide the path you are standing on renders in full instead.

The session list accepts `sort`, `direction`, `page`, `size`, and its filter keys, and returns 400 for an unknown key, an unknown sort or direction, a filter value of the wrong type, or a page outside its bounds. Sort keys map to fixed columns, filter keys map to fixed predicates, and request values reach SQL only as bound parameters. A children log pages with `?after=`, the index of the last child already shown.

Reports cite raw records as `(session_id, source, line_no)`. The records URL derives from that natural key, so a later port or route change does not invalidate the saved tuple. This form opens the records browser on the cited line:

```text
/session/{session_id}/records/{source}?after={line_no - 1}#L{line_no}
```

## Large values open only when you ask

The records page shows each archived line's number, type, length, and head; opening a row fetches the full line. Every turn links both to its thread's transcript and to the one line it was read from, so you can move between the modeled turn and the archived record in one click.

When Claude Code writes a tool result to a file instead of the transcript, the result links to `/session/{session_id}/offload/{name}`. Some offloads are tens of megabytes, so the page serves them in chunks and returns the next offset. The route treats `name` as a key into `offload_files`; it never opens a path from the URL.

## Extracts and page loads can contend for the store

The viewer closes its database connection after each request, leaving `aiobserve extract` free to take DuckDB's write lock while the viewer is idle. Neither side retries a collision:

- If an extract starts while a page request holds the store, the extract fails with DuckDB's lock error. Reload the page, then run the extract again
- If a page loads while an extract holds the lock, the viewer returns 503 and says the store is being written. Reload after the writer releases the lock
- If a re-extract changes the schema while the viewer runs, the viewer returns 503 with the schema version this build expects. Restart the viewer

The viewer fails at startup if the store is missing, its schema is unsupported, or the port is already in use.

## Hard bounds keep every page below 500 KB

A browser can hang if the viewer renders a whole transcript. The viewer therefore bounds the row counts and text behind pages at the SQL boundary. Those queries do not select an uncut column that can hold agent or user content: `raw`, `text`, `thinking`, `result`, `input`, `content`, `agent_type`, `model`, or `description`. `tests/view/test_bounds.py` enforces that rule.

Full-value requests are the declared exception. Each returns one transcript line, prompt, task brief, text block, thinking block, or tool value, so its size depends on the largest matching value rather than a page of them. Offloads remain chunked. JSON is re-indented only while doing so remains cheap; deeply nested data stays as stored because indentation work grows quadratically with nesting.

`src/aiobserve/view/bounds.py` defines each page size beside its ceiling. A typed size above its ceiling returns 400. The payload checks charge each transcript character at five bytes, the longest HTML escape, and add measured markup costs from the canonical store.

| Surface | Default and limit |
| --- | --- |
| Session list | 104 sessions; each long string is cut to 100 characters, skills and agent types to four 20-character names, and work to three |
| Projects | 100 projects; the path is cut to 100 characters |
| Tree | 25 children per open level, 16 levels deep, each label cut to 48 characters |
| Children log | 12 rows, each string cut to 300 characters |
| Previewed value | 4,000 characters, with the rest a fetch away |
| Raw records | 100 rows by default, at most 200 |
| Offload | 50,000 characters by default, at most 60,000 |
| Syntax highlighting | 256,000 characters, above which the value prints as stored |

The worst node page comes to 466,658 bytes of the 500,000 allowed. The tree is what multiplies: an open path is `1 + 16 × (25 + 1)` = 417 rows, and a row is pinned at 914 bytes, which is 381,138 of the page. The rest is 16 crumbs at 558 bytes, 12 log rows at 1,616, two previewed values at 20,600, and 16,000 of chrome — leaving 33,342 spare. `TREE_ROW_BYTES` is measured through the app rather than budgeted, at a label of nothing but `&` and the longest query string a link can carry, and pinned with no slack: a byte of slack there is 417 bytes of page. Nearly all of a row is its URL written twice, the `href` a reader follows and the `hx-get` htmx fetches — what the fetch then does with the response is written once on `#tree-rows` — so a store whose agent runs carry longer ids than the recorded corpus does is a re-measure.

The session list is bound independently of corpus size. Its filter box offers the 10 busiest project paths that fit its bound, whole or not at all; a cut path would filter by a directory nobody named. The projects page cuts a long path the same way and leaves that row unlinked. The same rule keeps row filtering correct: the viewer filters whole titles, paths, and skill lists, then cuts only the rows it renders. The worst-case list projects to 499 KB: 10 KB of page chrome plus 104 rows at 4.7 KB each.

A session header does not have a reader-controlled size, so its query cuts every string, skill list, PR list, session description, and friction line. `tests/view/test_bounds.py` measures these fixed costs and checks every route the viewer exposes against the 500 KB ceiling, once with no query string and once with the dearest knobs a URL can carry.
