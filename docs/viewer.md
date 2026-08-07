# The trace viewer

`aiobserve view` serves the trace store in a browser so you can look at a session instead of querying it. It binds `127.0.0.1` only, opens the store read-only, and ships no asset it did not vendor. Flags: `aiobserve view --help`; the design behind it is [the trace-viewer design](../plans/trace-viewer/design.md).

## What it shows

The list at `/` holds one row per session — when it started, its title and project, the rollup counts, cost, tokens, and time. Every column heading sorts by that column; clicking it again reverses. A cost with a `*` beside it had calls at a model our price table lacks, so the total is a floor.

The form above the list narrows it: by project, by a date range, by a skill the session ran, or by a floor on failed tool calls. A filter rides the sort headings and the pager, so re-ordering or turning the page keeps it; the masthead link clears everything. The footer's citation names each filter after the paging, so the line reproduces the rows that were on the screen.

A session page at `/session/{session_id}` opens with the header the `sessions` row carries, then the main-source turn timeline in order. Each turn shows its prompt or command, its counts, and its cost. A run spawned from a turn appears as a chip on that turn, with any run it spawned nested under it. Two rows exist so the page's numbers match the header's: an unattributed row for the calls that sit under no turn, and an unattached section for the runs that resolve to no turn.

Opening a turn fetches its api calls a page at a time: per call the model, the tokens, the cost, a preview of what it wrote, and a row per tool call it made. Everything on that fragment is a preview — the full text, the thinking, and one tool's arguments and result each load on their own, one value per fetch, when you open them.

A run chip links to `/session/{session_id}/run/{run_id}`, the same page for one agent run: its header, the thread above it as a trail of links, its own turn timeline, and the runs under it that no turn of its timeline claims. The trail stops where the store stops naming parents — a fork's spawning call lives in files this store may not hold, and a guess in a breadcrumb is a wrong citation.

## URLs are the citation surface

Every page is a plain GET you can paste into a report or a message. The list takes `sort`, `direction`, `page`, `size`, and the filter keys; a session or a run takes its ids. An unknown key, an unknown sort or direction, a filter value of the wrong type, or a page outside its bounds answers 400 rather than guessing. Sort keys are column names and filter keys are fixed predicates, so a citation says what produced the rows and the order — and no request text reaches SQL as anything but a bound value.

Session pages are keyed by natural ids, so a report that cites `(session_id, source, line_no)` keeps citing the tuple. The URL is derived from it, and a port or route change breaks nothing already written down.

## Reading while an extract runs

The viewer holds no connection between requests, so `aiobserve extract` can take the write lock whenever the viewer is idle. The collision goes both ways and neither side retries:

- An extract that starts while a page is mid-request fails with DuckDB's lock error. Reload the page and run it again
- A page loaded while an extract holds the lock answers 503 saying the store is being written. It serves again as soon as the writer lets go
- A re-extract that bumps the schema under a running viewer answers 503 naming the version this build reads. Restart the viewer

Launching against a store this build cannot read, a store that is not there, or a port already serving fails at startup instead of opening a browser onto an error page.

## What keeps a page small

A viewer that renders a whole transcript is a viewer that hangs, so no query behind a page selects a column that holds what the agent read or wrote — `raw`, `text`, `thinking`, `result`, `input`, `content` — without truncating it in SQL. The list is the page a growing corpus stretches, so it is paged: `PAGE_SESSIONS` rows by default, `MAX_PAGE_SESSIONS` at most, both in `src/aiobserve/view/listing.py`. `tests/view/test_bounds.py` holds the ceiling and the projection that has to fit under it.
