# The trace viewer

`aiobserve view` serves the trace store in a browser so you can look at a session instead of querying it. It binds `127.0.0.1` only, opens the store read-only, and ships no asset it did not vendor. Flags: `aiobserve view --help`; the design behind it is [the trace-viewer design](../plans/trace-viewer/design.md).

## What it shows

The list at `/` holds one row per session — when it started, its title and project, the rollup counts, cost, tokens, and time. Every column heading sorts by that column; clicking it again reverses. A cost with a `*` beside it had calls at a model our price table lacks, so the total is a floor. What a transcript wrote reaches a row cut to a head: the title and the project path to 100 characters, the skills to the first four names with a count of what was left. The session's own page has them whole.

The form above the list narrows it: by project, by a date range, by a skill the session ran, or by a floor on failed tool calls. A filter rides the sort headings and the pager, so re-ordering or turning the page keeps it; the masthead link clears everything. The footer's citation names each filter after the paging, so the line reproduces the rows that were on the screen.

A session page at `/session/{session_id}` opens with the header the `sessions` row carries, then the main-source turn timeline in order, a page of turns at a time. Each turn shows its prompt or command, its counts, and its cost. A run spawned from a turn appears as a chip on that turn, with any run it spawned nested under it. Two rows exist so the page's numbers match the header's: an unattributed row for the calls that sit under no turn, and an unattached section for the runs that resolve to no turn and to no run of the session either. Every list on the page is capped — the turns, the runs under each of them, the compaction markers, and the header's own skills and PR links — and each one says how much it left behind, with a link to the page that holds it where a link can reach it.

Opening a turn fetches its api calls a page at a time: per call the model, the tokens, the cost, a preview of what it wrote, and a row per tool call it made. Everything on that fragment is a preview — the full text, the thinking, and one tool's arguments and result each load on their own, one value per fetch, when you open them.

A run chip links to `/session/{session_id}/run/{run_id}`, the same page for one agent run: its header, the thread above it as a trail of links, its own turn timeline, and the runs under it that no turn of its timeline claims. The trail stops where the store stops naming parents — a fork's spawning call lives in files this store may not hold, and a guess in a breadcrumb is a wrong citation.

Every timeline links to its thread's raw transcript at `/session/{session_id}/records/{source}`: one row per archived line, with the line number, the record type, its length, and the head of the line itself. Opening a row fetches that line whole. Each turn also links to the line that carries it, so a rendered turn and the record behind it reach each other in one click.

A tool result Claude Code wrote to a file instead of the transcript links to `/session/{session_id}/offload/{name}`. Those files run to tens of megabytes, so the page serves the content in chunks and hands back the offset of the next one. The name is a key into `offload_files`, never a path the server opens.

## URLs are the citation surface

Every page is a plain GET you can paste into a report or a message. The list takes `sort`, `direction`, `page`, `size`, and the filter keys; a session or a run takes its ids. An unknown key, an unknown sort or direction, a filter value of the wrong type, or a page outside its bounds answers 400 rather than guessing. Sort keys are column names and filter keys are fixed predicates, so a citation says what produced the rows and the order — and no request text reaches SQL as anything but a bound value.

Session pages are keyed by natural ids, so a report that cites `(session_id, source, line_no)` keeps citing the tuple. The URL is derived from it, and a port or route change breaks nothing already written down. That tuple has a page of its own: `/session/{session_id}/records/{source}?after={line_no - 1}#L{line_no}` opens the records browser on the cited line.

A timeline cursor reads the same way and citing one turn takes the same shape: a session page takes `after`, `turns`, and `chips`, where `after` is *the last turn index already shown*, so `/session/{session_id}?after={turn_index - 1}#turn-{turn_id}` opens that turn first on the page. Paging is forward only and keyed on the turn index rather than on a row count, so a link keeps opening the same turns however much the session grew after it was written down.

## Reading while an extract runs

The viewer holds no connection between requests, so `aiobserve extract` can take the write lock whenever the viewer is idle. The collision goes both ways and neither side retries:

- An extract that starts while a page is mid-request fails with DuckDB's lock error. Reload the page and run it again
- A page loaded while an extract holds the lock answers 503 saying the store is being written. It serves again as soon as the writer lets go
- A re-extract that bumps the schema under a running viewer answers 503 naming the version this build reads. Restart the viewer

Launching against a store this build cannot read, a store that is not there, or a port already serving fails at startup instead of opening a browser onto an error page.

## What keeps a page small

A viewer that renders a whole transcript is a viewer that hangs, so no query behind a page selects a column that holds what the agent read or wrote — `raw`, `text`, `thinking`, `result`, `input`, `content` — without truncating it in SQL. A per-value fetch is the declared exception: one tool's result or one transcript line, one value to a request, so it tops out at the largest value in the store rather than at a page of them. Rendering keeps that promise: JSON is re-indented only while indenting stays cheap, because indenting is quadratic in nesting and 10 KB of nothing but `[` would otherwise serve 50 MB. A value nested past what anyone reads is served as it was stored.

Every page size is a bound parameter too: a default in the query manifest, a ceiling in the app, and a 400 for a hand-typed `?size=` past it. The ceiling is the number the payload bound is computed from, because a size is something a reader types. A row's own markup costs what it was measured to cost against the canonical store — the fixtures are redacted down to a few characters and project nothing — while every character of transcript content the row carries counts as five bytes, which is what `&` escapes to. The turn fragment's two sizes multiply, so its ten calls of twelve tool rows spend the budget and `?calls=` only goes down from the default.

The session timeline is paged the same way, in two sizes that multiply: twenty turns to a page, eight run chips to a turn. The unattached list is a list of chips as well and rides every page, so the route refuses a pair whose `(turns + 1) × chips` passes 200 run rows — the budget the ceiling affords, and most of what it buys. A list the cap cut says how many it left and links to the page holding them all, `?turns=1&chips=100`, which fits the widest forest the corpus records — 94 runs under one turn — on a page of its own. That page weighed 33.6 KB against `data/traces.duckdb` on 2026-08-07, and the largest page any legal pair of sizes can ask for projects to 348 KB at the worst character: 324 KB of turn and run rows, 12 KB of compaction markers, and 12 KB for everything else the page carries.

The list is the other page a corpus grows, and it is bound the same way: 125 sessions to a page, the default and the maximum both, each row's strings cut to 100 characters and its skills to four names of 20. The cut is composed around the query rather than made in it, because the filters match whole values — a project path cut to its head would match nothing, and a skill outside the first few would disappear from a filter that finds it. The filter box is bound too, at the 10 busiest projects, each path offered whole or left out for the same reason. That gives 10,000 B of chrome plus 125 rows of 2,400 B — 1,000 B of measured markup and 1,400 B of heads at the worst character — or 310 KB against the 350 KB ceiling.

That last 12 KB of a session page is the header, which is the one part of it no size a reader types bounds — a session's PR links grow with every PR it opens. So the query cuts what the header shows: each string to a head, each of its two lists to its first members with a count of what it left. The compaction markers are capped the same way and for the same reason, at 20 to a page against the 18 the densest recorded thread holds. `tests/view/test_bounds.py` holds the ceiling and everything that has to fit under it, and re-measures the header's own weight on every run.
