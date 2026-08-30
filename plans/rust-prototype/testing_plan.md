# Testing plan: Rust conversion prototype

The obligations for [the design](design.md). A prototype's suite, not a port of the Python tier's
842 tests: each level below earns its place by proving something the level under it cannot, and
the parity diff over the real corpus carries the weight the missing tests would have carried.

Three oracles do most of the work. The **parity diff** proves extract and store against the real
corpus. The **browser tier** proves the viewer against a real Chromium. The **Rust suite** is a
thin gate that reds in seconds on the way to those two.

## The levels

### unit (`hyphae-extract`) — recorded JSONL on disk, no store, no server

Fixtures are the redacted recorded sessions already under `tests/fixtures/` (`.claude/rules/testing.md`);
this level invents no record. Each leaf names the fixture directory that drives it.

- A session's turns, api calls and tool calls come back with the same ids and parents the Python
  extractor produces for the same file. *Evidence:* `tests/fixtures/spine`; the Rust entity ids
  compare equal to a JSON dump of `hyphae.extract.transcript` over the same transcript, checked in
  as an `insta` snapshot the Python side generated
- Parallel tool calls in one api call all attach to that call, in transcript order. *Evidence:*
  `tests/fixtures/parallel_tools`; assertion on the tool-call list of the owning api call
- A subagent transcript becomes its own thread keyed by the run id, and the spawning tool call
  points at it. *Evidence:* `tests/fixtures/teammate`; assertion on `source` of the run's rows and
  on the tool call's run reference
- A compaction record is recorded as a compaction on its own thread. *Evidence:*
  `tests/fixtures/compaction`; assertion on the compaction row's thread and index
- Rows a fork or resume copied are marked replayed rather than dropped. *Evidence:*
  `tests/fixtures/fork_origin` and `fork_byref`; assertion that the replayed flag is set and the
  row still exists
- **An unexpected record shape raises, naming the offending value and the line** — bolded because
  the design's chosen `Value` walk (`extract/transcript.py`, `_check_type`) is the one place a
  silent skip would corrupt every downstream count. *Evidence:* a one-line invented record with an
  unknown `type` appended to a fixture copy in a tempdir (invented, and unavoidably so — no
  recorded session carries a schema violation); the error message contains the type string
- An offload file referenced by `persistedOutputPath` is extracted as an offload row.
  *Evidence:* `tests/fixtures/offload`; assertion on the offload row's name and its tool call
- A duplicated record uuid does not collapse two rows into one. *Evidence:*
  `tests/fixtures/dup_uuid`; row count assertion

### unit (`hyphae-extract`, fingerprints) — real files in a tempdir

`extract/claude_code.py:fingerprint` digests every session file's path, size and mtime_ns, folded
with the extractor version. The design ports it; these leaves pin what re-extraction depends on.

- **A fingerprint changes when a subagent transcript or an offload file changes, not only the main
  transcript** — bolded: this is the whole reason the digest covers the file set, and a port that
  digests one file passes every other leaf here. *Evidence:* a fixture tree copied to a tempdir;
  the digest before and after touching each non-main file compare unequal
- Two runs over an untouched tree produce the same digest. *Evidence:* same tempdir, two calls,
  equal strings
- The digest folds in the extractor version, so bumping it re-extracts. *Evidence:* two digests
  over the same tree with different version constants compare unequal

### integration (`hyphae-store`) — a real DuckDB file in a tempdir, written by the Rust exporter

No mocks: DuckDB is the thing under test as much as the code around it. The store is built by
extracting `tests/fixtures/` through the Rust path, which is how every viewer leaf below gets its
rows too.

- **The widest table round-trips its nested LIST/STRUCT columns unchanged** — bolded: stage 1 of
  the design exists to find out whether the appender can write these at all, and the fallback to
  prepared `INSERT` batches hangs off the answer. *Evidence:* insert the fixture corpus's rows,
  read them back, compare the nested values field for field; the leaf's failure mode is a panic,
  which is itself the go/no-go signal the design asks for
- A TIMESTAMPTZ written by the Rust exporter reads back as the same instant in UTC. *Evidence:*
  a session's `started_at` from the fixture corpus, compared against the value the Python store
  holds for the same session
- **Re-exporting one session replaces its rows and leaves every other session untouched** —
  bolded: `export/duckdb.py:export` deletes by session key across every table inside one
  transaction, and a missing table in that list would leak rows on every re-run. *Evidence:*
  build the corpus, re-export one session, assert row counts per table are unchanged and no
  duplicate primary key exists
- A failed insert rolls back, leaving the session's earlier rows intact. *Evidence:* export a
  session, then export a mutated copy whose last table violates a constraint; the store still
  holds the first export's rows and the error propagates
- The insert column list and the DDL agree for every table. *Evidence:* a leaf that reads
  `information_schema.columns` for each table and compares it to the crate's per-table column
  constant — the check the design's "write them once, beside the DDL" replaces `dataclasses.fields`
  with
