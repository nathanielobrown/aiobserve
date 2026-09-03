# Closing results

What the branch measured against [the Phase 0 baseline](baseline.md). Same machine, same
method: 18-core M5 Max, idle, warm caches; medians of 3.

## Headline

| Measurement | Baseline | After Phase 5a | After Phase 4 | Now | How |
| --- | --- | --- | --- | --- | --- |
| Gate: `mise run test` | — | 25.02s, 248.7s Σ | 20.62s, 201.3s Σ | **19.11s**, 190.1s Σ | median of 3 at `-n 12 --dist loadgroup` |
| Serial wall, `uv run pytest` | 226.28s | 145.44s | 109.59s | **96.63s** | median of 3: 96.49 / 96.63 / 96.69 |
| Σ test-seconds, serial | 225.4s | 145.0s | 108.7s | **95.8s** | `--junitxml`, summed over 2,237 cases |
| Result | 2,174 passed, 51 skipped | 2,178 passed | 2,185 passed | 2,186 passed, **51 skipped** | the skip count is what had to hold |

**On CI the `mise run check` step fell from 221s to 173s to about 155s** across the branch's
pushes, read off the GitHub job timings for `4d2e18b`, `b24d6ca`, `b2ec5c2` (153s) and `b96bf22`
(158s). Those are single samples on a shared runner with a handful of cores, so read the
direction and not the digits: the last two are one sample each and say nothing to each other.
What they do say is that the cuts land where there is less parallelism to hide them, which is
the machine that matters.

The serial number is the control the parallel one cannot give: it says the suite has 130s less
work in it, not that 130s moved behind a worker. Σ test-seconds is 99.1% of the serial wall, so
the run is still test bodies rather than harness.

**Read a Σ only against another Σ at the same width.** Twelve workers contending turn 96s of
serial work into 190 test-seconds, because each leaf's own clock runs while eleven others hold
the machine. The number compares two schedulers on one machine; it does not compare a parallel
run to a serial one — and the gap is contention, not rebuilt fixtures, which the last section
prices.

Twelve ids more than the baseline collected: Phase 2 split one test into three, the
`gen_schema` fix gave its refusal leaf a second case, Phase 4 added a guard per rewrite, and
the watcher fix below added a leaf holding its own bound. A thirteenth arrived after the
medians above were taken — the memo's walk leaf gained a second depth to close the one mutant
the audit found alive — so the suite collects 2,187 today at the same measured cost.

## Which phases the 130 serial seconds came from

Four steps remove work; two move it:

- **Phase 1**, the thread pin, and **Phase 3**, the shared render pass, are the two setup levers
  that cut serial time. Their split was not measured — doing so needs a serial run per commit,
  and nothing downstream turns on the answer
- **Phase 4** cut 36s of it on its own, measured as one block: 145.44s before its first commit
  and 109.59s after its last. It is the only phase that deletes work a page does rather than
  work a fixture does, so the viewer gets the same cut (below)
- **The watcher fix** cut 13s: 109.59s before it and 96.63s after, all of it wall clock three
  leaves were waiting out rather than work anything did
- **Phase 2**'s split and **Phase 5a**'s reorder cost serial time or leave it alone; they buy the
  parallel wall by spreading the same work over more workers

## Stragglers now

Top 6 by junit `time`, serial:

| Seconds | Test |
| --- | --- |
| 6.83 | `view/test_bounds__node.py::test_a_node_page_at_the_sizes_a_reader_gets_…` |
| 6.33 | `view/test_bounds__node.py::test_a_node_page_at_the_widest_knobs_…` |
| 5.13 | `view/pages/node/test_node.py::test_every_kind_renders_a_body_and_every_shape_a_log` |
| 4.84 | `view/test_bounds__node.py::test_a_second_page_of_a_level_…` |
| 4.78 | `view/test_enrichment.py::test_a_store_whose_enrichment_tables_are_empty_…` |
| 3.89 | `view/test_enrichment.py::test_a_partly_described_store_…` |

