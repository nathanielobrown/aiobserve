# Design: Python test suite under 30 seconds

**Goal, from the owner:** "I want total test runtime under 30 seconds on the Python version."

**Gate:** the pytest tier as `mise run check` runs it (the `test` task in `mise.toml`) finishes
in under 30s wall, median of 3 runs on an otherwise idle 18-core M5 Max. The suite stays green,
`mise run check` stays the gate, and no test drops a property it asserts today.

**As built,** Phases 0–3 landed, together with Phase 5's collection reorder; Phase 4 and Phase
5's connection reuse did not. The numbers this design was written against are superseded by
[the Phase 0 baseline](baseline.md); what the finished suite measures is in
[the closing results](results.md). Later phases carry their own as-built notes where what
shipped differs from what is described.

Provenance labels used throughout:

- **[Py]** — measured on this Python codebase, `main` at `bc2aa17`, 2026-09-01, 18-core M5 Max,
  idle. Re-verify in Phase 0; the machine and HEAD will have moved
- **[Rust]** — measured on the Rust port of this codebase (worktree branch `rust-prototype`,
  HEAD `a2432ab`). The Rust tests are ports of these and the viewer SQL is shared, so findings
  usually translate — but every Rust number is a hypothesis until re-measured on Python, and two
  already failed to translate (see "Where Python differs from the Rust findings")

## Problem

The suite is 2,187 collected ids **[Py]** (an earlier baseline counted 1,976 at an older HEAD;
Phase 0 re-baselines) running single-process in ~199.6s wall. Profiling the same workload
established **[Rust]**: ~89% of suite time is viewer page renders, and >90% of a page render is
DuckDB — engine 41.6%, lock/futex waits 31.6%, allocator 16.8%; host-language work under 1.5%.
Compile, network, and sleeps are negligible (<5s combined). The costs belong to the workload
shape — many page renders, each opening a DuckDB connection that spawns an 18-thread pool for
queries over ~20-row tables — not to Python.

Two design-time probes confirmed the big levers on Python **[Py]**:

- `SET threads TO 1` on every test connection: the straggler test alone went 41.0s → 29.1s,
  its CPU 106s → 30s (the default per-connection thread pool is pure contention at this scale)
- pytest-xdist `-n 18` plus that pin: **the full suite ran green in 47.2s wall** (344.4
  summed test-seconds, 2,136 passed, 51 env-gated skips — same skips as serial), and a second
  independent run reproduced it at 43.6s with the same counts. No fixture isolation, port, or
  store-lock failure appeared in either run

The residual problem is the straggler:
`tests/view/test_bounds__node.py::test_a_node_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets`
took 34.8s inside that parallel run **[Py]**. Wall time can never go below the slowest single
test, so this one test blocks the 30s gate no matter the core count.

## Measurement protocol

Applies to Phase 0 and to the "done" check of every later phase:

- Median of 3 runs on the idle 18-core machine, warm caches
- Report **wall time of `mise run test`** (the gate number) **and** the serial number from
  plain `uv run pytest` (which stays serial: the `-n` flag lives in the mise task, not in
  `addopts`) — the serial number is what detects work creeping back in behind parallelism
- Per-test durations from `--junitxml` (addopts already carries `--durations=10`)
- After each phase: suite green, skip count unchanged (51 env-gated **[Py]**), and the phase's
  own red-check (below) performed once

## Phases

### Phase 0 — Python baseline (half a day)

Re-establish every number on the implementing machine and HEAD before changing anything:

1. `uv run pytest --collect-only -q` time (0.39s **[Py]** — collection/import is a non-lever)
2. Full serial run with `--junitxml`; keep the file. Note the top 15 durations and the sum of
   test-seconds
3. A no-op run (`uv run pytest tests/test_scaffolding.py -q`) to isolate harness startup
4. Reconcile the id count against the 1,976 the goal was stated over — the delta is new tests
   landed since, not a measurement error, but say so with the numbers

**Done:** a short table (serial wall, Σ test-seconds, top stragglers) committed in this plan
directory as `baseline.md`, superseding every bracketed number here.

### Phase 1 — parallelism + DuckDB thread pin (the measured 199.6s → ~47s)

Two changes that were measured together green **[Py]**:

