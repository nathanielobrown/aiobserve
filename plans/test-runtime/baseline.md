# Phase 0 baseline

Every number the [design](design.md) brackets as **[Py]** is superseded by this file.

Measured 2026-09-02 on the 18-core M5 Max, idle, warm caches, in a worktree at
`main` = `381909c`. The design baselined at `bc2aa17`; four commits landed since, including the
`views/` reorganization that moved `test_walk.py` and `test_node.py` under
`tests/view/pages/node/`.

## Headline

| Measurement | Value | How |
| --- | --- | --- |
| Serial wall, `uv run pytest -q` | **226.28s** | median of 3: 226.45 / 226.18 / 226.28 |
| Σ test-seconds | 225.4s | `--junitxml`, summed over 2,225 cases |
| Collected ids | 2,225 | `pytest --collect-only -q` |
| Collection time | 0.36s | warm; 2.73s cold — a non-lever either way |
| Harness startup | 0.52s | `pytest tests/test_scaffolding.py -q`, 3 passed |
| Result | 2,174 passed, 51 skipped | skips are env-gated; the number to hold constant |

Σ test-seconds is 99.6% of wall: the run is test bodies, not harness. The 51 skips match the
design's count.

**Id count.** The goal was stated over 1,976 ids and the design counted 2,187; 2,225 collect
now. The deltas are tests landed since each of those baselines, not measurement drift — the
suite is green at every one of the three counts.

## Stragglers

Top 15 by `call` duration, from the median run's `--junitxml`:

| Seconds | Test |
| --- | --- |
| 37.88 | `view/test_bounds__node.py::test_a_node_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets` |
| 12.17 | `view/test_enrichment.py::test_a_store_whose_enrichment_tables_are_empty_renders_every_page` |
| 11.16 | `view/pages/node/test_walk.py::test_every_control_in_the_corpus_walks_its_own_level_or_climbs_out_of_it` |
| 10.76 | `view/test_app__list.py::test_a_column_the_store_left_null_reads_as_one_dash` |
| 10.73 | `view/test_enrichment.py::test_a_partly_described_store_shows_the_items_it_reached_and_nothing_for_the_rest` |
| 10.64 | `view/pages/node/test_node.py::test_every_kind_renders_a_body_and_every_shape_a_log` |
| 10.64 | `view/test_enrichment.py::test_a_store_no_enrichment_pass_has_touched_renders_every_page` |
| 5.17 | `view/test_dev.py::…becomes_one_message_on_the_stream[style.css-css]` |
| 5.14 | `view/test_dev.py::…becomes_one_message_on_the_stream[dev-reload.js-page]` |
| 5.02 | `view/test_dev.py::test_the_reload_stream_answers_as_an_event_stream_under_the_same_policy` |
| 4.61 | `view/pages/node/test_nav_tree__badges.py::test_every_priced_row_carries_the_spend_the_store_holds_under_it` |
| 2.91 | `view/pages/node/test_nav_tree__names.py::test_every_row_is_named_from_the_column_its_kind_is_named_by` |
| 1.88 | `view/test_dev.py::test_an_open_stream_does_not_hold_the_server_open_when_it_is_interrupted` |
| 1.76 | `view/pages/node/test_nav_tree__badges.py::test_a_row_badges_its_cost_only_where_it_has_a_share_to_draw` |
| 1.72 | `view/pages/node/test_nav_tree__presets.py::…is_its_own_cell_or_the_full_one_that_holds_the_path[noapi]` |

The top 15 are 138s of the 225s; the remaining 2,210 ids share 87s. Everything above 10s is a
viewer sweep over the corpus, which is what Phases 2 and 3 address. The three `test_dev.py`
leaves are sleeps and a watcher's debounce, not compute — parallelism hides them and nothing
else will.

The straggler runs 37.9s serial against the design's 41.0s probe at `bc2aa17` — close enough to
treat as the same test, and still far above the 30s gate on its own.
