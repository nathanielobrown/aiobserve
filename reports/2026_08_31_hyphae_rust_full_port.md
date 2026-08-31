# 2026-08-31 — hyphae: the Rust port, finished and priced

The port is done. Everything `reports/2026_08_30_hyphae_rust_prototype.md` left in Python — enrichment writing, OTLP export, `hp view --dev`, the analysis library, the store's read side — is now in Rust, and every one of the Python tier's 2,027 test ids has a disposition. This report carries three things the prototype could not: what each suite costs when you run it, where each Python test went, and what the port found in production code that both languages share. The design and its four as-built amendments are `plans/rust-prototype/full-port.md`; this report does not restate them.

The price, since it is the question the prototype deferred: ten agent slices over two calendar days, 108 commits (`git log --oneline 4cf891f..HEAD`, the prototype report's commit to the branch head). That is the conversion of the whole tier, not the prototype.

Every number below was measured on one machine — Apple M5 Max, 18 cores, 128 GB, macOS — against this repository's fixture corpus, warm, with nothing else of mine running. Each is the median of three runs. This measures two suites on this hardware, not two languages.

## The suites, as a person actually waits for them

| Harness | Command | Count | Median wall | Runs |
| --- | --- | --- | --- | --- |
| Python tier | `uv run pytest` (single process, as `mise run test` runs it) | 2,027 ids — 1,976 passed, 51 skipped | **199.60 s** | 199.44 / 199.60 / 200.07 |
| Rust tier | `cargo nextest run` (default `-j`, 18 cores) | 792 tests, all passed | **115.46 s** | 115.00 / 115.46 / 115.90 |

The Rust suite finishes in 58% of the Python suite's wall, 84 seconds sooner. Both figures are their own harness's operating point.

**The two counts are different units.** pytest counts a parametrized node id; a Rust test is the leaf that loops over the same cases — `tests/analyze/test_queries.py` alone is 332 ids that Rust expresses as 7 sweeps over one manifest. 2,027 against 792 is not 2.6x the testing, and neither wall may be divided by either count.

Beside the headline, never above it:

| Figure | Command | Median | Runs |
| --- | --- | --- | --- |
| Rust at `-j1` (the isolation model, machine held out) | `cargo nextest run -j1` | 301.21 s | 300.62 / 301.21 / 301.49 |
| Rust full gate | `mise run rust-check` | 116.58 s | 115.19 / 116.58 / 117.67 |
| Python view subset | `uv run pytest tests/view` | 179.26 s, 870 ids | 179.19 / 179.26 / 179.36 |

**The 115s→301s gap is 18 cores, not harness overhead.** A throwaway probe of 200 empty nextest tests ran in 1.913 s at `-j1`, so a test process costs 9.6 ms; 200 tests that each open the cached corpus store read-only ran in 6.046 s, so a cold-process store open costs 20.7 ms on top. Over 792 tests that is 7.6 s of spawn, 2.5% of the `-j1` wall, and at most 23.9 s — 8% — if every test opened a store. At the default `-j` it is under 1%. The design asserted the opposite before anyone measured it; the correction is on the design.

The per-subject pairs the slices collected show the honest shape the headline flattens, and it runs both ways. Store-heavy families favour Rust: `tests/view/test_enrichment.py` is 12 ids in 33.73 s against 14 Rust leaves in 17.99 s, and `tests/view/test_dev.py` is 62 ids in 20.01 s against 14 leaves in 2.91 s. Pure-function sweeps favour pytest, which pays no process at all where nextest pays one per leaf: `test_formatters.py` is 38 ids in 0.02 s against 4 leaves in 0.011 s, and `test_highlight.py` is 15 ids in 0.05 s against 15 leaves in 0.121 s. Each pair is that slice's own measurement of one file, not re-measured for this report.

## Every Python test has a disposition

Collected fresh at `188a4d4`: `uv run pytest --collect-only -q` reports 2,027 ids, `cargo nextest list --workspace` reports 792 tests in 117 binaries.

| Row-class | ids | share |
| --- | ---: | ---: |
| ported — a Rust test is named | 1,630 | 80.4% |
| re-expressed — folded into a different Rust oracle | 180 | 8.9% |
| class (c) — Python-repo-only, stays | 191 | 9.4% |
| deferred — named, with a reason | 17 | 0.8% |
| already-covered — an existing Rust leaf absorbs it | 9 | 0.4% |

The tier is 2,027, not the pre-port census's 2,017, and the ten extra are the port's own: the generation bridge added Python-side freshness gates (`tests/tools/test_gen_bounds.py` +3, `test_gen_query_manifest.py` and `test_gen_enrichment.py` new). The port grew the class it does not port, which is what a generator written in Python for a Rust consumer costs.

**One classification is unresolved.** The census marks class (c) per file; two slices reclassified per leaf, calling 14 of `tests/extract/test_records.py`'s 117 ids citation gates over Python source with no Rust analogue. Under the per-file rule class (c) is 191 and re-expressed is 180; under the per-leaf rule it is 205 and 166. The table above uses the per-file rule, because that is what the design's figures rest on. Nobody has ruled.

## What the port found in production

One design ruling and five findings, all in code both languages run:

- **The viewer opens a connection per request, by design** — and it stays that way. A DuckDB read-only connection holds the file's shared lock for its lifetime and no busy timeout exists to wait on (probed, duckdb 1.5.5), so caching one in axum state would block every `hp extract` until the viewer exits. The probe and the rejected alternatives are the design's per-request-connect amendment
- **That connection costs Rust ~10 ms more than Python's, on every page load.** Over 50 opens of the same store: bare `connect` 3.07 ms against Python's 3.53 ms — the engine is at parity — but `SET TimeZone='UTC'` costs Rust 3.6 ms and Python nothing, and installing the eight macros costs 6.5 ms against 0.56 ms. Production is untouched; installing the macros lazily is the named follow-up, since `macros::needed_by` already knows which statements call one
- **One test is the Rust suite's critical path.** `hyphae-view::bounds_node`'s node-page sweep serves every node page of every session three times and runs over 60 s in every timed run — 74 s alone, against Python's 36.9 s for the same three sweeps. At 18 cores the suite cannot finish faster than its longest single test, so that leaf is the whole job for anyone who wants the Rust tier under a minute. The cause is inferred: the per-request open, multiplied by three sweeps of the corpus
- **`Store::open_read_only` never called `check_version`**, where Python's read-only open says how to migrate — so a store of another vintage reached the viewer as binder errors. Found by the port, fixed
- **`highlight::lit` returned `syntax: None` unconditionally**, so the prototype's viewer highlighted nothing and `parts::code` wrote no `code json` wall. Fixed, and the port added real highlighting: syntect behind a 39-entry table mapping TextMate scope prefixes onto the short class names `static/pygments.css` already paints, longest prefix winning
- **`render::PlainCode` spelled a quote `&quot;` where markupsafe writes `&#34;`** — an unlexed fence was the one value the Rust viewer served in a spelling Python does not. It now escapes through `render::escape`

One test-side stance is worth keeping beside them. `hyphae-view`'s `render.rs` gained `nothing_a_transcript_wrote_becomes_an_element_the_browser_acts_on`, which has no Python twin on purpose: it asserts the escaping claim absolutely rather than by parity, because a parity oracle checks two implementations against each other and neither against the page a browser builds.

## The machinery that outlives the port

Five cross-language mechanisms are permanent, not scaffolding:

- **The enriched-store parity leaf** — `hyphae-enrich/tests/parity.rs` shells into `tests.conftest.build_enriched_store` and diffs all three enrichment tables by primary key, every column but the clock, with a vacuous-pass guard
- **The `hp query` parity leaf** — `hp/tests/parity.rs` drives `hyphae.cli.main` in process and diffs both streams per query at production defaults. Neither leaf ever prints a stored value: a mismatch names the query, the row and the column and stops, because the corpus is recorded sessions
- **The DDL-digest leaf** — `the_tables_a_store_creates_are_the_ones_python_declares` shells out to Python's `declared_columns` and compares table by table, column name and type, so a DDL edit stays a versioned decision on both sides
- **The generation bridge, gated on freshness** — Python owns the query manifest, the bounds registry and the enrichment stamps and vocabulary; three generators in `tools/` write committed JSON under `rust/metadata/` that Rust `include_str!`s, and a pytest leaf per generator regenerates into a temp directory and compares bytes
- **The shared recorded envelopes** — both tiers read the same `tests/enrich/fixtures/*.json` (claude 2.1.221, captured 2026-08-13), so no Rust test invents a model response

The maintenance stance follows from those: the Python tier keeps running in CI and nothing Python-side was deleted. `rust-check` stays outside `check` because CI installs no Rust toolchain, and `HYPHAE_SKIP_PYTHON_PARITY` is the escape hatch for the two leaves that need a Python environment.

## The seventeen deferrals

Each is named with its reason where the reader will look for it — the module doc of the code that has no Rust subject, or the design's Out-of-scope list.

| Deferred | Why | Recorded in |
| --- | --- | --- |
| `tests/export/test_duckdb.py` ×5 | `migrate` has no Rust implementation | `rust/crates/hyphae-store/src/store.rs` module doc |
| `tests/export/test_schema.py` ×5 | `check_shape`, `declared_shape` and `SchemaShapeError` have no Rust subject | the same module doc |
| `tests/enrich/test_client__pool.py` ×2 | need a real `claude` on the PATH | the leaves' own docs |
| `tests/test_pipeline.py` ×2 | no second entry point to compare, and `EXTRACTOR_VERSION` is a Rust `const` nothing can monkeypatch | the same |
| `tests/view/test_parts.py` ×2 | `parts::fact` carries no `cut` flag — Python's only caller passes `cut=True` | `rust/crates/hyphae-view/tests/parts.rs` |
| `tests/view/test_dev.py` ×1 | pins that `view/app.py` never hoists `watchfiles`; `notify` is an unconditional dependency, so there is no absence to arrange | `rust/crates/hyphae-view/tests/dev.rs` |
| `tests/test_cli.py` — half a leaf | `hp view` opens no browser, so `--no-browser` has nothing to suppress | the design's Out-of-scope list |

That last entry is half a leaf — its `--dev`/port/store half is ported — which is why the row-class table rounds to 17.

## The verdict: the conversion is done, and the remaining question is ownership

The prototype showed the port was achievable; this pass shows it is complete and what it cost. Ten agent slices converted the tier, the Rust suite gates in 116 s against the Python tier's 200 s, and 90% of the Python ids have a Rust counterpart named — the rest are repo furniture no port would move, plus seventeen deferrals that each name the thing they'd need.

What this pass does not settle is who owns the schema. Both tiers still run, and five cross-language leaves are what keeps them honest; retiring the Python tier would flip registry ownership and take every one of those oracles with it. Until someone decides that, the two-tier arrangement is the product, and its price is the 200 s Python wall in CI plus the Python environment two Rust leaves need.