- **Add `pytest-xdist` to the dev dependency group** and change the `test` task in `mise.toml`
  to `uv run pytest -n auto`. Put the flag in the mise task, **not** in `addopts`: a developer
  running one test file keeps the serial harness (no worker spin-up, `-s`/pdb still work), and
  `mise run mutate` — which invokes pytest itself per mutant — is not silently reshaped.
  **As built,** the task asks for twelve workers, or every core on a machine with fewer: `auto`
  spends a third more CPU to finish 3s later here. The matrix is in [results.md](results.md)
- **Pin DuckDB's per-connection thread pool to 1 for the whole test process.** In
  `tests/conftest.py`, at module import, wrap `duckdb.connect` so every connection the harness
  or the app-under-test opens runs `SET threads TO 1` (works on read-only connections;
  verified **[Py]**). One seam covers all three connection sites: the viewer's per-request
  `open_store` (`src/hyphae/view/store.py`), the fixtures' direct `duckdb.connect` calls
  (`tests/view/conftest.py`), and the store builders. Comment it with the measurement:
  the pool costs more than it earns on ~20-row fixture tables

Why this does not touch the standing ruling: the production viewer's per-request connect
(`view/deps.py` + `view/store.py::open_store`) stays exactly as it is. The ruling exists
because a cached read-only DuckDB connection holds the shared file lock and would block
`hp extract` for the viewer's lifetime. Nothing here caches a connection, and the thread pin
lives only in test scaffolding — production `hp view` keeps DuckDB's default pool, whose value
on a multi-GB store is unmeasured.

xdist hazards, addressed against how the fixtures actually work on `main`:

- Session-scoped fixtures (`corpus_db`, `enriched_db`, `client`, `store`, …) become
  per-worker under xdist. Each worker rebuilds them in its own `tmp_path_factory` directory —
  no cross-worker file sharing, so no DuckDB lock contention. Cost is ~0.8s per worker,
  already inside the measured 47.2s **[Py]**
- No test binds a fixed port: servers bind port 0 or run through the in-process `TestClient`.
  Both measured runs surfaced zero collisions **[Py]**; the one bind-then-reuse race is in the
  risk register
- `pytest-timeout` (120s, signal method) worked unchanged under xdist **[Py]**

**Done:** `mise run test` median ≤ ~50s, green ×3, no new flakes.
**Red-check:** temporarily break one view assertion; confirm the failure reports cleanly
through xdist.

### Phase 2 — split the straggler (the gate phase, ~47s → ~25s projected)

`test_a_node_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets` builds one planted
store, then sweeps `pages(store)` three times through a `TestClient` over the plant: (1) at
default knobs, (2) at `WORST_KNOBS`, (3) at `WORST_KNOBS` plus `?log=1&page=2` keeping only
200s. All assertions then run over the union — including three exact-equality pins
(`worst_crumb_bytes`, `NAV_TREE_ROW_BYTES`, `MEASURED_PAGER_BYTES` under
`fits`/`exact_pins()`), which are equalities against the **max over all three sweeps**.

Split it into three test ids, one per sweep, sharing a module-scoped fixture that plants the
store once and yields the `TestClient` (plant cost ~0.8s **[Py]**; under `--dist load` a
worker that rebuilds it pays ~1s, acceptable). The existing `enriched_plant` fixture is
function-scoped over `tmp_path` and cannot back a module fixture — build the module plant
from `planter(enriched_db, …)` over a `tmp_path_factory` directory instead.

The decomposition rule — apply it mechanically, assertion by assertion:

- **Every `==` becomes `<=` in every leaf whose sweep exercises that assertion, plus `==`
  in the one leaf whose sweep achieves the max today.** (Not all assertions appear in all
  three: `MEASURED_PAGER_BYTES` rows exist only in the paged sweep, so a leaf without paged
  URLs has no pager rows to weigh — its `max()` over an empty sequence would crash loud at
  split time, which is the correct failure, not a pin to replicate.) This covers the budget pins (`worst_crumb_bytes`,
  `NAV_TREE_ROW_BYTES`, `MEASURED_PAGER_BYTES`, the chrome `fits`) and the reached-the-cap
  checks (`max(escaped) == queries.NAV_CHARS`, the count pins `PANE_DETAILS` /
  `DEAR_PANE_DETAILS`). Note the current test writes several of these as a bare `==` with no
  separate ceiling to "keep" — the `<=` half is *added* by the split, not moved
