# Export stored traces over OTLP

`hp export-otlp` sends sessions from the trace store to an OTLP/HTTP backend as spans. Use a dry run first, then review the data policy and delivery limits below before sending traces to a shared backend.

## Preview the exact export

```console
hp export-otlp /path/to/repo --db data/traces.duckdb --dry-run
```

A dry run counts what the next export would ship, shaping each session with the same mapper the export uses and posting nothing. It counts the sessions the backend has not already acknowledged, not the whole corpus, and says how many it would skip. Name another backend with `--backend` to count against that backend's ledger; a dry run needs no key for it. It opens the store read-only, writes no delivery record, and sends no request.

Compactions are broken out because a compaction is where a session's account of itself gets lossy, so how many will ship is worth seeing before an hour of sending. Against a backend that holds nothing yet, the count matches `live_compactions`: the mapper ships each session's `SessionTrace.live()` rows, which is what the `live_*` views hold ([the store guide](store.md)).

Use the result to check the backend's ingest quota and estimate the run time at your chosen `--rate`.

## The trace store defines the corpus

The exporter reads the store, not Claude Code's transcript files. It can therefore send sessions that Claude Code has pruned from disk, and the remote corpus matches the one local queries use.

The project argument selects sessions by their recorded working directory. The command expands `~` and resolves relative paths before matching. It stops if the store holds no session for that project, which catches a mistyped path instead of reporting a successful export of nothing.

A real export reads sessions and writes its delivery ledger through one DuckDB connection, held for the whole run. DuckDB admits one writer, so nothing else can write while an export runs: an `extract` or `enrich` beside it queues for the store and then gives up naming the process holding it ([the store guide](store.md)).

## Configure one destination

The default `generic` backend sends to `OTLP_ENDPOINT`. Set optional request headers in `OTLP_HEADERS` as comma-separated `name=value` pairs:

```console
hp export-otlp /path/to/repo --db data/traces.duckdb
```

Named backends and their key variables live in `BACKENDS` in `src/hyphae/export/otlp_delivery.py`; `--help` lists the accepted names. For example:

```console
hp export-otlp /path/to/repo --db data/traces.duckdb --backend honeycomb
```

Keys come from `.env` or the environment. The command validates the endpoint and required key before opening the store, and it never prints keys. `OTLP_ENDPOINT` overrides the endpoint of a named backend, which lets you put a collector in front of it.

The backend name also identifies its delivery ledger. Sending the same sessions to two named backends creates separate delivery records. All generic endpoints share the `generic` identity, so changing `OTLP_ENDPOINT` alone does not make a session eligible to send again.

## Transcript text stays local by default

The default export sends the structure of the work: span ids and times, project metadata, model and tool names, token counts, costs, stop reasons, agent types, command names, and PR numbers. This metadata is not anonymous; it includes such values as the project path, Git branch, session ids, and request ids.

The default omits transcript-derived text: prompts, command arguments, model responses and thinking, tool inputs and results, session titles, session agent names, subagent briefs, PR URLs, and repository names. PR links still become events on the session root, but those events contain only the PR number by default.

Each session becomes one trace. Its root span has children for turns, model calls, tool calls, subagent runs, and compactions. A tool call that starts a subagent becomes the subagent span rather than a second tool span. Rows copied into a fork emit no span because sending them would double-count the work.

`session_spans()` in `src/hyphae/export/otlp.py` defines what ships. `tests/export/test_otlp__privacy.py` scans the raw request bytes for every excluded field.

Use `--include-text` only when you intend to publish transcript content to the backend. It adds the excluded fields and cuts each one to `--max-chars`, which defaults to 500. Truncation limits size; it does not redact secrets.

## Keep the default rate unless the backend proves it can take more

The exporter sends 300 spans per second by default. The prior importer saw a backend return HTTP 200 while silently dropping about 40% of spans at 2,575 spans per second. A successful request therefore does not prove that every span was persisted, and the exporter cannot detect this kind of server-side loss.

Override the limit with `--rate` only after testing the backend at that rate. Requests use gzipped protobuf and contain at most 2,000 spans.

## Delivery is at least once

The exporter sends a session whole and writes its `otlp_delivery` row only after every batch returns success with no rejected spans. If a batch fails or the run stops, that session gets no delivery row and the next run sends it again. Sessions confirmed before the failure remain recorded.

A resend uses the same trace and span ids. Each id is derived from the session id, row kind, and the row's natural id. A backend that deduplicates those ids can treat the send as an update; a backend that does not will store another copy. The exporter does not compare remote spans with the next payload.

The `otlp_delivery` table is keyed by session and backend. Each row stores the shipped session fingerprint and the `MAPPER_VERSION` that shaped it. A changed fingerprint resends that session. A changed mapper version resends every selected session because their old delivery rows no longer count as current.

This ledger records what the backend acknowledged, not what a later query can find there. Backend-side delivery verification is not built.

## Replacing the store also replaces the ledger

The ledger lives inside `traces.duckdb`. If a `SCHEMA_VERSION` change requires a fresh store, the new store has no delivery history. The next export to a backend resends the selected corpus to that backend. This favors duplication over loss, but it can trigger a full backfill. Read [the store guide](store.md) before replacing a store.

## A rejected span blocks the sessions behind it

If a backend accepts a request but reports rejected spans, the exporter stops and records no delivery for that session. The same session will fail again on the next run, so later sessions cannot ship until the cause is fixed.

There is no skip or quarantine flag. A deterministic rejection means the mapper produced data the backend will not accept. Fix the mapper and bump `MAPPER_VERSION`; the next run then reshapes and resends the corpus.

The design choices and the failure record from the importer this command replaced are in [the OTLP export design](../plans/otlp-export/design.md).
