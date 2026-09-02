# Closing results

What the branch measured against [the Phase 0 baseline](baseline.md). Same machine, same
method: 18-core M5 Max, idle, warm caches; medians of 3.

## Headline

| Measurement | Baseline | Now | How |
| --- | --- | --- | --- |
| Gate: `mise run test` | — | **27.63s** | median of 3: 26.56 / 27.63 / 28.78, at `6a8802c` |
| Serial wall, `uv run pytest` | 226.28s | **145.44s** | median of 3: 143.12 / 145.44 / 146.00 |
| Σ test-seconds | 225.4s | **145.0s** | `--junitxml`, summed over 2,228 cases |
| Result | 2,174 passed, 51 skipped | 2,177 passed, **51 skipped** | the skip count is what had to hold |

The serial number is the control the parallel one cannot give: it says the suite has 80s less
work in it, not that 80s moved behind a worker. Σ test-seconds is 99.3% of the serial wall, so
the run is still test bodies rather than harness.

Three ids more than the baseline collected — Phase 2 split one test into three.

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

Under `-n auto` the shared group's setup, 19.6-21.8s, is the longest single item, and the wall
is that plus the tail behind it.

## Why `-n auto`

A/B of the whole suite on 18 cores: 8 → 34.63s, 12 → 27.25 / 27.27s, `auto` → 27.63s,
24 → 30.59s. A hand-picked 12 wins by less than the run-to-run spread and would cost every
machine with a different core count, CI included. The reasoning lives beside the flag in
`mise.toml`.

## One thing to know before you measure

`tests/tools/test_gen_schema.py::test_main_refuses_to_guess_which_table` calls `main()` against
the runner's real `sys.argv`, and that `main()` refuses on `len(sys.argv) != 2`. So pytest reds
it whenever the command line carries exactly one argument — `uv run pytest -q`, or
`uv run pytest <one path>`. Pre-existing and out of this branch's scope — the branch touches nothing
under `tests/tools/`. It is the reason the serial numbers above were taken with a bare
`uv run pytest` and the Σ run with `--junitxml <path>` as two tokens, where the baseline's
table names `uv run pytest -q`. The two commands differ by one flag and by that one test's
verdict, not by anything either measures.