- **Every per-row universal replicates into all three leaves unchanged**: `cuts == {1}`
  (every detail preview shows exactly one cut mark), `assert found` per row kind,
  `assert dear and len(dear) < len(previews)`, the bar/badge checks on the widest row, and
  every other assertion quantified over "each row/page" rather than over the union's max.
  Moving one of these to a single leaf would let a regression visible only under another
  sweep's knobs pass — e.g. a detail rendering zero or two cut marks only at `WORST_KNOBS`
- Before splitting, instrument the current test **under `HYPHAE_PIN_EXACT=1`** to record
  which sweep produces each pin's argmax (expected: crumb and NavTree row in the worst-knobs
  sweep — the knobs exist to make links longest; pager only in sweep 3 — but *record it,
  don't assume it*). The flag matters: `fits`/`exact_pins()` in `tests/view/budgets.py` read
  the crumb, pager, and chrome pins as equalities only when it is set; a default run treats
  them as ceilings. If a future change moves an argmax to another sweep, that sweep's `<=`
  plus the pin leaf's now-unmet `==` fail loudly; nothing under-measures silently

**Rejected within this phase:** deriving sweep 3's URL set (the 43-of-172 nodes with a second
page **[Rust]**) from sweep 1's `data-child` counts. It is exact only while the counting sweep
runs at the default `?log=`, breaks silently if sweep 1 is ever narrowed, and — decisive on
Python — it would couple two test ids through shared render output, forcing them onto one
xdist worker and re-serializing the very split this phase exists for. The ~130 requests that
answer 404 cost a fraction of a full render; keep them. Revisit only if the pager leaf is
still the straggler after measurement.

**Done:** no single leaf binds the wall — derive the per-leaf bound from the instrumentation
rather than assuming one; the two full sweeps each carry ~40–45% of the straggler's 34s, so
~14–16s in-parallel per leaf is the expected shape, not a failure. Projected wall after this
phase: 22–28s (inferred by reconstruction, not measured — the heavy tail collects last, so
leaves start ~9–12s in). **A median of 30±3s here is expected, not a failed phase: Phase 3
runs unconditionally, and the gate is re-measured after it.**

**As built,** the median after this phase was 44.87s — outside the projected band and above the
30±3s the paragraph above allows for. The projection is left as written because it is what
justified running Phase 3 unconditionally, which is what closed the gap.

**Red-check (TDD for a test change):** under `HYPHAE_PIN_EXACT=1` — without the flag the
crumb, pager, and chrome pins are ceilings and the check stays green — bump each pinned
budget constant (e.g. `NAV_TREE_ROW_BYTES`) by 1 and confirm exactly the expected leaf goes
red for each of the three; revert.

### Phase 3 — shared corpus render pass (margin: ~-40s serial)

Runs unconditionally after Phase 2 — the gate is re-measured after it, not before. Four tests
each sweep the same corpus store at default knobs through the same session-scoped `client`
and assert different properties over byte-identical responses **[Py durations from the
parallel run]**:

- `pages/node/test_node.py::test_every_kind_renders_a_body_and_every_shape_a_log` (12.1s)
- `pages/node/test_walk.py::test_every_control_in_the_corpus_walks_its_own_level_or_climbs_out_of_it`
  (10.0s — it computes its expectations from each page's own HTML only, so it is
  shared-map-safe)
- `test_enrichment.py::test_a_store_no_enrichment_pass_has_touched_renders_every_page`
  (12.3s — this one runs on the corpus `client`, the store with no enrichment tables)
- `test_app__list.py::test_a_column_the_store_left_null_reads_as_one_dash` (13.9s — its
  `>None<` scan is a full-corpus sweep, a Python straggler the Rust analysis never saw)

Stays out of the shared pass, by name:

- `test_enrichment.py::test_a_store_whose_enrichment_tables_are_empty_renders_every_page`
  (9.9s) and `…::test_a_partly_described_store_shows_the_items_it_reached_and_nothing_for_the_rest`
  (8.4s) — **the three enrichment-absence sweeps run over three different stores by design**
  (no tables at all / tables present but empty / partly described), each proving a different
  guard: a catalog-absent table versus a present-but-empty one versus a half-written pass.
  The byte-identical argument below licenses sharing only *within* one store; do not read it
  as a license to merge these
- `test_nav_tree__badges` / `__names` — skip-as-read logic and planted halves; revisit only
  if margin runs short

