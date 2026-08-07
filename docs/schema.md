# Telemetry schema

What a field in an AI coding session means and where it comes from. Read this before writing a query or an analysis — a wrong reading of a field produces a confident wrong finding.

**This document is a stub.** The span schema arrives with the importer, extracted from `mac_settings/claude-otel/` in a later step. Until then, do not describe fields here from memory.

## The rule for adding a field

Every entry names a **recorded session** that demonstrates the field, and the **Claude Code version** that produced it. The harness owns these shapes and changes them without notice, so a field documented from memory is a guess that reads like a fact.

| Field | Where it comes from | What it means | Seen in |
| --- | --- | --- | --- |
| _(none yet)_ | | | |

## Sources of session data

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — the transcript of one session, one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl` — one subagent the session spawned, beside an `agent-<id>.meta.json`. A parallel fan-out nests them another level, under `subagents/workflows/wf_<id>/`. `aiobserve.sessions` walks this tree
- Claude Code's own OpenTelemetry export — a thinner, live schema. Enabled per-machine, not per-repo.

The encoded directory name is the session's working directory with each `/` replaced by `-`, so `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The tree is shared across Claude accounts: `~/.claude-black/projects` is a symlink to `~/.claude/projects`, so a transcript's path does not tell you which account produced it.
