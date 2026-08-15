# Shipping traces to an OTLP backend

`aiobserve export-otlp` sends the sessions already in the trace store to any OTLP/HTTP backend as spans, so a waterfall view and a backend's query language can be pointed at the same corpus the local queries read. Read this before you run it against a backend anyone else can see.

```
aiobserve export-otlp /path/to/repo --db data/traces.duckdb --backend honeycomb
```

The store is the source, not the transcripts on disk, so a session Claude Code has pruned still ships. DuckDB admits one writer, so an export cannot run beside `extract` or `enrich` — a second run fails fast on the lock rather than waiting.

The project argument is resolved before it is matched against the recorded `cwd`s, so a relative path or a quoted `~/…` names the repository a shell would. A project the store holds no session under stops the run, send or dry run: the command promises a corpus, so an empty one is a mistyped path rather than a clean delivery of nothing.

## Where it ships

`--backend` picks one of the entries in `BACKENDS` in `src/aiobserve/export/otlp.py`, which holds each backend's endpoint, the variable its key comes from, and the header that key travels in. `generic` is the base case: any OTLP/HTTP endpoint, named by `OTLP_ENDPOINT`, with `OTLP_HEADERS` carrying `name=value` pairs. `OTLP_ENDPOINT` also overrides a named backend's endpoint, which is how a run reaches a collector standing in front of one.

Keys come from `.env` or the environment, are validated before the store is opened, and are never printed. The backend name is also the ledger key, so shipping the same corpus to two backends tracks each separately.

Spans leave at 300 per second (`--rate`). The importer this replaces measured a backend answering 200 while dropping about 40% of what it took at 2,575 spans/s, and nothing on our side can see that happen; the limit is the mitigation, and it puts a full corpus backfill at tens of minutes. Requests are gzipped protobuf, up to 2,000 spans each.

## Counting before you send

```
aiobserve export-otlp /path/to/repo --dry-run
```

`--dry-run` shapes every session and says how many spans a send would put on the wire, and how many of them are compactions — the one count no query of the store reproduces, since `live_compactions` keeps the copies a fork inherited and the mapper drops them. It needs no backend and no key, opens the store read-only, and writes nothing. Use it to size a backfill against a backend's ingest quota.

It is also a check: a session whose duplicated compactions the replay rule cannot separate crashes the count rather than reporting a number the send would not match.

## What leaves the machine

Metadata only. Nothing the user typed or the model wrote is sent: no prompt, no response text, no thinking, no tool input or result, no session title, no PR URL. What ships is the shape of the work — ids, timings, models, token counts, cost, stop reasons — and the resource says which project and which exporter version it came from. `session_spans()` in `src/aiobserve/export/otlp.py` is the whole list, and `tests/export/test_otlp__privacy.py` sweeps the raw request bytes for every excluded field.

A session becomes a root span with one child per turn, model call, tool call, subagent run and compaction, and PR links ride the root as events. A tool call that started a subagent becomes that subagent's span rather than one of its own, and rows a fork copied out of its parent's transcript emit nothing — they would double-count in every backend aggregation.

`--include-text` opts the excluded fields in, cut to `--max-chars` (500 by default). Truncation is not redaction — a credential fits in 200 characters — which is why it is a flag and not the default.

## Delivered at least once, never diffed

A session is posted whole and recorded only after the backend confirms every batch. A failure records nothing, so the next run sends that session again. Nothing compares what already landed against what is about to be sent — that machinery was the largest bug source in the importer this replaces — which means a backend that ignores span identity will hold duplicates.

Identity is what keeps a re-send a re-send: every span id is a sha256 digest of the session, the kind of row, and its natural id, so shaping the same session twice names the same spans. A backend that collapses on span id sees an update; one that does not sees a copy.

The ledger lives in `otlp_delivery` in the store itself, keyed by session and backend, and carries the fingerprint that was shipped and the `MAPPER_VERSION` that shaped it. Either one moving makes the session undelivered again, so a re-extract re-ships it and a change to span shaping re-ships the corpus.

## Two things worth knowing before you run it

**The ledger dies with the store.** It is a table in `traces.duckdb`, and a `SCHEMA_VERSION` bump's remedy is to extract into a fresh store ([the store guide](store.md)). That erases every delivery row, and the next export re-sends the whole corpus to every backend. The direction is safe — duplication, never loss — but the cost is a full backfill.

**A rejected span stops the run, every run.** A backend that accepts the request and reports it kept only part of it crashes the export, records nothing, and crashes again next time; sessions behind it never ship. That is deliberate. A deterministic rejection is a bug in what we send, no flag skips past it, and the fix is a mapper change — whose `MAPPER_VERSION` bump then re-sends everything.