Mechanism: in `tests/view/conftest.py`, a builder `render_pages(path) -> dict[str, str]` (URL →
served HTML, every response asserted 200 once) with a session-scoped `corpus_pages(corpus_db)`
over it, consumed by those tests in place of their own render loops. The builder takes a store
path rather than the corpus `client` and `store` so the red-check below can rebuild the map over
a planted copy. Each test keeps its own id and its own failure report — pytest fixtures give us
what the Rust port's merged-leaf design had to fake with collected-failure lists. Under xdist,
mark the consumers
`@pytest.mark.xdist_group("corpus_sweep")` and add `--dist loadgroup` to the test task, so
one worker renders the map once (~10s at threads=1) instead of each consumer's worker paying
it. The extra, non-shared requests those tests make (enrichment fetch-URL 404s, the walk
chain-following leaf, `/sessions` reads) stay as they are.

Safety argument: the responses are byte-identical today — same store file, same knobs, same
app — so sharing changes what is rendered zero times, not what is asserted.

**Done:** the four consumers' summed durations drop ~4×; suite green; **gate re-measured
here** — this is where the 30s median must hold with margin.
**Red-check:** a plant in a scratch store can never surface through a map built from the
untouched corpus store, so the check must rebuild the map over the plant: shape the fixture
as a builder parametrized by store path (`corpus_pages` = build(corpus_db)), then in scratch
point the builder and a fresh `TestClient` at a planted copy carrying a `>None<`-producing
NULL, and at one with a broken enrichment table, and confirm the respective consumers red
against *that* map; revert.

### Phase 4 — query-shape fixes in the shared SQL/macros (product work that also buys margin)

**As built,** fixes 1–3 landed on this branch after the gate was met, at Nathaniel's
instruction — which is what settled open question 1 against its own recommendation of a
separate branch. Each was re-measured on Python and its A/B is in [results.md](results.md), so
the **[Rust]** marks below say what the fix was proposed on, not what it was kept on. Fix 4 was
measured and dropped, which is the "or not at all" it was already offered under: at most 0.8 s
of 110 s, against the invariant that a connection holds the whole macro library. Fix 3's memo
is narrower than the sketch below — a `Levels` on the request's `Corpus`
(`src/hyphae/view/pages/node/levels.py`), not something the request's `Db` holds.

These speed `hp view` for a reader, not just the tests. Each was measured end-to-end on the
Rust port (same DuckDB 1.5.5, same SQL bytes) **[Rust]**; each must be re-measured on Python
before it is kept. Combined **[Rust]**: mean page 69.6ms → 40.7ms from the first three alone.

1. **`context_window()` CASE → MAP** (`src/hyphae/analyze/macros.py::_CONTEXT_WINDOW`). The
   generated simple CASE desugars so a correlated-subquery operand — which
   `view_compactions.sql` passes — re-plans once per model in the price table (~0.95ms/arm,
   growing with every model added). A `MAP {...}[model]` body evaluates the operand once;
   semantics identical including NULL/unknown key → NULL **[Rust]**. Verify the NULL/unknown
   behaviour and result type with a direct duckdb probe on Python before trusting it
2. **Rollup views: correlated subqueries → grouped joins**
   (`src/hyphae/export/duckdb.py::_rollup_view` — eleven `(SELECT … WHERE … = s.id)`
   columns; count them at the source, not here). Rewrite as
   `LEFT JOIN (… GROUP BY session_id)`. 11.2ms → 1.9ms per `session_rollups` read; list
   pages nearly halved **[Rust]**. The same pattern is in `view_runs.sql` — five correlated
   subqueries (cost, unpriced, tool errors, compactions, the context struct), several
   sharing one correlation key; again, count at the file before rewriting
3. **Walk stops re-reading the NavTree's levels** (`src/hyphae/view/walk.py::_Reader` re-issues
   the exact statements `nav_tree` already ran in the same request — 24–31% of query time on
   the deep node kinds **[Rust]**). Preferred shape: a per-request memo keyed on
   (query name, frozen bindings) held by the request's `Db`, so identical statements within
   one request are fetched once. Request-scoped only — nothing outlives the connection, so the
   ruling is untouched
4. **Lazy JSON macro install** (`macros.install` unconditionally creates `tool_asked`, which
   autoloads the JSON extension at ~5.8ms; most `view_*.sql` files never touch JSON
   **[Rust]**). `macros.needed_by()` decides per statement but is all-or-nothing today — it
   returns the whole `SETUP` or nothing — so lazy JSON install first needs per-macro
   granularity it does not yet have. Smallest win; take it last or not at all

