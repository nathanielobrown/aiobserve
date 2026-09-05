# Design: the OTLP census as an exporter refresh drives

`hp export-otlp --dry-run` should count what the send after it would ship. Today it counts the whole selection. The fix is one more `Exporter` — a census that tallies spans instead of posting them — driven by the same `refresh()` as the send, with the delivery ledger pulled out of `OtlpExporter` so both can read it. `refresh()` does not change.

Designed against the canonical store (`data/traces.duckdb`, probed read-only 2026-09-03): 573 sessions under the mycelia project, 571 `generic` delivery rows at `MAPPER_VERSION` `"1"`, 567 at the session's current fingerprint. The dry run prints "573 session(s) would ship"; a `generic` send ships 6.

## Problem

The dry run and the send share the extractor and the mapper, and diverge on the one thing that decides what ships. `pipeline.refresh` skips a session whose fingerprint the exporter holds; `export/otlp.py:census_project` walks `extractor.sessions(project)` with no diff, and its docstring says so. The CLI prints the census as "would ship" and `docs/otlp-export.md` calls it a preview of the exact export. After the first send every dry run overstates, and `--backend` is parsed and ignored on the dry-run path.

The audit card is half stale. S37 landed: `census_project` is the loop the card wanted in the library, and `cli.py:_census_otlp` is nine lines. S26 landed: the census sits beside the mapper. What remains is the diff, under one constraint: the dry run must stay a read-only open that needs no key and creates no table (`tests/export/test_otlp__cli.py::test_a_dry_run_counts_without_a_backend` pins all three), while the fingerprints it needs live in `otlp_delivery`, a table `OtlpExporter.__init__` creates and a backend name selects.

## Call paths, current → proposed

Current:

- send: `cli._export_otlp` → `named_backend` → `open_trace_store(read_only=False)` → `OtlpExporter(backend, connection)` → `refresh(project, extractor=StoreSource, exporter=OtlpExporter)`
- dry run: `cli._export_otlp` → `cli._census_otlp` → `open_trace_store(read_only=True)` → `census_project(project, extractor=StoreSource)` → `census(traces)` → `session_spans`

Proposed — one `with`, one `refresh` call, two exporters:

- `cli._export_otlp` → `open_trace_store(read_only=args.dry_run)` → `DeliveryLedger(connection, backend=args.backend)` → the mode's exporter → `refresh(project, extractor=StoreSource(connection), exporter=…)`
- send: `named_backend` first, as today, then `OtlpExporter(backend, ledger, …)`; `export()` posts, then `ledger.record(…)`
- dry run: `OtlpCensus(ledger, text=…)`; `export()` runs `session_spans` and adds to `.counts`. `refresh` returns what would ship and what the ledger already holds; the CLI prints both

`census(traces, text)` stays as the pure count the tests use as their oracle. `census_project` is deleted: its loop is `refresh`.

## File-tree diff

```
src/hyphae/export/otlp_delivery.py     ~  DeliveryLedger split out of OtlpExporter, which now takes one; OtlpCensus beside it
src/hyphae/export/otlp.py              ~  Census.__add__; census_project deleted
src/hyphae/cli.py                      ~  _export_otlp opens once and picks the exporter; _census_otlp deleted
docs/otlp-export.md                    ~  the dry run previews the send to --backend, not the selection
CONTEXT.md                             ~  Census, Delivery ledger
tests/export/test_otlp__census.py      ~  census_project leaf rewritten onto refresh; a send-then-census leaf
tests/export/test_otlp__cli.py         ~  the dry-run leaf counts after a send and honours --backend
tests/export/test_otlp__delivery.py    ~  OtlpExporter constructions take a ledger
```

## Key contracts

- `DeliveryLedger(connection, *, backend: str)` — `fingerprints() -> dict[str, str]` for this backend at `MAPPER_VERSION`, `{}` when the store has no `otlp_delivery` table; `record(session_id, fingerprint, spans_sent)`; `create()` runs `check_shape` and the DDL. Only `OtlpExporter.__init__` calls `create()`. A backend name is the whole address: the ledger never sees a key
- `OtlpExporter(backend: Backend, ledger: DeliveryLedger, *, …)` — the connection argument goes; the ledger carries it
- `OtlpCensus(ledger: DeliveryLedger, *, text: TextPolicy)` — an `Exporter`; `counts: Census` after a refresh. `text` has no default: the CLI made that choice. `fingerprints()` delegates to the ledger, and `export()` records nothing of its own — `refresh` reads the fingerprints once before its loop, so an in-memory record would be unobservable (`testing_plan.md`)
- `Census.__add__`, so a census is a sum of per-session censuses and `census(traces)` is that sum as a loop
- `refresh()` and `RefreshResult` — untouched
- CLI line: `"{sessions} session(s) and {spans} span(s) would ship to {backend}, {compactions} of them compactions; {skipped} unchanged — nothing sent"`

