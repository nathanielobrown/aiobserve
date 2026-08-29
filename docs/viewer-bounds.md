# Viewer URLs and page bounds

What a viewer URL may ask for, and what the page that comes back is allowed to weigh. [The viewer guide](viewer.md) says what each page shows; this one says what bounds it. `src/hyphae/view/bounds.py` defines every number here.

## URLs preserve the query behind what you saw

Every page is a plain GET you can paste into a report or message. One rule shapes every path: **a word saying what kind of id comes next stands in front of each id, so no two ids sit side by side**. A turn reads at `/session/{session_id}/thread/{source}/turn/{turn_id}` — a session, then the thread the node was recorded on (`main` or a run's id), then the turn. A run is the exception the rule allows: its id is also the thread its rows carry, so `/session/{session_id}/run/{run_id}` says it once. Fragment URLs obey the rule too, and a node's fragment is its own path under a prefix: `/fragment/body/session/{session_id}/thread/{source}/turn/{turn_id}`. `tests/view/test_app.py` holds every route the app exposes to the rule.

Node pages take four knobs, and every link on a page carries the ones that aren't defaults, so a click serves the URL it displays:

<!-- aigarden:cog sh "uv run python -m tools.gen_bounds knobs" -->
| Knob | What it does |
| --- | --- |
| `?nav=full` | The whole NavTree. The default |
| `?nav=noapi` | The api calls folded away, each turn's tool calls standing directly under it |
| `?nav=agents` | The runs alone, each under the run that spawned it — the session's org chart |
| `?kin=` | Children per open level, at most 200 |
| `?log=` | Rows in one page of the reading pane's children log, at most 100 |
| `?detail=` | Characters of each value the reading pane previews, at most 4,000 |
<!-- aigarden:end -->

The three sizes only go down. Each default is also its ceiling, because the page's byte bound is arithmetic over the defaults and there is no headroom to spend. A size outside its range or a `nav` the viewer doesn't have returns 400 rather than a guess.

A value's own URL — the one a preview's `+N more character(s)` link opens — answers 404 where the row is there and the column under it is empty: a `Read` ran no command, a slash turn typed no prompt of its own. Nothing links to one of those, so a request for one is a URL that was typed or kept.

How wide the NavTree is drawn is the one thing you set that no URL carries: it belongs to the screen you are reading on, not to the node you linked to, so a pasted link would hand someone else your column. Drag the handle between the NavTree and the reading pane — or focus it and press the arrow keys — and this browser keeps the width for every session you open.

The presets are the [control above the NavTree](viewer.md#the-navtree-opens-one-path-and-nothing-else), and typing one into the URL does the same thing. Every preset leaves every visible node with a visible parent, and a level whose preset would hide the path you are standing on renders in full instead.

The session list accepts `sort`, `direction`, `page`, `size`, and its filter keys, and returns 400 for an unknown key, an unknown sort or direction, a filter value of the wrong type, or a page outside its bounds. Sort keys map to fixed columns, filter keys map to fixed predicates, and request values reach SQL only as bound parameters. A children log pages with `?page=`, numbered from one; page one is the node's own URL. A number below one is a 400, like any other size a URL carries out of bounds; a number past the level's last page is a 404, because only the level knows where it ends.

Reports cite raw records as `(session_id, source, line_no)`. The records URL derives from that natural key, so a later port or route change does not invalidate the saved tuple. This form opens the records browser on the cited line:

```text
/session/{session_id}/thread/{source}/records?after={line_no - 1}#L{line_no}
```

## Large values open only when you ask

The records page shows each archived line's number, type, length, and head; opening a row fetches the full line. The record the page opens on — the first row, which is the one a citation names — arrives open with its line already fetched, as long as it is under 15,000 characters. A record wider than that waits for a click like every other row: nothing bounds how long an archived line is, the store holds one of 7.6 million characters, and a page that pulls one unasked is a page nobody budgeted. Every turn links both to its thread's transcript and to the one line it was read from, so you can move between the modeled turn and the archived record in one click.

When Claude Code writes a tool result to a file instead of the transcript, the result links to `/session/{session_id}/offload/{offload_name}`. Some offloads are tens of megabytes, so the page serves them in chunks and returns the next offset. The route treats the name as a key into `offload_files`; it never opens a path from the URL.

## Hard bounds cap every page, most at 500 KB

A browser can hang if the viewer renders a whole transcript. The viewer therefore bounds the row counts and text behind pages at the SQL boundary. Those queries do not select an uncut column that can hold agent or user content: `raw`, `text`, `thinking`, `result`, `input`, `content`, `agent_type`, `model`, `brief`, or `description`. `tests/view/test_bounds.py` enforces that rule.

Full-value requests are the declared exception. Each returns one whole value — `src/hyphae/view/store.py:Value` names them, from a transcript line to a line an enrichment pass wrote — so its size depends on the largest such value in the store rather than on a page of them. The tail row's fetch is a second: a reader who clicks `+N more` is asking for the rest of that level, so it serves the level less the window at a NavTree row apiece — 1.8 MB for the widest level in the canonical store, 1,587 tool calls under one turn with the api calls folded away, since the fetch serves whichever preset the URL names. A query's citation page is the third, and the one no corpus moves: it is the size of a statement we ship. Offloads remain chunked. JSON is re-indented only while doing so remains cheap; deeply nested data stays as stored because indentation work grows quadratically with nesting.

`src/hyphae/view/bounds.py` defines each page size beside its ceiling. A typed size above its ceiling returns 400. The payload checks charge each transcript character at five bytes, the longest HTML escape, and add measured markup costs from the canonical store.

<!-- aigarden:cog sh "uv run python -m tools.gen_bounds bounds" -->
| Surface | Default and limit |
| --- | --- |
| Session list | 113 sessions; each long string is cut to 100 characters, skills and agent types to 4 20-character names, and work to 3 |
| Projects | 100 projects; the path is cut to 100 characters |
| A session's errors | 100 failed tool calls; each title is cut to 110 characters |
| NavTree | 200 children per open level, 16 levels deep, each title cut to 110 characters |
| Children log | 100 rows a page, each string cut to 300 characters |
| Previewed value | 4,000 characters, with the rest a fetch away |
| Raw records | 100 rows by default, at most 200 |
| Offload | 50,000 characters by default, at most 60,000 |
| Syntax highlighting | 256,000 characters, above which the value prints as stored |
<!-- aigarden:end -->

Each page is weighed against its ceiling at the widest response its route can be asked for — every size at the top of its range, and every string at the width the query cuts it to — rather than at the widest page this corpus happens to hold.

<!-- aigarden:cog sh "uv run python -m tools.gen_bounds pages" -->
| Page | Worst case, in bytes |
| --- | --- |
| Node page | 6,483,912 of the 6,500,000 it is allowed |
| Expansion | 621,164 of 625,000 |
| Session list | 498,625 of 500,000 |
| Projects | 301,225 of 500,000 |
| A session's errors | 96,278 of 500,000 |
| Raw records | 288,000 of 500,000 |
<!-- aigarden:end -->

The node page is weighed against a budget of its own rather than the 500,000 the rest share, because the NavTree is a window a reader widens in place and not a page. This is what fills it:

<!-- aigarden:cog sh "uv run python -m tools.gen_bounds node" -->
| Part of the node page | What it comes to, in bytes |
| --- | --- |
| NavTree | 3,217 rows at 1,703: 5,478,551 |
| Children log | 100 rows at 6,165: 616,500 |
| Previewed values | 3 rendered at 120,550: 361,650 |
| Crumbs | 16 at 556: 8,896 |
| Pager | 565 |
| Chrome | 17,750 |
| Spare | 16,088 |
<!-- aigarden:end -->

The NavTree is what multiplies: an open path is a row for the root and one for every child of every level it descends through, and those rows are most of the page. `NAV_TREE_ROW_BYTES` is measured through the app rather than budgeted, at a title of nothing but `&` and the longest query string a link can carry, and pinned with no slack in either direction — a byte of slack there is a byte on every row of the widest page, and the room above is spoken for. Nearly all of a row is its URL, written three times: the `href` a reader follows, the `hx-get` htmx fetches, and the popover's own path under a prefix. What the click does with its response is written once on `#nav-tree-rows` and inherited; what the popover does with its own cannot be, because htmx walks up from the element that fetched, and a swap written on the row would be taken by the link inside it — so its five attributes are spelled out on every row, and a store whose agent runs carry longer ids than the recorded corpus does is a re-measure. `NODE_BYTES` in `tests/view/budgets.py` records what each raise of the ceiling bought, and the spare in the table above is what the next thing a row grows by will be measured against.

A preview the page marks up is priced at 30 bytes a character against the five an escaped one costs — an element around every token — and that price holds whether the markup is the syntax the record named or the Markdown a session wrote. A run's is the first reading pane whose three previews are all rendered, which is why the arithmetic charges three. A log row is the dearest thing on the page after the NavTree: it prints up to three of the store's own strings at the width the log query cuts them to, which is what a reader gets for reading a level without opening it. An api call's is the widest row there is — the model that answered, the head of what it said, and the tools it went on to call.

An expansion carries a ceiling of its own. A click fetches it, like the full-value requests exempted above, but what comes back is a page of rows rather than one value: an api call's body opened in a log row lists the tools it called, at the `?log=` the reader is already reading under. `tests/view/budgets.py` declares that ceiling rather than deriving it from the 500,000, because a page of rows nobody counted is what a click can afford to hide.

A session's errors list grows the way the corpus pages do — nothing about a session caps how often its tools fail — so it is bounded the same way. More than half of one of its rows is a title of nothing but `&`.

The session list is bound independently of corpus size. Its filter box offers the 10 busiest project paths that fit its bound, whole or not at all; a cut path would filter by a directory nobody named. The projects page cuts a long path the same way and leaves that row unlinked. The same rule keeps row filtering correct: the viewer filters whole titles, paths, and skill lists, then cuts only the rows it renders. `bounds.SESSIONS` is the most rows that fit rather than merely a number that does: the suite holds it from below as well, so a row that grew has to move it instead of eating the slack.

A session header does not have a reader-controlled size, so its query cuts every string, skill list and PR list. The description and friction line beside them come from the enrichment query and are cut at its own wider width. `tests/view/budgets.py` measures these fixed costs, `tests/view/test_bounds.py` weighs every route the viewer exposes against its own ceiling, and `tests/view/test_bounds__node.py` sweeps the node pages twice — once at the defaults, where the NavTree holds a row of every kind there is, and once at the knobs that make every link on the page longest.