Sequencing note: these land after the gate is met by Phases 1–3, as their own branch —
they change shipped query results' *byte layout* risk-free only if verified (risk register).

**Done:** per-fix, an A/B timing on Python showing the win, and the equivalence assertion below
run once.

### Phase 5 — contingency: test-harness connection reuse (only if the gate is unmet)

**As built,** the reorder landed — `pytest_collection_modifyitems` in `tests/conftest.py`, which
fronts `tests/view/` and the shared-corpus group. The connection reuse did not: the gate was met
after it, so the invasive half was never needed.

**Try tail reordering first — it is far cheaper.** xdist hands tests out in collection
order, and `tests/view/` collects after every other directory, so the heavy leaves start
~9–12s into the run. A `pytest_collection_modifyitems` hook in `tests/conftest.py` that
fronts the long tests is a few lines, changes nothing about what any test asserts, and is
re-measured in one run. Only if the gate is still unmet after that:

Hold one read-only connection per `TestClient` app instead of per request, in the harness
only — e.g. an app-factory used by the fixtures that overrides the `Db` dependency with a
cached connection. Saves the per-request open + `SET TimeZone` + macro install (~14ms × a few
thousand requests ≈ 30–50s serial **[Rust]**; unmeasured on Python).

Why it would not contradict the ruling: the ruling protects `hp extract` from a viewer holding
the store's file lock. A test's store is a private tmp file no extractor will ever open; the
lock the cached connection holds excludes nobody. The shipped `open_store` is untouched.

Deliberately last: it is the most invasive harness change (the `SchemaMoved` /
store-moved-underneath lifecycle tests *depend* on per-request opens and would need to opt
out), and the gate is judged after Phase 3, which the projections say suffices without it. Do not build it
speculatively.

## Where Python differs from the Rust findings (verified on `main`)

1. **The clone-page trim does not apply.** Rust's `bounds_node` swept 933 URLs because its
   URL lister read the *planted* store, including 139 planted clone pages. Python's sweep is
   `pages(store)` where `store` is the session-scoped **corpus** connection
   (`tests/view/conftest.py`) — planted ids are served but never swept. Python already does
   ~516 requests, and the Rust plan's biggest sweep cut is already Python's shape. Corollary:
   if `pages()` is ever pointed at a planted store, do not exclude clones by URL substring
   (`-planted-`) — key any exclusion off the plant that wrote them
2. **The straggler is 41s, not 75s** (same reason), and the second-worst Python test —
   `test_app__list.py::test_a_column_the_store_left_null_reads_as_one_dash` (13.9s) — has no
   Rust counterpart in the top ten
3. **`main` is at `bc2aa17`, not the `e0d0d16` the goal was baselined on**; 2,187 ids
   collect, not 1,976. The 199.6s baseline predates at least PR #25

## File-tree diff

```
plans/test-runtime/design.md      this file — commit it on the implementing branch first;
                                  an untracked copy left on main blocks the fast-forward
plans/test-runtime/baseline.md    Phase 0 output
plans/test-runtime/results.md     what the finished suite measures, against that baseline
pyproject.toml                    + pytest-xdist (dev group, with a purpose comment)
mise.toml                         test task: uv run pytest -n auto [--dist loadgroup from Phase 3]
                                  as built, -n 12 or every core where there are fewer
tests/conftest.py                 duckdb.connect wrapper pinning SET threads TO 1;
                                  collection reorder fronting the long work (Phase 5)
tests/view/conftest.py            + render_pages builder and corpus_pages fixture (Phase 3)
tests/view/test_bounds__node.py   straggler split into three ids over a module-scoped plant
tests/view/pages/node/test_node.py, .../test_walk.py, tests/view/test_enrichment.py,
tests/view/test_app__list.py      consume corpus_pages (Phase 3)
src/hyphae/analyze/macros.py      MAP context_window (Phase 4; lazy JSON install not built)
src/hyphae/export/duckdb.py       grouped-join rollup views (Phase 4), and view_runs.sql beside it
src/hyphae/view/pages/node/levels.py  the per-request level memo (Phase 4), where the sketch
                                  said walk.py|store.py|deps.py
src/hyphae/view/dev.py            found by profiling rather than designed here: the reload
                                  watcher lets go on a shorter timeout (results.md)
```

## Decisions

- **xdist flags in the mise task, not `addopts`** — keeps single-test debugging and
  `mise run mutate` serial. Rejected: `addopts = "-n auto"` (reshapes every pytest invocation)