The baseline's 37.9s straggler is gone, the three `test_dev` leaves have left the table, and
nothing is above 7s. The `test_node` figure carries the shared render pass, which builds inside
whichever consumer runs first. Everything else here is a whole-corpus page sweep, which is the
subject of the last section.

The shared group's setup is the longest single item, and the wall is that plus the tail behind
it.

## Which scheduler, and how many workers

Every cell is one whole-suite run under `--junitxml`, on the idle 18-core machine. These were
taken before Phase 4, so read them against each other rather than against the headline: what
they settle is which scheduler and how many workers, and Phase 4 takes work out of every cell
alike.

| `--dist` | `-n` | Wall | Σ test-seconds |
| --- | --- | --- | --- |
| loadgroup | 6 | 38.31s | 191.0s |
| loadgroup | 8 | 35.38s | 215.9s |
| loadgroup | 12 | **25.02s** | 250.8s |
| loadgroup | 18 (`auto`) | 27.94s | 332.8s |
| worksteal | 6 | 44.43s | 185.6s |
| worksteal | 8 | 38.32s | 202.6s |
| worksteal | 12 | 34.76s | 245.1s |
| worksteal | 18 (`auto`) | 34.59s | 307.9s |

The two finalists at medians of 3 — loadgroup at 12: **25.02s** (25.02 / 24.71 / 26.72) and
**248.7s** Σ (250.8 / 242.0 / 248.7); loadgroup at `auto`: 27.94s (27.94 / 28.12 / 27.87) and
332.8s Σ (332.8 / 334.4 / 330.7). Twelve wins both, by more than the spread of either.

**worksteal loses at every width, by 8-10s of wall.** It does not honour `xdist_group`, and a
verbose run at twelve shows both halves of the cost: the four `corpus_sweep` leaves ran on three
workers, so the render pass they share was built three times, and all four reported at 98-99% of
the run rather than the 69-72% loadgroup puts them at. A run that meets its longest work at the
end finishes on one worker alone — the busiest spent 31.0s of a 33.8s wall while the idlest sat
out 20.1s of it. Stealing balances a tail; it cannot balance a tail it created.

**Past twelve workers the machine gives nothing back.** Σ climbs the whole way up — 191, 216,
251, 333 — while the wall bottoms out at twelve and rises again. So the task asks for twelve, or
for every core on a machine that has fewer, which is what CI runs.

## Phase 4: the queries under the suite

Each fix is A/B'd on the real store — 627 sessions, 16 GB — with the arms interleaved
`old, new, old, new` so drift in the machine shows up as disagreement between an arm's two
medians. DuckDB pinned to 4 threads, medians of 7 reads, caches warm.

| Fix | Subject | Before | After |
| --- | --- | --- | --- |
| `context_window()` CASE → MAP | `view_compactions.sql`, the thread with 22 compactions | 11.5 / 11.1 ms | 2.6 / 2.6 ms |
| Grouped-join rollups | `SELECT * FROM session_rollups` | 18.0 / 17.9 ms | 5.4 / 5.4 ms |
| Grouped-join rollups | `SELECT * FROM corpus_rollups` | 84.5 / 84.7 ms | 29.0 / 28.9 ms |
| Grouped-join rollups | one session out of `session_rollups` | 5.6 / 5.7 ms | 1.3 / 1.2 ms |
| Grouped-join rollups | one session out of `corpus_rollups` | 76.9 / 77.4 ms | 25.4 / 25.4 ms |
| Grouped-join `view_runs.sql` | the session with 240 agent runs | 10.7 / 10.7 ms | 6.3 / 6.6 ms |

The third fix is measured differently, because what it removes is a whole page's second read
rather than one statement: the same three URLs served through the app, medians of 7 requests,
the arms interleaved the same way. The old arm is the memo bypassed rather than the old code
restored, so the two arms differ by the memo and by nothing else.

