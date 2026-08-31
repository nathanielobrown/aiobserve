# Design: the full port — remaining features and the test tier

Extends [the prototype design](design.md); nothing here contradicts what shipped. Scope: port the
four deferred features and the Python test tier's classes (a)+(b) — 1,836 of 2,017 collected node
IDs per the test census (a gitignored handoff; its numbers are restated here where load-bearing) —
on the same branch and PR. The headline metric is suite wall time: pytest runs the tier in 210.44s in one process, and
the report's claim must not let parallelism masquerade as language speed.

The constraint that decides the shape: nextest is process-per-test, so pytest's amortization —
one session-scoped 16-session store shared across 1,411 tests — evaporates. Today each Rust
routes test rebuilds the fixture store at ~2.9s (measured this session); naively extended over
the 776 view tests that is slower than Python. Everything else is a port; this is the design.

## The shared store cache

A `hyphae-testsupport` dev-only crate replaces the three per-crate `tests/common/` copies and
owns `corpus_store()` / `enriched_store()`, each returning a path under
`rust/target/test-stores/<key>/`:

- **Key** = the extractor's own fingerprint fold over the fixture file set, plus
  `SCHEMA_VERSION`, the Rust extractor version, and a planting-recipe version. Invalidation *is*
  the key: touch a fixture or bump a version and the path changes; old directories die with
  `cargo clean`
- **Miss** → build into `<key>/tmp-<pid>.duckdb`, atomic-rename into place; concurrent racers
  lose a few seconds once, benignly. No lock file
- **Every test opens read-only.** RO+RO cross-process opens are fine (probed on duckdb 1.5.5;
  RW anywhere blocks, so nothing ever RW-opens the cached file). A planting test copies the file
  to its tempdir first, exactly as Python's `plant` fixture does
- The enriched store ports `tests/conftest.py:build_enriched_store`'s planting cycles into
  testsupport, writing through the Rust enrichment store (slice 2)

Rejected: `build.rs` (the builder is the crate being compiled — chicken-and-egg, and cargo
re-runs it opaquely); `OnceLock` (process-local, so nextest defeats it); a committed prebuilt
store (binary in git that goes stale silently, and fixtures are transcripts by rule).

## Query and bounds metadata

Python stays the single owner; generation is the bridge, on the `scenarios.py → routes.json`
pattern already in place. A `tools/gen_*` tool emits `queries_manifest.json` (per query: scope,
params, defaults, `REQUIRED`) from `analyze/manifest.py`, and a bounds registry from
`tests/view/budgets.py` + the `Page`/`Fragment`/`Value` catalog; both are committed,
freshness-gated in `mise run check`, and consumed by the Rust sweeps. `hyphae-analyze`
`include_str!`s the manifest JSON as its *runtime* binding table, so `hp query` and the tests
sit on one derivation chain with no second hand-written copy. Exact file homes per
`docs/documentation.md` at implementation.

Rejected: mirrored Rust tables with lockstep tests (drift caught late, two edits per query);
a hand-written language-neutral file (discards the typed `Param` vocabulary and makes Python
grow a parser for its own facts).

## Feature ports

**Enrich writing.** A `CliRunner` trait — `run(args, env, cwd) -> Output` — is the seam
monkeypatch played in Python. Production impl spawns `claude` (the one allowed subprocess site);
`FakeCli` implements the trait returning the recorded envelopes, reused by path from
`tests/enrich/` (`envelope_success.json`, `envelope_logged_out.json`, the two auth fixtures —
real recorded CLI output, the only ground truth for the envelope shape). `mutated`/`without`
become `serde_json::Value` helpers in testsupport, labelled at use site as today. The pool stays
sync: std threads over a bounded channel; the Chain becomes per-item gates (a `Condvar` map) the
fake runner blocks on, released in scripted order, 10s timeout. The enrichment store (DDL,
upsert, stamp) lives in `hyphae-enrich`, mirroring `enrich/store.py`.

**OTLP export.** `opentelemetry-proto` (prost, trace feature) for message types — no SDK, as
Python — `flate2` for gzip, `ureq` as the sync production client so no reactor threads through
the delivery loop. The test collector ports `tests/export/conftest.py` whole: an axum server on
`127.0.0.1:0` in a per-test tokio runtime (dev-dep), recording raw + inflated bytes + headers,
answering from a scriptable `Reply` queue, assertions via `ExportTraceServiceRequest::decode`.
The clock is an injected trait `{ monotonic, sleep }`: `TestClock` records requested sleeps and
advances; `RefusingClock` panics on any sleep, so pacing and backoff tests cost zero wall time.

**Analyze runner.** New crate `hyphae-analyze` (select, bind from the manifest JSON, cite). `hp`
subcommands refactor into library functions taking parsed args and `&mut impl Write`; `main.rs`
stays a thin clap dispatch. Tests call the function and read the buffer — the capsys equivalent,
chosen over assert_cmd for the ~100x per-test process cost, which the suite-speed goal cannot
carry. assert_cmd keeps only the existing process-level fail-fast leaves.

**Dev reload.** `notify` watcher feeding a tokio broadcast channel; the channel is the seam —
tests publish into it and read the SSE response stream through `oneshot`, and the watched-set is
a pure function tested directly. Graceful shutdown via `with_graceful_shutdown`; one
process-level test spawns `hp view --dev`, opens the stream, sends SIGINT, and asserts a clean
exit — the uvicorn-child equivalent, slow-marked by nextest override.

## Clocks