## Chosen test seam

`refresh(project, extractor=StoreSource(connection), exporter=OtlpCensus(...))` over the fixture stores, with `census(traces)` on the same selection and the SQL `MAPPING` formula as the two oracles `test_otlp__census.py` already carries. The representative behaviour, over `delivered_db`: a census before any send counts both sessions; `deliver` one through the receiver; the next census counts one and skips one, and its `extracted` is the session the send would ship. The CLI leaf drives `cli.main("export-otlp", …, "--dry-run", "--backend", …)` and asserts the line, the untouched store and no request — today's pins plus the per-backend count.

## Slices

1. `DeliveryLedger` split out; `OtlpExporter` takes one. No behaviour change; `mise run check` green with only constructor edits in the delivery leaves
2. `OtlpCensus`, `Census.__add__`, `census_project` deleted; the census leaf rewritten onto `refresh` and the send-then-census leaf added. Verified by `test_otlp__census.py`
3. `_export_otlp` opens once and picks the exporter; `_census_otlp` deleted; the CLI dry-run leaf counts after a send and under a second `--backend`. Verified by `test_otlp__cli.py`
4. `docs/otlp-export.md` and `CONTEXT.md`, landed with doc-sync at PR time

## Decisions

- A census `Exporter`, not a mode of `refresh` — rejected: a `dry_run` flag or a `describe` callback on `refresh` (the `enrich/enricher.py:_pass` shape). Enrichment had to build its injection point; the pipeline already has one, and `refresh` passes the deletion test by having one job
- `OtlpCensus` beside `OtlpExporter` in `otlp_delivery.py`, not in `otlp.py` as the file-tree diff first said — rejected by the import direction: `otlp_delivery` imports the mapper, so a census in `otlp.py` naming a `DeliveryLedger` is a cycle. The two `Exporter`s over one ledger belong together anyway, and the mapper now imports nothing of the pipeline
- Split the ledger out of `OtlpExporter` — rejected: `OtlpExporter(send=False)`. A census would then need a `Backend`, which needs a key, which the dry run must not need. A ledger read by two adapters is a real seam; read by one it was a hypothetical one
- Diff against `--backend`'s ledger — rejected: keep counting the selection and rename the line. The operator's question is the run time and quota of the send they are about to start; the corpus size is one `hp query` away
- `fingerprints()` returns `{}` for a missing table — rejected: run the DDL on the dry-run path. It would take the write lock and leave a table behind, which the dry-run leaf forbids. `check_shape` already treats an absent table as not-drift
- Print `skipped` beside the count — rejected: the count alone. "6 would ship" with no "567 unchanged" reads like a shrunken corpus

## Out of scope

- Comparing against the backend: the ledger records what was acknowledged, not what a query there finds (`docs/otlp-export.md`), and the census inherits that
- A census for `hp extract`: `DuckDbExporter` is the store; its refresh needs no preview
- One connection for both modes: the dry run opens read-only on purpose
- M2 (one replay rule for compactions): the census counts the mapper's spans, so it follows M2 without an edit

## Open questions

- Does the ledger move to its own module rather than stay in `otlp_delivery.py`? Settled by whether the transport module's docstring — "never reads a store row" — still reads true with a named ledger class inside it
- Should `--dry-run` with a named backend refuse when that backend's key is missing, rehearsing the send's preflight? I kept the no-key dry run: the census answers a question one asks before having a key

## Glossary changes

Add under **Pipeline** in `CONTEXT.md`:

- **Census** — what `hp export-otlp --dry-run` prints: the sessions and spans a send to one backend would ship now, driven by the same `refresh()` with the posting swapped for a count
- **Delivery ledger** — `otlp_delivery`: what each backend acknowledged per session, and the fingerprints an OTLP send or census diffs against