| Fix | Subject | Before | After |
| --- | --- | --- | --- |
| Per-request level memo | a tool call five levels down | 144.6 / 144.8 ms | 118.7 / 119.5 ms |
| Per-request level memo | a turn of `main` | 100.5 / 100.1 ms | 80.5 / 81.1 ms |
| Per-request level memo | the session page | 53.2 / 53.2 ms | 53.5 / 53.5 ms |

The session page is the control: nothing stands beside a session, so the walk reads no level
and there is nothing to answer twice. On the two pages that do walk a level, the whole request
drops a fifth — the levels the NavTree opened were a quarter of the page's query time, and the
walk was running every one of them again.

A keyed read of `corpus_rollups` costs what a scan does either way: the replay exclusion is a
window over the whole family, so no filter on one session reaches it.

`view_runs.sql` answers identically for all 126 sessions of the store that ran an agent run.
That corpus is what proves the empty cases: it holds a run with no api call of its own, one
with no non-synthetic call, six with no tool call and 2,276 with no compaction.

**The fourth fix, lazy JSON macro install, was measured and dropped.** Installing the whole
macro library on a fresh connection costs 0.744 ms, of which the three JSON macros are 0.287 ms
(`tool_asked`, `tool_path`, `tool_fields`, medians of 50 installs on an in-memory database). A
serial suite run opens 2,815 of them and spends 1.77 s installing macros, so skipping the JSON
three on every connection that never calls one would be worth at most 0.8 s of 110 s — 0.6% —
and less than that on a page, where 0.29 ms sits inside 53-145 ms.

What it would cost is the invariant `DEFINITIONS` is written around: a connection holds the
whole library or a query that reaches for a macro fails on a page nobody rendered under test.
`install` runs before any query is known, so laziness means either a check on every statement or
a tier per consumer. That is a fail-fast property traded for half a percent, so the fix is not
landed.

**The float drift the risk register predicted is real and bounded.** Summing a session's
`cost_usd` in one grouped pass instead of one subquery per session moves the last bits: over the
627 sessions, `session_rollups` is identical and `corpus_rollups` differs on 109 rows, worst
absolute 4.1e-12 and worst relative 3.6e-15. Every row still rounds equal at 4dp, which is the
tightest any consumer reads — `money()` prints 2dp, `cost_distribution.sql` rounds to 4.

## Where the last 109 seconds went

Profiled rather than guessed: a serial run under `--durations=0 --durations-min=0.05`, and
`cProfile` over the three leaves that lead it. Shares are of the 108.7s Σ that stood before the
watcher fix.

| Share | Cause | Where it shows |
| --- | --- | --- |
| 16.7% | Three streaming leaves waiting out `awatch`'s five-second window, with no query in them | `view/test_dev.py` — **fixed below** |
| 16.6% | Three sweeps of every node page over the escaped plant, at three knob sets | `view/test_bounds__node.py` |
| 8.8% | Two sweeps of every node page over the enrichment-absence stores | `view/test_enrichment.py` |
| ~5.9% | Opening a connection per request: 2,815 opens at 2.26 ms | spread over every page a test renders |
| 6.5% | The shared render pass, built inside whichever consumer runs first | `view/pages/node/test_node.py` setup |
| 4.2% | Three leaves waiting out a real DuckDB holder in a real subprocess | `view/test_lifecycle.py` |
| 3.9% | One sweep per preset | `view/pages/node/test_nav_tree__presets.py` |
| 2.6% | An extract waiting out a holder, and the pipeline's own leaves | `tests/test_pipeline.py` |
| 2.6% | Two planted sweeps of the corpus for cost badges | `view/pages/node/test_nav_tree__badges.py` |
| 2.0% | Reading the view package's own source, once per rule | `view/test_layout.py` |

Two facts frame the rest. **Fixture setup is 7.4s of the run** — the `--durations` footer splits
into 86.5s of call against 7.4s of setup — so the session scoping Phases 1 and 3 put in has
already taken that lever. And the top of the profile inside a sweep is DuckDB executing
statements, not the harness around it: in a whole-corpus sweep `store.fetch` is the largest
single line by internal time.

