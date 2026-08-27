# `compaction/` — a session that compacted twice on its main thread and once inside a run

Redacted excerpt of `1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9.jsonl`, **Claude Code 2.1.198**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Eight records, lines 381, 389–390, 410, 976, 989–990
and 1005 of the original: two of the session's 18 `system/compact_boundary` records, each with the
`isCompactSummary` user record it wrote and the api calls either side of it.

The two boundaries carry different triggers, which is the reason both are here:

- line 2 — `trigger: "manual"`, the operator asking for a compaction at 171,313 tokens
- line 6 — `trigger: "auto"`, Claude Code compacting at 222,837 tokens thirteen hours later

Each takes a little over two minutes, which is what `durationMs` is for.

## The calls around each boundary

A boundary is charged to the thread that hit it, and what a reader wants beside it is how full that
thread's context was — which is read from the nearest priced call before it. Four records carry
those calls:

| Line | Source line | What it carries |
| --- | --- | --- |
| 1 | 381 | the last call before the manual boundary: 168,373 read, 1,026 written |
| 4 | 410 | the first after it: nothing read back, 36,465 written — the rebuild a compaction forces |
| 5 | 976 | the last call before the auto boundary: 212,428 read, 1,699 written |
| 8 | 1005 | the first after it: 22,968 read, 19,740 written |

Two things ride on them. The session now names a model, so a per-thread context window has something
to look up — though all four answered on `claude-fable-5`, so this excerpt cannot show a thread
disagreeing with its session about which model to size against. And line 1 and line 4 are six hours
apart, a silence the recording holds (04:15 to 10:16 on 2026-07-02, with nothing between them), so
the main thread's rebuild is one of the corpus's three idle reloads.

One of the four carries `attributionSkill: "manager"`, kept the way `spine/` keeps its skill names —
a skill name is what the operator called a file, not something an agent wrote.

## What the tests read

`compactMetadata` carries `trigger`, `preTokens`, `postTokens` and `durationMs` on every boundary in
the corpus, so a `Compaction` reads them without a default. The summary records are here for the
count: boundaries and `isCompactSummary` records run 1:1, which is the check that lets the design
drop the prior importer's nearest-assistant-call inference.

## Redaction

Every string outside the structural keep-list is `[redacted]`; the `compactMetadata` scalars and the
uuid lists it carries are kept, since they are the record's whole content, as are `model`,
`stop_reason` and the usage numbers of the four calls. `gitBranch` is pseudonymised. No prompt,
summary text, or file path survives.

## The agent run that compacted

`1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9/subagents/agent-a003de2a5c1985f71.jsonl` is a five-record
excerpt of the same session's `general-purpose` run — lines 1, 78, 82, 83 and 84 of its 85 — and the
corpus's only `compact_boundary` outside `main`:

| Line | Source line | What it carries |
| --- | --- | --- |
| 1 | 1 | the run's opening prompt, so the thread has a start |
| 2 | 78 | the last call before the boundary: 214,775 read, 1,469 written, on `claude-opus-4-8` |
| 3 | 82 | the boundary: `trigger: "auto"`, 240,349 → 16,918, 119,332 ms |
| 4 | 83 | the `isCompactSummary` record it wrote |
| 5 | 84 | the rebuild after it: 7,806 read, 33,072 written |

Source records 79–81 are dropped. One of them is a `Write` tool call, and the recorded corpus holding
no edit call at all is what a test relies on to bound an absence
(`tests/analyze/test_shapes.py::test_the_editing_shapes_need_edit_calls_no_fixture_recorded`). Record
78 shares its message id with 80, so the priced call before the boundary survives the cut anyway.

The `Agent` call that spawned the run is outside the excerpt, so the run lands in the viewer's
Unattached bucket — the same shape `parallel_tools/` has. Line 2 and line 5 are 302 seconds apart,
the shortest silence the corpus holds over the idle floor, and the rebuild is 81% of the context, so
it stays under the 90% detector and is a gap without being a reload.

Redaction as above. The run's `agentType`, `toolUseId` and `spawnDepth` are structural and kept, its
`description` is `[redacted]`, and its `gitBranch` is pseudonymised.
