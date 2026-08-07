# `fork_origin/` — a fork that replayed the transcript it was spawned from

Redacted excerpt of `5a88789c-1da7-4f32-b631-40a7e243334b.jsonl`, **Claude Code 2.1.215**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Lines 1–3 of 736, plus two of the session's
subagent transcripts.

The session was sourced for the copied-history fork. The auditor `acbc29008a04b9702` ran first;
the fork `a61a059e3610e6fb4` was spawned from it and its file opens with the auditor's records
copied verbatim, uuids and all, before it goes on to do work of its own. Both files therefore
hold the same message, and a count that assumes a uuid belongs to one transcript reports it
twice. 51 pairs of transcripts on this machine overlap that way, every one with a fork on at
least one side (scanned 2026-08-07).

The two open at the same timestamp — the fork's first record *is* the auditor's — so first-record
time cannot separate them. Spawn depth can: the auditor is at depth 1, the fork at depth 2, and a
copy is always spawned deeper than what it copied.

## What each file is here for

- the main transcript, lines 1–3 — the bookkeeping records that open a transcript. The session's
  own work is not in here; the two subagents are the point
- `subagents/agent-acbc29008a04b9702.jsonl`, lines 1–12 of 128 — the auditor: the prompt it was
  given, the message it answered with, and the tools that message issued. That message carries
  1,146 output tokens, which is what makes the rollup parity test discriminating
- `subagents/agent-a61a059e3610e6fb4.jsonl`, lines 1–13 and 43–49 of 165 — the fork: the 12
  records it copied from the auditor, then two messages of its own totalling 4,904 output tokens.
  The gap is a straight cut, so the fork's later records point back at parents this excerpt does
  not carry — the extractor reads uuids, not the chain
- both `.meta.json` files, whole but for a redacted `description`. The fork's carries
  `isFork: true` and `agentType: "fork"`; the auditor's carries neither

## Redaction

As `spine/` — see that README.