### The one fix worth landing

`_events` asks `awatch` to surface twice a second rather than every five (`src/hyphae/view/dev.py`).
Cancelling the response never reaches the thread `awatch` blocks in — `anyio.to_thread.run_sync`
waits for it either way, and `awatch` sets its stop event only after the thread has already
returned — so a reader who closes the page holds a worker for whatever is left of that window.
The suite's three streaming leaves each paid the full five seconds.

| Subject | Before | After |
| --- | --- | --- |
| `…a_file_saved_under_a_watched_path…[style.css-css]` | 5.18 / 5.16 s | 0.63 / 0.63 s |
| `…a_file_saved_under_a_watched_path…[dev-reload.js-page]` | 5.14 / 5.14 s | 0.62 / 0.63 s |
| `…the_reload_stream_answers_as_an_event_stream…` | 5.04 / 5.02 s | 0.51 / 0.53 s |

The debounce that decides when a save is reported is untouched, and a watcher that surfaces with
nothing to report goes straight back down: driving the real watcher over a real directory yields
the first change in 0.12s at either setting. `test_dropping_the_stream_lets_go_of_the_watcher_…`
holds the bound from now on — it fails at 5.02s against the old value.

### Three candidates measured and dropped

**`ASGITransport` instead of `TestClient`** (design open question 2). The portal machinery —
`testclient`, `anyio.from_thread`, the backend, the thread and future plumbing — is **0.040s of
a 7.64s** whole-corpus sweep, 0.5%. There is nothing there to win.

**A shared read-only corpus artifact across workers.** Building all three session-scoped stores
costs **1.04s per worker**: `corpus_db` 0.51s, `exportable_db` 0.47s, `enriched_db` 0.06s. At
twelve workers that is 12.5s of Σ and about a second of wall, against a fixture contract every
tier in the suite reads. The gate's 190s Σ against the serial 95.8s is contention between twelve
workers on eighteen cores, not fixtures rebuilt eleven times.

**Merging the three enrichment-absence stores** is out of bounds: each is the absence a different
page has to render, and one store cannot be three.

### Ranked follow-ups, none built

1. **The per-request store open — 6.4s ceiling, and the same cut on every page load.** Each
   request opens a connection, checks the schema version, rebuilds fourteen temp views and
   installs the macro library: 2.26 ms, of which `refresh_views` is 1.37 ms and `macros.install`
   0.58 ms. Two written contracts stand in the way, and both are deliberate — `view/deps.py`
   opens per request so `hp extract` can write between two page loads, and `refresh_views`
   promises a reader this code's view text rather than whatever a writer last stored. Caching
   either is a design conversation, not a sweep
2. **The escaped plant's markdown cost — about 5s, and only in tests.** In the widest-knobs
   sweep, `markdown_it` is 5.5s of the 6.1s spent rendering: the plant is 4,001 `&` at every cap,
   so the inline parser's entity rule fires 1.6M times. It does not appear at all in an
   unplanted sweep, so the win is the suite's and the risk — a fast path through the viewer's one
   escaping seam — is the product's. Wrong trade at this size
3. **The lock waits in `test_lifecycle` and `test_pipeline`, about 4s.** A real subprocess holds
   a real DuckDB lock and the leaf waits it out. The waiting is the thing under test
4. **The planted halves of `test_nav_tree__badges` and `__names`, 4.1s between them**, which the
   design already deferred

## One thing to know before you measure

The baseline table names `uv run pytest -q` as its serial command, and the serial numbers here
were taken with a bare `uv run pytest` instead. `gen_schema.main()` used to read the runner's own
`sys.argv`, so `-q` reached the tool as the table to print and reddened
`tests/tools/test_gen_schema.py`. Fixed on this branch — `main` takes its arguments now — so
either spelling measures the same thing from here on.
