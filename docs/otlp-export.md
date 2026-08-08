# Shipping traces to an OTLP backend

`aiobserve export-otlp` sends the sessions already in the trace store to any OTLP/HTTP backend as spans, so a waterfall view and a backend's query language can be pointed at the same corpus the local queries read. Read this before you run it against a backend anyone else can see.

```
aiobserve export-otlp /path/to/repo --db data/traces.duckdb
```

The store is the source, not the transcripts on disk, so a session Claude Code has pruned still ships. `OTLP_ENDPOINT` says where, and `OTLP_HEADERS` carries `name=value` pairs for the key; both come from `.env` or the environment, and a run with no endpoint refuses before it opens the store. DuckDB admits one writer, so an export cannot run beside `extract` or `enrich` — a second run fails fast on the lock rather than waiting.

## What leaves the machine

Metadata only. Nothing the user typed or the model wrote is sent: no prompt, no response text, no thinking, no tool input or result, no session title, no PR URL. What ships is the shape of the work — ids, timings, models, token counts, cost, stop reasons — and the resource says which project and which exporter version it came from. `session_spans()` in `src/aiobserve/export/otlp.py` is the whole list, and `tests/export/test_otlp__privacy.py` sweeps the raw request bytes for every excluded field.

Today a session becomes a root span, one span per turn, and one per model call. Tool calls, subagent runs, compactions and PR events follow, along with the flags that opt text in.

## Delivered at least once, never diffed

A session is posted whole and recorded only after the backend confirms every batch. A failure records nothing, so the next run sends that session again. Nothing compares what already landed against what is about to be sent — that machinery was the largest bug source in the importer this replaces — which means a backend that ignores span identity will hold duplicates.

Identity is what keeps a re-send a re-send: every span id is a sha256 digest of the session, the kind of row, and its natural id, so shaping the same session twice names the same spans. A backend that collapses on span id sees an update; one that does not sees a copy.

The ledger lives in `otlp_delivery` in the store itself, keyed by session and backend, and carries the fingerprint that was shipped and the `MAPPER_VERSION` that shaped it. Either one moving makes the session undelivered again, so a re-extract re-ships it and a change to span shaping re-ships the corpus.

## Two things worth knowing before you run it

**The ledger dies with the store.** It is a table in `traces.duckdb`, and a `SCHEMA_VERSION` bump's remedy is to extract into a fresh store ([the store guide](store.md)). That erases every delivery row, and the next export re-sends the whole corpus to every backend. The direction is safe — duplication, never loss — but the cost is a full backfill.

**A rejected span stops the run, every run.** A backend that accepts the request and reports it kept only part of it crashes the export, records nothing, and crashes again next time; sessions behind it never ship. That is deliberate. A deterministic rejection is a bug in what we send, no flag skips past it, and the fix is a mapper change — whose `MAPPER_VERSION` bump then re-sends everything.
