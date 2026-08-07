# `workflow/` — a session that fanned out into a parallel workflow

Redacted excerpt of `8d930c77-9e60-4784-9885-6d4c226280f7.jsonl`, **Claude Code 2.1.207**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Lines 1–6 of 210, in original order, plus part of the
session's directory.

The session was sourced for the workflow layout: `spine/` spawns subagents into `subagents/`, and no
recorded session of slice 1 puts an agent a directory deeper, beside a journal.

## What each file is here for

- the main transcript, lines 1–6 — the four editor-state records that open a transcript with neither
  a uuid nor a timestamp, then the two records of the first turn
- `subagents/workflows/wf_c30cc877-997/agent-a6f04bb0e6eff6013.jsonl` — one workflow agent's run,
  whole (the original is 6 records). Sourced by its bare `agentId`, exactly as a `subagents/` agent
  is: the extra directory changes where the file sits, not what the records are
- `.../journal.jsonl`, lines 1–4 of 186 — `started` and `result`, the two record types only a
  journal holds, with one agent's `started`/`result` pair and two more `started`s
- `.../agent-a6f04bb0e6eff6013.meta.json` — whole; a workflow agent's meta carries `agentType` and
  `spawnDepth` and nothing else, where a `subagents/` agent's also names the tool call that spawned it

The session's own `workflows/` directory — the definition and the script that drove the run — is not
here. Nothing reads those, and the test that proves the extractor ignores them plants files by name.

## Redaction

As `spine/` — see that README. The journal's `result` object is scrubbed field by field, keeping its
structure: an agent's answer is the same untrusted content a `tool_result` holds.
