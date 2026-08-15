# Shipping traces to an OTLP backend

`aiobserve export-otlp` sends sessions from the trace store to any OTLP/HTTP backend as spans. This lets you point a waterfall view and the backend's query language at the same corpus that local queries read. Read this before you run it against a backend anyone else can see.

```
aiobserve export-otlp /path/to/repo --db data/traces.duckdb --backend honeycomb
```

The decisions behind it, and the importer it replaces, are in [the OTLP export design](../plans/otlp-export/design.md).

The store is the source, not the transcripts on disk, so a session Claude Code has pruned still ships. DuckDB admits one writer, so an export cannot run beside `extract` or `enrich` — a second run fails fast on the lock instead of waiting.

The command resolves the project argument before matching it against the recorded `cwd`s, so a relative path or a quoted `~/…` names the repository a shell would. If the store holds no session for that project, the command stops, whether sending or running dry. The command promises a corpus, so an empty one means a mistyped path rather than a clean delivery of nothing.

## Where it ships

`--backend` picks an entry from `BACKENDS` in `src/aiobserve/export/otlp.py`. Each entry holds the backend's endpoint, the variable that supplies its key, and the header that carries the key. `generic` is the base case: any OTLP/HTTP endpoint named by `OTLP_ENDPOINT`, with `OTLP_HEADERS` carrying `name=value` pairs. `OTLP_ENDPOINT` also overrides a named backend's endpoint, letting a run reach a collector in front of that backend.

Keys come from `.env` or the environment. The command validates them before opening the store and never prints them. The backend name is also the ledger key, so the ledger tracks two backends separately when you ship the same corpus to both.

Spans leave at 300 per second (`--rate`). The importer this replaces measured a backend answering 200 while dropping about 40% of the spans it took at 2,575 spans/s, and nothing on our side can detect that loss. The limit mitigates it and puts a full corpus backfill at tens of minutes. Requests are gzipped protobuf, up to 2,000 spans each.

## Counting before you send

```
aiobserve export-otlp /path/to/repo --dry-run
```

`--dry-run` shapes every session and reports how many spans a send would put on the wire, including how many are compactions. No store query can reproduce the compaction count because `live_compactions` keeps the copies a fork inherited and the mapper drops them. It needs no backend or key, opens the store read-only, and writes nothing. Use it to size a backfill against a backend's ingest quota.

It is also a check: if the replay rule cannot separate a session's duplicated compactions, the count crashes instead of reporting a number that the send would not match.

## What leaves the machine

Metadata only. Nothing the user typed or the model wrote is sent: no prompt, no response text, no thinking, no tool input or result, no session title, no PR URL. What ships is the shape of the work — ids, timings, models, token counts, cost, stop reasons — and the resource identifies the project and exporter version it came from. `session_spans()` in `src/aiobserve/export/otlp.py` defines the whole list, and `tests/export/test_otlp__privacy.py` sweeps the raw request bytes for every excluded field.

A session becomes a root span with one child per turn, model call, tool call, subagent run and compaction. PR links ride the root as events. A tool call that started a subagent becomes that subagent's span instead of a separate tool-call span. Rows that a fork copied from its parent's transcript emit nothing because they would double-count in every backend aggregation.

`--include-text` opts in the excluded fields, cut to `--max-chars` (500 by default). Truncation is not redaction — a credential fits in 200 characters — so this is a flag, not the default.

## Delivered at least once, never diffed

The exporter posts a session whole and records it only after the backend confirms every batch. A failure records nothing, so the next run sends that session again. Nothing compares what has already landed with what is about to be sent. That machinery was the largest bug source in the importer this replaces. As a result, a backend that ignores span identity will hold duplicates.

Identity keeps a re-send a re-send: every span id is a sha256 digest of the session, the kind of row, and its natural id, so shaping the same session twice names the same spans. A backend that collapses on span id sees an update; one that does not sees a copy.

The ledger lives in `otlp_delivery` in the store itself. It is keyed by session and backend and carries the shipped fingerprint and the `MAPPER_VERSION` that shaped it. A change to either makes the session undelivered again, so a re-extract re-ships it and a change to span shaping re-ships the corpus.

## Two things worth knowing before you run it

**The ledger dies with the store.** It is a table in `traces.duckdb`, and the remedy for a `SCHEMA_VERSION` bump is to extract into a fresh store ([the store guide](store.md)). That erases every delivery row, and the next export re-sends the whole corpus to every backend. The direction is safe — duplication, never loss — but the cost is a full backfill.

**A rejected span stops the run, every run.** If a backend accepts the request but reports that it kept only part of it, the export crashes and records nothing. It crashes again next time, and sessions behind it never ship. That is deliberate. A deterministic rejection is a bug in what we send. No flag skips past it, and the fix is a mapper change — whose `MAPPER_VERSION` bump then re-sends everything.
