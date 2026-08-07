# The trace store

`aiobserve extract` writes session traces into one DuckDB file. There is one canonical store, `data/traces.duckdb` — gitignored, like everything under `data/`. Read this before you delete a store, move one, or bump a version constant.

## The store is the archive, not a cache

Claude Code prunes a session's transcript and its `tool-results/` files from disk after a few weeks, which is the constraint the pipeline was built around ([the trace-pipeline design](../plans/trace-pipeline/design.md)). Every line of every file goes into the store — `raw_records` holds the transcripts, `offload_files` the tool outputs Claude Code moved out of them — so once a session's files are gone, the store is the only copy of it. A refresh keeps such a session's rows rather than mirroring what is on disk.

Deleting a store therefore destroys sessions no re-extract can recover. Re-parsing a pruned session out of its archived `raw_records` is possible but not built; the design lists it under out of scope.

## A version bump re-extracts in place

`EXTRACTOR_VERSION` (`src/aiobserve/extract/claude_code.py`) is folded into each session's fingerprint, so raising it makes the next refresh re-extract every session whose files are still on disk, into the same store. Nothing needs deleting, and pruned sessions keep whatever the parser of their day produced.

`SCHEMA_VERSION` (`src/aiobserve/export/duckdb.py`) has no migrations while the project is early. Opening a store an older schema wrote refuses before it reads or writes a single table, and says to extract into a fresh one. Deleting the old store is a separate decision — run the check below first.

## Check the session set before deleting a store

An older store is safe to delete once the canonical store holds every session it holds:

```sql
ATTACH 'data/traces.duckdb' AS canonical (READ_ONLY);
ATTACH 'old.duckdb'        AS old       (READ_ONLY);
SELECT id FROM old.sessions EXCEPT SELECT id FROM canonical.sessions;
```

No rows means every session in the old store was re-extracted into the canonical one. A row is a session the canonical store has never heard of — usually one whose files Claude Code has since pruned, which makes the old store its only home.

The check compares session ids, not rows. It answers "would deleting this lose a session?", which is the question after a re-extract from the same files.
