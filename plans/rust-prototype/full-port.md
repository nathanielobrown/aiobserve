# Design: the full port — remaining features and the test tier

Extends [the prototype design](design.md); nothing here contradicts what shipped. Scope: port the
four deferred features and the Python test tier's classes (a)+(b) — 1,836 of 2,017 collected node
IDs per the test census (a gitignored handoff; its numbers are restated here where load-bearing) —
on the same branch and PR. The headline metric is suite wall time: pytest runs the tier in 210.44s
in one process, and the report's claim must not let parallelism masquerade as language speed.

The constraint that decides the shape: nextest is process-per-test, so pytest's amortization —
session-scoped stores shared across the store-reading directories (view, analyze, gallery) —
evaporates. Slice 1 measured the per-process rebuild of both stores at ~0.3 s and the shared
cache removes it; the residual per-request store cost is ruled on in the amendment below.
Everything else is a port; this is the design.

## The shared store cache

A `hyphae-testsupport` dev-only crate replaces the three per-crate `tests/common/` copies and
owns `corpus_store()` / `enriched_store()`, each returning a path under
`env!("CARGO_TARGET_TMPDIR")` (shared workspace-wide; a literal `rust/target/` breaks under
`CARGO_TARGET_DIR`):

- **Key** = one digest over *everything that decides the stored bytes*: a build-script digest of
  the writer crates' sources (`hyphae-extract`, `hyphae-store`, `hyphae-enrich`, testsupport's
  own planting module — which subsumes `SCHEMA_VERSION`, `EXTRACTOR_VERSION`, the DDL and row
  mapping, and the `CLEAN_INVENTED` selection), plus the *selected* transcript set's
  (path, size, mtime_ns) entries, plus — for the enriched key — the taxonomy and prompt versions
  the generation bridge carries (below). Invalidation *is* the key; over-invalidation costs one
  ~2s rebuild, and mtime in the fold means a fresh clone misses cold — correct, just noisy.
  Rejected: keying on hand-bumped version constants alone (nothing forces the bump) and on
  `current_exe()` (per-test-binary keys multiply builds and over-invalidate on any code change)
- **Miss** → take an `O_EXCL` sentinel or poll it (a cold default-`-j` first wave is otherwise
  one multi-threaded build per worker — tens of seconds of thrash), build into
  `<key>/tmp-<pid>.duckdb`, checkpoint and close (no `.wal` remains, probed), atomic-rename
- **Every test opens read-only.** RO+RO cross-process opens are fine (probed on duckdb 1.5.5;
  RW anywhere blocks, so nothing ever RW-opens the cached file). A planting test copies the file
  to its tempdir first, exactly as Python's `plant` fixture does

Rejected: `build.rs` building the store (the builder is the crate being compiled; hashing
sources in a build script is fine, running the pipeline is not); `OnceLock` (process-local, so
nextest defeats it); a committed prebuilt store (binary in git that goes stale silently, and
fixtures are transcripts by rule).

## Python-owned metadata: the generation bridge

Python stays the single owner; generation is the bridge, on the `scenarios.py → routes.json`
pattern already in place. A `tools/gen_*` tool emits committed, freshness-gated JSON carrying
the three Python-owned vocabularies Rust needs at build or test time: the query manifest from
`analyze/manifest.py` (per query: scope, params, defaults, `REQUIRED`), the bounds registry —
emitted by *extending* `tools/gen_bounds.py`, which already derives these tables, not by a
second derivation — and the enrichment versions (`PROMPT_VERSION`, `TAXONOMY_VERSION`,
`LEVELS`), which the planting recipe and the enriched cache key consume. `hyphae-analyze`
`include_str!`s the manifest JSON as its *runtime* binding table, so `hp query` and the tests
sit on one derivation chain with no second hand-written copy.

Two honesty caveats: the freshness gates run in the *Python* tier and its CI workflow
(`rust-check` is deliberately outside `check`), so a Rust leaf re-derives what it can — every
`include_str!` query name has a manifest entry and vice versa. And a **permanent parity leaf**
in the Rust tier shells `uv run` `build_enriched_store` into a tempdir and diffs the enrichment
tables against the Rust-planted cache — landing with the planting port, before any view test
reads the store, because the gallery builds the browser tier's copy through the same Python
function and a silent drift leaves two tiers green against different data.

