# The trace store

`aiobserve extract` writes session traces into one DuckDB file. There is one canonical store, `data/traces.duckdb` — gitignored, like everything under `data/`. Read this before you delete a store, move one, or bump a version constant.

## What it holds

```mermaid
erDiagram
    sessions ||--o{ turns : "ran"
    sessions ||--o{ agent_runs : "spawned"
    sessions ||--o{ compactions : "hit"
    sessions ||--o{ pr_links : "opened"
    sessions ||--o{ raw_records : "archived"
    sessions ||--o{ offload_files : "archived"
    sessions ||--|| extract_state : "fingerprinted by"
    sessions ||--o{ otlp_delivery : "shipped per backend"
    turns ||--o{ api_calls : "drove"
    api_calls ||--o{ tool_calls : "asked for"
    tool_calls ||--o| agent_runs : "started"
    tool_calls ||--o| offload_files : "offloaded its result to"
    agent_runs ||--o{ agent_runs : "spawned"
```

The columns live in `_SCHEMA` in `src/aiobserve/export/duckdb.py`. [The schema guide](schema.md) explains what each telemetry field means and cites the session that proves it. A session's own thread and each of its agent runs share these tables. The `source` column tells them apart, so a turn or a call is keyed by `(session_id, source, id)`.

Queries don't read these tables directly. `_VIEWS` in the same module derives the `live_*` views, which drop the records a fork replayed. The `corpus_*` views also drop records already held by an earlier session. A resume copies its ancestor's records verbatim, so counting both doubles the corpus. `session_rollups` and `corpus_rollups` roll each family up to one row per session.

[Enrichment](enrichment.md) writes into the same file: three `*_enrichments` tables keyed one-to-one onto sessions, turns and agent runs, plus the views that join them back on. A store no pass has touched holds none of them, so a query over them fails saying so.

## The store is the archive, not a cache

Claude Code prunes a session's transcript and its `tool-results/` files from disk after a few weeks. This constraint shaped the pipeline ([the trace-pipeline design](../plans/trace-pipeline/design.md)). Every line of every file goes into the store: `raw_records` holds the transcripts, and `offload_files` holds the tool outputs Claude Code moved out of them. Once a session's files are gone, the store is its only copy. A refresh keeps such a session's rows rather than mirroring what is on disk.

Deleting a store therefore destroys sessions no re-extract can recover. Re-parsing a pruned session out of its archived `raw_records` is possible but not built; the design lists it under out of scope.

## A version bump re-extracts in place

Each session's fingerprint includes `EXTRACTOR_VERSION` (`src/aiobserve/extract/claude_code.py`), so raising it makes the next refresh re-extract every session whose files are still on disk into the same store. You don't need to delete anything, and pruned sessions keep whatever the parser of their day produced.

`SCHEMA_VERSION` (`src/aiobserve/export/duckdb.py`) has no migrations while the project is early. When you open a store written by an older schema, the program refuses before reading or writing a single table and tells you to extract into a fresh one. Deleting the old store is a separate decision — run the check below first.

A fresh store also starts with an empty `otlp_delivery`, the table `export-otlp` writes to record what a backend confirmed. The next export therefore re-sends every session to every backend ([the OTLP export guide](otlp-export.md)).

## Check the session set before deleting a store

An older store is safe to delete once the canonical store holds every session from the older store:

```sql
ATTACH 'data/traces.duckdb' AS canonical (READ_ONLY);
ATTACH 'old.duckdb'        AS old       (READ_ONLY);
SELECT id FROM old.sessions EXCEPT SELECT id FROM canonical.sessions;
```

No rows means every session in the old store was re-extracted into the canonical one. A row identifies a session the canonical store has never heard of — usually one whose files Claude Code has since pruned, which makes the old store its only home.

The check compares session ids, not rows. It answers "would deleting this lose a session?", which is the question after a re-extract from the same files.
