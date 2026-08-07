# `compaction/` — a session that compacted its own context twice

Redacted excerpt of `1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9.jsonl`, **Claude Code 2.1.198**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Four records, lines 389–390 and 989–990 of the
original: two of the session's 18 `system/compact_boundary` records, each with the
`isCompactSummary` user record it wrote.

The two boundaries carry different triggers, which is the reason both are here:

- line 1 — `trigger: "manual"`, the operator asking for a compaction at 171,313 tokens
- line 3 — `trigger: "auto"`, Claude Code compacting at 222,837 tokens thirteen hours later

Each takes a little over two minutes, which is what `durationMs` is for.

## What the tests read

`compactMetadata` carries `trigger`, `preTokens`, `postTokens` and `durationMs` on every boundary in
the corpus, so a `Compaction` reads them without a default. The summary records are here for the
count: boundaries and `isCompactSummary` records run 1:1, which is the check that lets the design
drop the prior importer's nearest-assistant-call inference.

## Redaction

Every string outside the structural keep-list is `[redacted]`; the `compactMetadata` scalars and the
uuid lists it carries are kept, since they are the record's whole content. `gitBranch` is
pseudonymised. No prompt, summary text, or file path survives.