- A store the Rust exporter created carries the same `SCHEMA_VERSION` value the Python store
  stamps. *Evidence:* the `meta` row compared against `src/hyphae/export/schema.py:SCHEMA_VERSION`
- Opening a store stamped with a different schema version fails loudly. *Evidence:* a tempdir
  store with the version row rewritten; the error names the held version
- Opening a store another process holds the write lock on reports it as locked, not as a crash.
  *Evidence:* a second connection opened against a store held open for write; the error is the
  typed locked variant

### integration (`hyphae-view`) — the axum router by `oneshot`, over the fixture store, no browser

`Router: tower::Service`, so this tier is the Rust counterpart of `tests/view/test_node.py`'s
`TestClient` sweep. The store is the one the level above builds; enrichment rows come from the
Python `tests/conftest.py:build_enriched_store` (see *Seam changes* — no Rust code writes them).

- **Every URL in `tests/e2e/routes.json` answers 200** — bolded: this is the page sweep, the
  cheapest thing standing between a refactor and a broken page, and reading the generated route
  file rather than a hand-written Rust list is what keeps the two tiers naming the same pages.
  *Evidence:* one test case per entry; the failure names the route template and the status
- A node id the store does not hold is a 404, for every node kind. *Evidence:* one case per kind
  with a fabricated uuid (invented ids, deliberately — the point is that the store lacks them);
  mirrors `test_a_node_the_store_does_not_hold_is_a_404`
- **A hostile title round-trips escaped on every surface that prints it** — bolded: the design
  moves the escaping contract from htpy's default to `rsx!`'s, and a single `Raw` in the wrong
  place is unreviewable by eye across ~92 components. *Evidence:* the sentinel pattern of
  `tests/view/test_app__safety.py` — a fixture session whose title carries
  `<script>alert('planted')</script>`, asserted absent verbatim from the NavTree row, the crumb
  chain, the reading pane title, the tab title and the children log of every page that lists it
- An attribute value that is already markup is still escaped. *Evidence:* the case
  `tests/view/test_components.py:test_an_attribute_is_escaped_even_when_its_value_is_already_markup`
  ported to `rsx!`
- Markdown a pass wrote renders as HTML in the reading pane and as text in a NavTree row.
  *Evidence:* an enrichment description containing a block element; asserted rendered in one place
  and escaped in the other, mirroring `tests/view/test_node__markdown.py`
- **Keyset paging over a thread returns each row once and stops** — bolded: `view/store.py`'s
  `window` / `page_rows` / `cursorless_rows` split is subtle, and a cursor bug shows up as
  silently missing rows rather than as an error. *Evidence:* walk every page of the widest
  fixture thread by following `after`; the union of rows equals the unpaged query's rows, with no
  duplicate `turn_index`
- A row the paging query gives no cursor value is reachable through the cursorless path and is
  outside the count. *Evidence:* the fixture corpus's unattributed bucket; assertion that its rows
  appear on the page and that `matched_rows` excludes them
- A `?detail=` fetch returns the full value while the page's preview is cut at the documented
  ceiling. *Evidence:* `tests/fixtures/offload`'s largest tool result; the page body's length is at
  the cut and the detail response's is not
- A `?log=` page and a `?nav=` preset each change the page and each survive a round trip through
  the links the page mints. *Evidence:* one case per knob; the returned markup's own links carry
  the non-default knob back (`docs/viewer-bounds.md`)
- Every response carries `content-security-policy: default-src 'self'`. *Evidence:* header
  assertion over the route sweep, which is what makes the browser tier's console check meaningful
- A locked store answers 503 rather than 500. *Evidence:* a request issued while a writer holds
  the store; status assertion
- `insta` snapshots of the NavTree row, the crumb chain, the facts block and the cost badge.
  *Evidence:* four committed `.snap` files over fixture rows; the NavTree row above all, since the
  readability of exactly that function is a goal the report answers

### CLI (`hp` binary) — the compiled binary against tempdir stores

Process level, because the fail-fast contract is about refusing to start.

- `hp extract` against a directory with no sessions exits non-zero with a message naming the
  directory. *Evidence:* an empty tempdir; exit code and stderr asserted
- `hp view` refuses a port already bound, naming the port and the remedy. *Evidence:* a listener
  held on the port; the message mirrors `view/app.py:claim`
- `hp view` against a missing store file, or one holding another schema version, refuses to start.
  *Evidence:* two tempdir cases; both exit non-zero before binding
- `hp extract` run twice over the same tree re-extracts nothing the second time. *Evidence:* the
  fixture tree; the second run reports zero sessions extracted and every `extract_state` row
  keeps its first run's `extracted_at`

### parity (the corpus oracle) — both binaries, the real store, harness in gitignored `data/`

The real corpus at `/Users/nob/repos/hyphae/data/traces.duckdb` in the primary checkout, never
this worktree and never committed. Local-only by construction: the harness, its two output stores
and its diff all live under `data/`.