Two seams, not one. The wall clock is one `utcnow()` honoring `HYPHAE_FIXED_NOW`, hoisted from
`hyphae-view/src/format.rs` into a shared module; view formatting and `hp query --as-of`'s
default read it, and one spawned-binary leaf proves the env var reaches a served page. Analyze
tests do not patch anything: they pass `--as-of 2030-01-01` explicitly (a testsupport
`FAR_FUTURE` constant) — the in-process runner makes explicit what the `far_future` class patch
smuggled in. Export keeps the behavioral `Clock` trait above, because an env instant cannot fake
`sleep`. Rejected: one injected clock threaded through the viewer — churn across ~92 components
for a value a fixed instant already covers, and the env seam shipped with the prototype.

## Guards, timeouts, snapshots

- **Subprocess/network guards**: `rust/clippy.toml` `disallowed-methods` bans
  `std::process::Command` and the `ureq` constructors; `#[expect]` at exactly the production
  `CliRunner` impl, the export transport module, and the two test files that spawn our own
  binary. `clippy --all-targets -D warnings` in `rust-check` covers test code too. Rejected:
  a grep gate — clippy resolves paths, so an aliased import cannot slip past
- **Timeouts**: `.config/nextest.toml` slow-timeout with terminate-after for the same 120s hard
  cap as pytest-timeout, plus per-test overrides for the slow-marked leaves. Warnings-as-errors
  is already `-D warnings` at compile; Rust has no runtime-warning channel to trap — the
  crash-on-unexpected extractor is the analogue
- **Snapshots**: one philosophy for the ported tier — no self-referential goldens; ported tests
  parse markup back and assert data, as `tests/view/conftest.py` does. The Python-generated
  parity cases (`render_cases.json`, `format_cases.json`, the extract snapshots) are a different
  species — cross-implementation oracles — but currently have no freshness gate, which is
  exactly the failure `tests/tools/conftest.py:1-7` bans goldens for. They gain a
  regenerate-and-diff leaf in the Python tier. The four insta readability exhibits stay
  confined. When the Python tier retires, the parity cases lose their oracle — flagged, out of
  scope

## Accounting and the speed claim

"Full suite ported" means: every class (a)+(b) test *function* has a named Rust counterpart or a
disposition line — *re-expressed as X* / *absorbed by oracle Y* — in the testing plan's per-file
table, with sweep widths preserved by iterating the same discovered registries. Node-ID counts
are parametrization artifacts, not the unit. The largest re-expressions, named now so the table
surprises nobody: `test_records.py` (117 — pydantic model claims become extraction-output
assertions over the same fixtures) and `test_records__drift.py` (44 — model↔parser agreement is
done by the cross-language parity snapshots and the DDL/column leaf). Class (c) = 181 collected
stays Python: `tests/tools/` 136, `test_components.py` 32, `tests/gallery/` 10,
`test_scaffolding.py` 3 — repo furniture and Python-source introspection with no Rust subject.

Measurement protocol: same machine, fixture corpus, warm compile and store cache (cold adds one
~3s cache build, stated). Three numbers side by side: `cargo nextest run` default (the machine
number), `cargo nextest run -j1` (the language comparison, and the headline sentence), and
pytest's single-process wall time re-baselined after the timezone fix merges — census §6; a
parallel branch owns that fix, and until it lands an evening `mise run check` shows exactly that
one failure. Compile time is reported separately, never folded in.

## Conversion order

Each slice green under `mise run rust-check` before the next; the earliest honest timing signal
comes first because the view tier is 86% of Python's wall time.

1. **testsupport + store cache**; convert the existing 80 tests. Oracle: nextest green, routes
   tests fall from ~2.9s to milliseconds — the recorded before/after is the cache's proof
2. **Enrichment store + planting** (`test_store.py`'s 29). Oracle: ported assertions, plus one
   recorded comparison of row/category/stamp counts against Python's `build_enriched_store`
   over the same corpus
3. **View tier** (776). Oracle: the ported assertions; then the first timing checkpoint against
   pytest's 181.6s view figure
4. **Extract tier re-expression** (241). Oracle: ported assertions + the existing cross-language
   snapshots
5. **Generated registries + library and bounds sweeps** (332 + 195). Oracle: freshness gates
   green in the Python tier; the Rust sweep runs the 22 corpus-scoped queries and skips the same
   44 key-scoped ones
6. **`hp` in-process refactor + analyze runner** (~90 analyze/CLI tests). Oracle: ported
   assertions, plus Rust `hp query` output compared to Python's for each corpus-scoped query
   over the fixture store — the runner's parity diff
7. **Enrich writing** (148). Oracle: the shared recorded envelopes; live leaves stay env-gated
8. **OTLP export** (~125). Oracle: protobuf-decoded spans at the collector; live leaf env-gated
9. **Dev reload** (62). Oracle: SSE frames via `oneshot`; the child-process shutdown leaf
10. **Accounting table, measurement run, report update**; full `test_cli` parity lands with 6

## Out of scope

- The 181 class-(c) tests stay Python, and the Python tier keeps running in CI — nothing
  Python-side is deleted or rewritten beyond the generation tools and the parity-case gate
- The browser tier is already reused; no new specs
- Mutation scoring for the Rust tier, and the registry-ownership flip a Python retirement would
  force
- The `tests/test_cli.py` timezone defect — owned by a parallel branch; not touched here

## Open questions

- Is the one recorded enriched-store comparison (slice 2) enough, or should a permanent lockstep
  leaf live in the Python tier while both planting implementations exist? A drift there skews
  every enrichment-reading view test silently
- `opentelemetry-proto`'s prost types have not been decoded against bytes our encoder produced —
  no recorded OTLP bytes exist to pin the version. Settled by a ten-minute round-trip probe at
  slice 8 start; a mismatch demotes the crate to vendored `.proto` + prost-build
