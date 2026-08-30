# Design: Rust conversion prototype

Convert hyphae to Rust in one shot, as a prototype: a working `hp` binary that extracts the real
corpus, serves the viewer, and passes the reused browser tier. The deliverable is a judgment call
made concrete — what the project feels like on the other side: component readability, the edit
loop, one static binary. It is not a production migration; nothing on `main` changes, and the
Python app keeps working throughout.

Decisions already made (research pass, 2026-08-30, recorded in the session that wrote this plan):
Rust over Go; hypertext's `rsx!` over askama and maud for markup; no PyO3 bridge — the store file
is the only seam between the two implementations.

## Goal and non-goals

The prototype answers three questions:

1. **Readability** — do the ~92 components under `src/hyphae/view/components/` read better as
   `rsx!` functions than as htpy calls?
2. **Iteration** — what does the edit→check→test→reload loop actually cost at this size?
3. **Product** — does one `cargo build --release` binary extract, store, and serve the real
   corpus indistinguishably from `hp` today?

Non-goals, deferred whole rather than half-built:

- **Enrichment writing** (`enrich/client.py`, prompts, validation) — the viewer *reads* whatever
  enrichment rows the store already holds; no Rust pass writes new ones
- **OTLP export** — `hp export-otlp` stays Python; the store seam makes that free
- **The doc/tooling layer** — cogs, aigarden, mutation testing, the gallery, budget/bounds sweeps
- **`hp view --dev` parity** — the prototype serves; the watch/SSE reload loop is a stretch goal

## Where it lives

A `rust/` cargo workspace at the repo root, on branch `rust-prototype`. In-repo rather than a
sibling checkout so tests reach `tests/fixtures/` and the binary reaches `data/traces.duckdb` by
relative path, and so this plan lands committed beside the code it describes.

```
rust/
  Cargo.toml            workspace: resolver 3, edition 2024, shared deps
  crates/
    hyphae-model/       the entities `src/hyphae/model.py` defines, as structs
    hyphae-extract/     transcript walk: JSONL in, SessionTrace out
    hyphae-store/       DuckDB schema, insert path, fetch, the SQL library loader
    hyphae-view/        axum router + rsx components, mirroring `src/hyphae/view/`
    hp/                 the binary: clap subcommands `extract`, `view`
```

Crate boundaries mirror the Python packages one-to-one so a reader can diff the two trees
module-for-module. Splitting also keeps incremental builds per-crate.

## Library choices

| Need | Crate | Why |
|---|---|---|
| Store | `duckdb` (`bundled`, `chrono`) | official org, version-locked to DuckDB releases; bundled build caches after the first compile |
| Web | `axum` | `Router: tower::Service`, so tests hit it with `oneshot` — no server, like TestClient |
| Markup | `hypertext` (`rsx!`, htmx feature) | HTML-shaped source, tag *and* attribute names compile-checked, components stay plain functions |
| JSONL | `serde_json` (`Value` walk) | the extractor deliberately reads dicts defensively (`extract/transcript.py`); port that shape, don't invent structs the schema doesn't promise |
| Markdown | `comrak` | GFM + the unsafe-HTML passthrough `view/render.py` needs |
| Highlighting | `syntect` + `two-face` | class-based output fits the no-inline-style CSP; two-face fills TOML/TS syntaxes |
| CLI | `clap` (derive) | help/completions for free |
| Async | `tokio` | axum's runtime; everything else stays sync, as the Python viewer is |
| Errors | `anyhow` (bin) / `thiserror` (crates) | fail fast with context, typed at the seams |
| Tests | `cargo-nextest`, `insta` | process-per-test speed; snapshots for rendered markup |

Left out on purpose: `schemars` (doc generation is out of scope), an Anthropic client (enrichment
shells to `claude`, and enrichment is out of scope anyway), any ORM or query builder — the 66
files under `src/hyphae/analyze/queries/` and the DDL in `export/duckdb.py` are the source of
truth and port as SQL strings, verbatim.

## The row-typing decision

The one place the port must choose rather than mirror. Python's `view/store.py:fetch` returns
`list[dict]`; every page consumes untyped rows because the SQL owns the logic. Two options:

- **A (chosen): keep rows generic.** `fetch` returns `Vec<Row>` where `Row` wraps column-name →
  `duckdb::types::Value` lookup with typed getters (`row.str("title")?`, `row.f64("cost")?`).
  A missing column or wrong type is a loud runtime error, exactly as Python's `KeyError` is
  today. This is a *port* — the SQL stays the schema authority, and the prototype ships.
- **B (rejected for the prototype): a typed struct per named query** (~40 across the `Page` /
  `Fragment` / `Value` enums in `view/store.py`). More compiler coverage, but it is a redesign
  with a serde-like row-mapping layer to build, and it hard-codes each query's shape in two
  places. Worth revisiting *if* the prototype graduates; the `Row` getters localize the change.

## Conversion order

One shot, but ordered so the riskiest unknowns die first and every stage has an oracle:

1. **Spike the store path (go/no-go).** Against a copy of the real `data/traces.duckdb`: create
   the schema, insert the widest table's rows via the appender, read TIMESTAMPTZ back through
   chrono, run one node-page query. Known trap: the appender can panic on nested LIST/STRUCT
   values — if hit, fall back to prepared `INSERT` batches (the Python exporter's own shape) and
   record it here.
   **Recorded 2026-08-30 — go.** The appender won: the DDL is flat scalars end to end, so no
   stored column is nested and the trap never fired. Nesting lives on the *read* side instead,
   where `view_call_header.sql` answers with a struct of a struct and a list, and
   `duckdb::types::Value` carries it back whole — which is what makes the generic `Row` above
   workable. The fallback would not have helped anyway: both paths bind through `ToSql`, so
   prepared `INSERT` refuses a nested value exactly as the appender does, and refuses it as a
   typed error rather than the expected panic. A nested column, if one is ever added, has to be
   composed in SQL rather than bound. On 10,000 real `api_calls` rows the appender took 22ms
   against the `INSERT` path's 1.6s, so `INSERT` stays only for the rollback shape stage 2
   needs.
2. **Model + extract + store.** Port `model.py` to structs; port the `Value` walk of
   `extract/transcript.py` and `extract/session_files.py` guard-for-guard; port the DDL,
   delete-then-insert transaction, and fingerprint logic of `export/duckdb.py`. The generic
   `dataclasses.fields` insert becomes explicit per-table column lists — write them once, beside
   the DDL, so schema and insert can't drift apart silently.
   **Oracle: the parity diff.** `hp extract` (Python) and `rust/hp extract` run over the same
   corpus into two store files; a harness diffs every table ordered by primary key. Ship with
   diffs either empty or each one explained in the prototype's report.
3. **Store reads + viewer.** Port `view/store.py` (fetch, keyset paging, the `_core`/window
   composition), then the modules `view/app.py` mounts and the components package, file-for-file.
   `static/` — htmx, the three JS files, `style.css` — is copied verbatim; the CSP middleware and
   the escaping contract (auto-escape everywhere, one `Raw` opt-out inside the render module,
   mirroring `Markup` in `view/render.py`) carry over as written.
4. **CLI + wiring.** clap subcommands `extract` and `view`; startup validation stays fail-fast.
   A `mise run rust-check` task (fmt, clippy `-D warnings`, nextest) so the gate is one command,
   matching `check-fast`'s role.

## Verification

- **Parity diff** (stage 2, above) — the extract/store oracle, run on the real corpus locally;
  its harness and output stay in gitignored `data/`, never committed
- **Browser tier, reused.** The specs under `tests/e2e/` are language-agnostic; point their base
  URL at the Rust server and run them unchanged. This is the viewer's acceptance test
- **A bounded Rust suite**, not a port of the 842-test Python tier:
  - every `Page` over the fixture corpus renders 200 (mirrors `tests/view/test_node.py`'s sweep;
    fixtures load from `tests/fixtures/` by relative path)
  - the escaping contract: a hostile title round-trips escaped on every surface that prints it
  - `insta` snapshots of a handful of components — the NavTree row above all, since readability
    of exactly that function is a stated goal
- **The report.** The prototype ends with a short write-up in `reports/`: the three goal
  questions answered with evidence — timed edit-loop numbers, the parity diff result, binary
  size, and the `nav_tree` component side by side in both languages

## Risks and escape hatches

- **hypertext is single-maintainer.** Bounded: its output is plain `Renderable` functions and it
  also speaks maud syntax, so a forced migration touches syntax, not architecture. Pin the
  version; vendor if it goes quiet
- **duckdb-rs appender gaps on nested types** — stage 1 exists to hit this first; prepared
  `INSERT` batches are the fallback. *Closed:* the gap is real but out of reach of this
  schema, and the fallback was the wrong one — see the stage 1 record above
- **Attribute names hypertext's tables don't know** (`data-*`, ours like `data-nav-tree`) use its
  quoted-name escape hatch; if that reads badly at scale, that's a finding for the report, not a
  blocker
- **The edit loop disappoints.** Measure honestly with `mold` + nextest configured; if the
  save→reload loop lands well over ~5s despite that, the report says so — that result is the
  prototype doing its job

## Amendments (2026-08-30, testing pass)

The testing plan (`testing_plan.md`) surfaced five seam gaps; resolved here so every later
stage shares one answer:

- **Browser-tier reuse is spec-level, not config-level.** The specs under `tests/e2e/specs/`
  run unchanged; `tests/e2e/playwright.config.ts` gains an environment seam (base URL, server
  command, and readiness URL overridable via env vars) so the same specs can point at the Rust
  server. That config change is the prototype's only behavioural edit outside `rust/`; its other
  Python-side touches are configuration and tooling learning the new directory exists
- **The tier drives the gallery; the gallery stays Python.** Python builds the gallery fixture
  store (enrichment rows included, via the existing `tests/gallery` code); the Rust `hp view`
  serves that store file — the store file is already the seam. The readiness URL override points
  at `/` instead of `/gallery`; no gallery index is ported
- **Frozen time.** The gallery freezes `fmt.utcnow` so relative times are stable. The Rust
  server mirrors this with a test-only env var (e.g. `HYPHAE_FIXED_NOW`) read at startup
- **No dev-reload script.** The Rust server serves without the dev script, so no `/dev/reload`
  endpoint is needed and the console-quiet assertion holds
- **Fingerprint parity.** The Rust extractor declares its own extractor version, so
  `extract_state.fingerprint` differs from Python's by construction. The parity diff excludes
  that column, prints every exclusion it applies, and the report explains it