- **Every table the extractor writes compares row for row, ordered by primary key, between the
  Python and Rust stores over the same corpus** — bolded: this is the one oracle that sees the
  long tail of real transcripts the fixture corpus cannot hold, and it is what lets the levels
  above stay small. *Evidence:* the harness's per-table diff output, checked into the prototype's
  report as counts; ship with the diff empty or every difference explained
- The comparison states what it excludes and why. *Evidence:* the harness names the excluded
  columns in its output — `extract_state.extracted_at` (wall clock), the extractor name and
  version columns, and the enrichment tables (no Rust pass writes them). An exclusion not printed
  is an exclusion nobody reviewed
- A session whose files changed since the Python store was built re-extracts under the Rust
  binary. *Evidence:* the fingerprint columns of the two stores compare equal for unchanged
  sessions — which requires the Rust extractor to use the Python `EXTRACTOR_VERSION` string
  verbatim (see *Design findings*)
- The Rust extract's wall-clock time and the resulting store's file size are recorded beside the
  diff. *Evidence:* the report's numbers; not a pass/fail leaf, but the product question the
  prototype exists to answer

### browser tier (`tests/e2e/`) — real Chromium against the Rust server over the fixture corpus

The existing Playwright specs, pointed at the Rust binary. See *Seam changes*: the specs are
unchanged, the config is not.

- **Every full page loads with an empty console under the real CSP** — bolded: this is the
  viewer's acceptance test and the only thing that sees an inline `<style>` the policy refuses or
  a script that throws. *Evidence:* `tests/e2e/specs/pages.spec.ts` green against the Rust server,
  with its `afterAll` proving the sweep visited every full page in `routes.json`
- The htmx interactions — popover fetches, the detail fetch, the log page swap — land 200 and
  swap into the right target. *Evidence:* `tests/e2e/specs/htmx.spec.ts` green against the Rust
  server, unchanged
- The Python tier's own browser run still passes with the config seam in place. *Evidence:*
  `mise run e2e` with no environment set, green, on the same branch
- `tests/e2e/routes.json` still matches `tests/view/scenarios.py`. *Evidence:* the existing
  generator-comparison leaf beside `tools/gen_e2e_routes.py`, unchanged and still run by
  `mise run check`

### the gate — one command

- `mise run rust-check` runs fmt, clippy `-D warnings` and nextest, and fails on any of them.
  *Evidence:* the task's own run in CI or locally with a deliberate warning introduced; it reds

## Seam changes this plan requires

The design says the browser tier runs "unchanged". Read literally that is false, and the gap is
worth naming before someone discovers it mid-implementation.

- `tests/e2e/playwright.config.ts` hard-coded `PORT = 8479` and
  `webServer.command = "mise run gallery --port ..."` with `cwd: "../.."`. It now reads three
  values from the environment, each defaulting to what it was: the base URL, the server command,
  and the readiness URL. The **specs** under `specs/` are genuinely unchanged; they read only
  `routes.json` and `baseURL`
- The tier tests the *gallery*, not the bare viewer: `tests/gallery/serve.py` builds a store from
  the redacted fixtures, writes enrichment rows through `build_enriched_store`, freezes
  `fmt.utcnow` to `corpus_now(store)` so pages hold still between launches, and mounts an index at
  `/gallery`. The store file is the seam the design already declares, so the practical shape is:
  Python builds the gallery store once and the Rust binary serves that file, with `HYPHAE_FIXED_NOW`
  set to the instant `corpus_now` derived. The `/gallery` index is Python's and stays there — no
  spec visits it, and the readiness URL points at `/` instead
- `gallery()` calls `build_app(store, dev=True)`, which puts `/static/dev-reload.js` on every page;
  that script opens an `EventSource` on `/dev/reload`. With `--dev` parity a non-goal, the Rust
  server omits the script rather than growing a quiet SSE endpoint — which is what keeps the
  sweep's empty-console assertion meaningful on a server that cannot reload

## Deliberately not covered

- **The bounds and budget sweeps** (`tests/view/test_bounds*.py`) — payload ceilings per page
  shape, which the design names as out of scope with the tooling layer. The `?detail=` cut leaf
  above is the one piece kept, because it is a page contract rather than a budget
- **Mutation scoring.** `mise run mutate` is Python-only tooling; the Rust suite is too small for
  the signal to mean much
- **Enrichment writing, OTLP export, `hp view --dev`, the cog/aigarden layer.** Design non-goals;
  each stays Python and the store file is the seam. The viewer *reads* enrichment rows, and the
  leaves above cover that read path
- **Dark mode and the narrow layout.** The browser tier already pins light mode at 1400x900 and
  says so; the prototype inherits that bound rather than widening it
- **Cross-platform builds.** One `cargo build --release` on this machine is the product question;
  a matrix is a migration's problem
