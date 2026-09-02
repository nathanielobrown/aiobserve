# Closing results

What the branch measured against [the Phase 0 baseline](baseline.md). Same machine, same
method: 18-core M5 Max, idle, warm caches; medians of 3. The serial numbers were taken at
`6a8802c`, before the scheduler was retuned, which nothing serial depends on.

## Headline

| Measurement | Baseline | Now | How |
| --- | --- | --- | --- |
| Gate: `mise run test` | — | **25.02s** wall, 248.7s Σ | median of 3 at the retuned `-n 12 --dist loadgroup` |
| Serial wall, `uv run pytest` | 226.28s | **145.44s** | median of 3: 143.12 / 145.44 / 146.00 |
| Σ test-seconds, serial | 225.4s | **145.0s** | `--junitxml`, summed over 2,228 cases |
| Result | 2,174 passed, 51 skipped | 2,178 passed, **51 skipped** | the skip count is what had to hold |

The serial number is the control the parallel one cannot give: it says the suite has 80s less
work in it, not that 80s moved behind a worker. Σ test-seconds is 99.3% of the serial wall, so
the run is still test bodies rather than harness.

**Read a Σ only against another Σ at the same width.** Twelve workers contending turn the same
145s of serial work into 249 test-seconds, because each leaf's own clock runs while eleven
others hold the machine. The number compares two schedulers on one machine; it does not
compare a parallel run to a serial one.

Four ids more than the baseline collected: Phase 2 split one test into three, and the
`gen_schema` fix gave its refusal leaf a second case.

## Which phases the 81 serial seconds came from

Two of the four phases remove work; the other two move it:

- **Phase 1**, the thread pin, and **Phase 3**, the shared render pass, are the only levers that
  cut serial time. Their split was not measured — doing so needs a serial run per commit, at
  ~145s each, and nothing downstream turns on the answer
- **Phase 2**'s split and **Phase 5a**'s reorder cost serial time or leave it alone; they buy the
  parallel wall by spreading the same work over more workers

## Stragglers now

Top 6 by junit `time`, serial:

| Seconds | Test |
| --- | --- |
| 9.93 | `view/test_bounds__node.py::test_a_node_page_at_the_sizes_a_reader_gets_…` |
| 9.14 | `view/test_bounds__node.py::test_a_node_page_at_the_widest_knobs_…` |
| 8.41 | `view/pages/node/test_node.py::test_every_kind_renders_a_body_and_every_shape_a_log` |
| 8.05 | `view/test_enrichment.py::test_a_store_whose_enrichment_tables_are_empty_…` |
| 7.61 | `view/test_bounds__node.py::test_a_second_page_of_a_level_…` |
| 7.14 | `view/test_enrichment.py::test_a_partly_described_store_…` |

The baseline's 37.9s straggler is gone; nothing is above 10s. The `test_node` figure carries the
shared render pass, which builds inside whichever consumer runs first. The two `test_enrichment`
leaves are the enrichment-absence stores the design ruled must stay separate — each proves a
different guard, so they are cost the suite is meant to pay.

The shared group's setup is the longest single item, and the wall is that plus the tail behind
it: 19.6-21.8s at eighteen workers, 14.1-14.5s at twelve.

## Which scheduler, and how many workers

Every cell is one whole-suite run under `--junitxml`, on the idle 18-core machine:

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

A keyed read of `corpus_rollups` costs what a scan does either way: the replay exclusion is a
window over the whole family, so no filter on one session reaches it.

**The float drift the risk register predicted is real and bounded.** Summing a session's
`cost_usd` in one grouped pass instead of one subquery per session moves the last bits: over the
627 sessions, `session_rollups` is identical and `corpus_rollups` differs on 109 rows, worst
absolute 4.1e-12 and worst relative 3.6e-15. Every row still rounds equal at 4dp, which is the
tightest any consumer reads — `money()` prints 2dp, `cost_distribution.sql` rounds to 4.

## One thing to know before you measure

The baseline table names `uv run pytest -q` as its serial command, and the serial numbers here
were taken with a bare `uv run pytest` instead. `gen_schema.main()` used to read the runner's own
`sys.argv`, so `-q` reached the tool as the table to print and reddened
`tests/tools/test_gen_schema.py`. Fixed on this branch — `main` takes its arguments now — so
either spelling measures the same thing from here on.