Rejected: mirrored Rust tables with lockstep tests (drift caught late, two edits per change —
the exact hazard the enrichment versions would have reintroduced as a hand-copied third
vocabulary); a hand-written language-neutral file (discards the typed `Param` vocabulary and
makes Python grow a parser for its own facts).

## Feature ports

**Enrich writing.** A `CliRunner` trait is the seam monkeypatch played in Python:

```rust
trait CliRunner { fn run(&self, args: &[String], env: &Env, cwd: &Path) -> io::Result<Output> }
```

Production impl spawns `claude`; `FakeCli` implements the trait returning the recorded
envelopes, reused by path from `tests/enrich/` (`envelope_success.json`,
`envelope_logged_out.json`, the two auth fixtures — real recorded CLI output, the only ground
truth for the envelope shape). `mutated`/`without` become `serde_json::Value` helpers in
testsupport, labelled at use site as today. The pool stays sync — std threads over a bounded
channel — and the Chain becomes per-item gates the fake runner blocks on, released in scripted
order. The enrichment store (DDL, upsert, stamp) lives in
`hyphae-enrich`, mirroring `enrich/store.py`. Rejected: a fake `claude` executable on `PATH`
(nearer to Python's `refuse_binary`, but it pays a process spawn per call and cannot express the
Chain's in-process gating).

**OTLP export** (`hyphae-export`). `opentelemetry-proto` (prost, trace feature) for message
types — no SDK, as Python — `flate2` for gzip, `ureq` as the sync production client so no
reactor threads through the delivery loop (rejected `reqwest::blocking`, which spins a runtime
thread and drags the async tree in as a dependency). The test collector ports
`tests/export/conftest.py` whole: an axum server on `127.0.0.1:0` in a per-test tokio runtime
(dev-dep), recording raw + inflated bytes + headers, answering from a scriptable `Reply` queue,
assertions via `ExportTraceServiceRequest::decode`. The clock is an injected trait
`{ monotonic, sleep }`: `TestClock` records requested sleeps and advances; `RefusingClock`
panics on any sleep, so pacing and backoff tests cost zero wall time.

**Analyze runner.** New crate `hyphae-analyze` (select, bind from the manifest JSON, cite). `hp`
subcommands refactor into library functions with a three-channel seam — capsys carries three
things, not one: the CSV on stdout, the citation on stderr (asserted apart by the analyze and
export CLI tests), and an exit path matched on message:

```rust
fn run(args: Args, out: &mut dyn Write, err: &mut dyn Write) -> Result<(), CliError>
struct CliError { code: u8, message: String }   // main.rs maps it to stderr + exit code
```

Tests call the function over two `Vec<u8>` buffers and match the error — the in-process choice
over spawning, whose ~100x per-test process cost the suite-speed goal cannot carry. The
process-level fail-fast leaves keep spawning the real binary via `env!("CARGO_BIN_EXE_hp")`, as
the existing CLI tests do.

**Dev reload.** `notify` watcher feeding a tokio broadcast channel; the channel is the seam —
tests publish into it and read the SSE response stream through `oneshot`, and the watched-set is
a pure function tested directly (rejected a poll-based watcher: same seam, worse latency, and
`notify` is the ecosystem default). Graceful shutdown via `with_graceful_shutdown`; one
process-level test spawns `hp view --dev`, opens the stream, sends SIGINT, and asserts a clean
exit — the uvicorn-child equivalent, slow-marked by nextest override.

## Clocks

Two seams. The wall clock is one `utcnow()` in a shared module, read **per call**, resolving in
order: a process-global override cell (`AtomicI64` epoch-micros + a `freeze(instant)` setter,
test-only by documentation), then `HYPHAE_FIXED_NOW` loaded into that cell at startup (keeping
the fail-fast `check_clock`), then the real clock. The current `LazyLock` cache in
`hyphae-view/src/format.rs` cannot express `tests/view/test_app__list.py`'s read-at-render leaf
— two clock moves against one built app — so the cell replaces it; process-per-test makes the
global safe. Rejected: a clock in router state (threads through every handler and component that
prints a relative time for the same observable property) and the shipped LazyLock (refuted by
that leaf).

The pinned-everywhere property of Python's `far_future` class patch — "a clock read added
anywhere under a test here is pinned too" — is restored by mechanism, not discipline:
`clippy.toml` `disallowed-methods` bans `chrono::Utc::now` and `SystemTime::now` outside the
`utcnow()` module and the export `Clock` production impl, the same gate already banning
`Command`. Analyze tests then bind windows explicitly with testsupport constants ported from
`tests/analyze/conftest.py` — `AS_OF_WHOLE = 2026-07-28` (opens the 28-day trailing window at
2026-06-30, covering all 15 corpus sessions), `AS_OF_PARTIAL`, `AS_OF_MID` — while `FAR_FUTURE`
(2030-01-01) remains what a leaf freezes the *clock* to when it exercises the `--as-of` default —
never a binding: 2030 as `$as_of` puts every windowed query years past the corpus and empties
the sweep.

Export keeps the behavioral `Clock` trait above, because an env instant cannot fake `sleep`.

## Guards, timeouts, snapshots

- **Subprocess/network guards**: `rust/clippy.toml` `disallowed-methods` bans
  `std::process::Command` and the `ureq` constructors; `#[expect(clippy::disallowed_methods)]`
  with a reason string marks each allowed site — the production spawn and transport, the
  `CARGO_BIN_EXE_hp` process leaves, the uv bridge the parity leaves use. The marker is the
  registry; no site list here to go stale. `--all-targets` covers test code. Rejected: a grep
  gate — clippy resolves paths, so an aliased import cannot slip past
- **Timeouts**: `.config/nextest.toml` slow-timeout with terminate-after for the same 120s hard
  cap as pytest-timeout, plus per-test overrides for the slow-marked leaves. Warnings-as-errors
  is already `-D warnings` at compile; Rust has no runtime-warning channel to trap — the
  crash-on-unexpected extractor is the analogue
- **Snapshots**: one philosophy for the ported tier — no self-referential goldens; ported tests
  parse markup back and assert data, as `tests/view/conftest.py` does. The Python-generated
  parity cases (`render_cases.json`, `format_cases.json`, the extract snapshots) are a different
  species — cross-implementation oracles — but currently have no freshness gate, which is
  exactly the failure `tests/tools/conftest.py:1-7` bans goldens for. They gain a
  regenerate-and-diff leaf in the Python tier. The four insta readability exhibits stay confined

## Accounting and the speed claim

"Full suite ported" means: every class (a)+(b) test *function* has a named Rust counterpart or a
disposition line — *re-expressed as X* / *absorbed by oracle Y* — in the testing plan's per-file
table, with sweep widths preserved by iterating the same discovered registries. Node-ID counts
are parametrization artifacts, not the unit. The largest re-expressions, named now so the table
surprises nobody: `test_records.py` (117 — pydantic model claims become extraction-output
assertions over the same fixtures) and `test_records__drift.py` (44 — model↔parser agreement is
done by the cross-language parity snapshots and the DDL/column leaf). The library sweep runs
**all 66** shipped queries with `FIXTURE_BINDINGS` and `AS_OF_WHOLE` and asserts each answers
with rows, as `test_every_query_runs` does; the 44-query skip lives only in the separate
corpus-views rule and ports as the same static check. Class (c) = 181 collected stays Python:
`tests/tools/` 136, `test_components.py` 32, `tests/gallery/` 10, `test_scaffolding.py` 3 —
repo furniture and Python-source introspection with no Rust subject.

*(As built, slice 10: class (c) is 191 over a 2,027-id tier, not 181 over 2,017. Slice 2's
generation bridge added ten `tests/tools/` ids of its own — three to `test_gen_bounds.py`, and
`test_gen_query_manifest.py` 3 and `test_gen_enrichment.py` 4, both new — so `tests/tools/` is
146. The port grew the class it does not port, which is what a generator written in Python for
a Rust consumer costs.)*

Measurement protocol — "how fast is the suite" is answerable; "which language is faster" is not,
without controls. Same machine, fixture corpus, warm compile and store cache (cold adds one ~2s
cache build, stated). Report, side by side: `cargo nextest run` default `-j` (the machine and
product number); `cargo nextest run -j1` **with a measured harness floor beside it** — an empty
test × N processes, since spawn (~7ms) and RO store open (18–30ms, both measured this session)
are nextest's isolation model, not the language — and pytest's single-process wall time
re-baselined after the timezone fix merges (census §6; a parallel branch owns that fix, and
until it lands an evening `mise run check` shows exactly that one failure). Pin the build
profile and say so: the dev profile's `opt-level = 1` reaches the bundled DuckDB, so the
measured run uses an optimized profile via `--cargo-profile` or states the `-O1`-vs-wheel
handicap. Compile time is reported separately, never folded in.

## File-tree diff

```
rust/crates/hyphae-testsupport/   dev-only: store cache, planting, AS_OF/FAR_FUTURE, envelope helpers
rust/crates/hyphae-analyze/       select/bind/cite; include_str!s the manifest JSON
rust/crates/hyphae-enrich/        CliRunner seam, prompts, validation, pool, enrichment store
rust/crates/hyphae-export/        OTLP shaping + delivery; the transport module
rust/crates/hyphae-view/src/dev.rs, hp/src/lib.rs (three-channel subcommands)
rust/.config/nextest.toml, rust/clippy.toml; per-crate tests/common/ deleted
tools/gen_query_manifest.py; tools/gen_bounds.py extended; emitted JSON committed + gated
```

## Conversion order

Each slice green under `mise run rust-check` before the next. The bridge precedes everything
that consumes it; the view tier — 86% of Python's wall time — comes as early as its inputs
allow. Collected-ID counts per slice sum to 1,836 with no test in two slices.

1. **testsupport + store cache**; convert the existing 80 tests. Oracle: nextest green, and the
   store build leaves every test process — met at `3c0eb06`: the routes leaf fell 1.75 s →
   1.42 s, exactly the ~0.3 s build, the whole prize once the build's true size was measured
2. **Generation bridge** (query manifest, extended `gen_bounds`, enrichment versions) + the
   Rust re-derivation leaf. Oracle: Python freshness gates green; manifest keys ↔
   `include_str!` names agree
3. **Enrichment store + planting** (29) + the permanent planting-parity leaf. Oracle: ported
   `test_store.py` assertions and the parity diff, before any view test reads the store
4. **View tier** (776, bounds sweep included — its registry exists since slice 2). Oracle: the
   ported assertions; then the first timing checkpoint against pytest's 181.6s view figure,
   under the optimized profile per the amendment below
5. **Extract, store and pipeline remainder** (241 + `test_duckdb` 23, `test_schema` 9,
   `test_pipeline` 10, `test_sessions` 10 = 293). Oracle: ported assertions + the existing
   cross-language snapshots
6. **`hp` three-channel refactor + analyze runner + library sweep** (332 + 90 = 422). Oracle:
   ported assertions, plus a permanent env-gated parity leaf diffing Rust `hp query` output
   against Python's per corpus-scoped query over the fixture store — run here and before any
   release claim, not once
7. **Enrich writing** (148 − 29 = 119). Oracle: the shared recorded envelopes; live leaves stay
   env-gated
8. **OTLP export** (125). Oracle: protobuf-decoded spans at the collector; live leaf env-gated
9. **Dev reload** (62). Oracle: SSE frames via `oneshot`; the child-process shutdown leaf
10. **Full `test_cli` parity (10), accounting table, measurement run, report update**

## Out of scope

- The 181 class-(c) tests stay Python, and the Python tier keeps running in CI — nothing
  Python-side is deleted or rewritten beyond the generation tools and the parity-case gate
- The browser tier is already reused; no new specs
- Mutation scoring for the Rust tier, and the registry-ownership flip a Python retirement would
  force (which takes every cross-language oracle with it)
- The `tests/test_cli.py` timezone defect — owned by a parallel branch; not touched here
- **`hp view --no-browser`** *(ruled at slice 10)*. `hp view` opens no browser, so the flag that
  suppresses one has nothing to suppress. Python gets the behaviour from `webbrowser` in its
  standard library and gets the test free by monkeypatching `cli.serve`; Rust has neither, so
  the port would be a dependency that spawns a subprocess past the `disallowed-methods` gate,
  plus a seam whose only caller is the leaf that reads it — for a convenience tab that no test
  in either tier actually opens. Every other flag of all six subcommands is ported and pinned
  (`rust/crates/hp/tests/surface.rs`). Nothing else in the repo passes `--no-browser`

## Open questions

- `opentelemetry-proto`'s prost types have not been decoded against bytes our encoder produced —
  no recorded OTLP bytes exist to pin the version. Settled by a ten-minute round-trip probe at
  slice 8 start; a mismatch demotes the crate to vendored `.proto` + prost-build

## Amendment: the viewer keeps per-request connect

Slice 1 located the view tier's real residue: production `Reader::connect()` opens a read-only
DuckDB connection per request — ~41 ms/page under the dev profile. The fork was to keep that,
or to cache the connection in axum state.

**Ruling: keep per-request connect, in production and in the tests.** The per-request open is
load-bearing product behavior, not an accident of porting: a DuckDB read-only connection holds
the file's shared lock for its lifetime, an RO holder blocks every RW open, and no busy-timeout
exists to wait on (probed, duckdb 1.5.5) — so a cached connection makes the running viewer
block every `hp extract` until the viewer exits. Opening per request is what lets an extract
land between two page loads, what turns a held write lock into a 503 instead of a crash, and
what makes the schema check per-request (`view/store.py` states this contract;
`Reader::connect` mirrors it). Rejected: a cached or pooled connection (the lock, above; and
`duckdb-rs`'s `Connection` is not `Sync`, so it also forces a pool for a property we don't
want), and reopen-on-mtime (restores staleness but still holds the lock between changes, so
the extract still blocks).

**Staleness:** unchanged. The Rust viewer keeps Python's semantics whole — sees a store
rewritten underneath it, 503 on a locked store, `SchemaMoved` per request. No behavior change
to document.

**The speed claim:** both viewers pay the same per-request open, so the `-j1` headline stays
like-with-like with nothing extra to disclose. Two obligations on slice 4: the ~41 ms is a
dev-profile number (`opt-level = 1` reaches the bundled DuckDB), so run the timing checkpoint
under the optimized profile the measurement protocol already pins, and re-measure the open
there before sizing anything against it; and if per-request opens then dominate the view
tier's wall, report that as a product cost both languages carry — never as harness overhead,
and never trimmed in the harness by sharing a connection production wouldn't.

## Amendment: the report leads with each harness as it is actually run

Ruled at slice 10, after the per-subject pairs the slices collected were laid side by side. The
protocol above makes `nextest -j1` against single-process pytest the headline. On the evidence
that pairing misleads, and in both directions: a store-heavy family flatters Rust, because the
process the `-j1` figure charges it for is the one Python amortizes across a session fixture;
a pure-function file flatters Python, because it pays no process at all and Rust pays one per
leaf. Underneath both, the units differ — pytest counts a parametrized id, Rust counts the leaf
that loops over the same cases, so the two "test" columns are not the same thing and a per-test
figure derived from them is arithmetic on a unit mismatch.

**Ruling: lead with what a person waiting on the suite actually waits for** — `pytest` as CI
runs it (single process, the tier whole) against `cargo nextest run` at its default `-j`. Both
are each harness's own operating point, so the comparison is of two suites rather than of two
languages. Beside it, and never above it: the `-j1` median, which is the isolation model's cost
with the machine held out; the measured harness floor, which is what `-j1` is mostly made of;
and one sentence saying the id counts are different units. The per-subject pairs stay in the
report as the texture the headline flattens, each labelled with the family it came from.