- **Thread pin as a `tests/conftest.py` wrapper around `duckdb.connect`** — one seam, zero
  shipped-code change. Rejected: an env knob in `open_trace_store` (a test-only branch in the
  shipped opener, and it would miss the fixtures' direct connects)
- **Split the straggler; don't sample it** — sampling turns argmax pins into guesses. Rejected
  per the standing no-weakening rule, as is structural-class dedupe (same reason: `assert ==`
  over a guessed representative under-measures silently)
- **Reject the page-2 sweep derivation** — exact only at default `?log=`, and it re-serializes
  the split under xdist. Rejected alternative kept on record above in case the pager leaf stays
  the straggler
- **Share renders via a fixture, not a merged test** — pytest's session fixtures give
  per-property failure reporting for free; the Rust port merged leaves only because nextest
  has no cross-test fixture
- **Phase 3 runs unconditionally; the gate is judged after it** — the post-split band is
  22–28s with only 2–5s of margin, too thin to declare victory at Phase 2. Rejected: gating
  Phase 3 on a Phase 2 miss (it invites stopping at a 29s median with no headroom)
- **Do not bypass the router / TestClient wholesale** — the Rust probe put the request layer at
  1.5µs/request; FastAPI's TestClient has real but unmeasured overhead on Python. If Phase 0's
  no-op numbers show it matters, an `httpx.ASGITransport` client is the drop-in to try —
  an open hook, not a phase
- **Phase 4 is product work gated on Python re-measurement** — every number there is [Rust]

## Risk register

- **xdist + DuckDB file locks:** absent by construction — every store file is per-worker tmp;
  both measured full runs showed zero lock errors. If a future fixture shares a store *file*
  across workers, RO+RO is safe but RO+RW is not; keep builders per-worker
- **Exact-pin argmax assignment (Phase 2):** a pin assigned to the wrong sweep fails loudly
  (`==` unmet), never silently — but instrument first (under `HYPHAE_PIN_EXACT=1`) so the
  split lands green
- **The three enrichment-absence stores stay separate — a standing ruling.** No-table,
  empty-tables, and partly-described are three different stores proving three different
  guards; Phase 3's byte-identical argument applies within one store only and must never be
  used to merge them
- **`_free_port()` bind race (pre-existing):** `tests/view/test_dev.py` binds port 0, closes
  the socket, then hands the number to a child `hp view` — a cross-worker reuse window under
  xdist. One leaf; both measured runs were clean; ledger it as a known flake source rather
  than fixing speculatively
- **Float summation order (Phase 4.2):** grouped-join rollups sum `cost_usd` in a different
  order; last-bit drift is possible. During development, assert old and new view SQL return
  equal rows on the fixture corpus with costs compared under a tight tolerance — and check
  whether any test pins an exact float (page rendering rounds to 2dp via `money()`, which
  absorbs it)
- **MAP macro semantics (Phase 4.1):** probe NULL key, unknown key, and result type on the
  Python duckdb build before swapping
- **`-n auto` on CI:** fewer cores than 18; the 30s gate is the owner's machine, CI merely gets
  faster than today. `timeout = 120` stays far above the worst post-split leaf
- **Clone-filter-by-substring:** a trap only if `pages()` ever reads a planted store — see
  difference #1

## Out of scope

- The browser tier (`tests/e2e`) — its own workflow, already outside `mise run check`
- Production viewer performance beyond Phase 4's shared-SQL fixes; no change to the
  per-request connect ruling
- The 51 env-gated skips and the live-CLI/live-store leaves — unchanged
- Redesigning what any sweep asserts — no sampling, no property drops, per the brief

## Open questions

1. Should Phase 4 land at all once the gate is met, or be re-scoped as a viewer-performance
   change with its own plan? (Recommendation: land it — it is user-facing speed — but as a
   separate branch so the suite change is reviewable alone)
   **Answered:** on this branch, at Nathaniel's instruction
2. `TestClient` per-request overhead on Python is unmeasured; Phase 0's no-op run bounds it.
   If it is >5ms/request, the ASGITransport swap becomes a real phase
   **Answered:** it is not — the swap is 0.040 s of a 7.64 s sweep, half a percent
   ([results.md](results.md)), so there is no phase here
3. Does `mise run mutate` interact with the new task flags on this machine? (Expected no —
   flags live in the task, and mutmut invokes pytest directly — but run one mutant to confirm)
