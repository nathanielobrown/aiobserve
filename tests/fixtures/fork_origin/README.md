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
- `subagents/agent-acbc29008a04b9702.jsonl`, lines 1–12 of 128 plus the borrowed compaction below
  — the auditor: the prompt it was given, the message it answered with, and the tools that
  message issued. That message carries 1,146 output tokens, which is what makes the rollup parity
  test discriminating
- `subagents/agent-a61a059e3610e6fb4.jsonl`, lines 1–13 and 43–49 of 165, with the borrowed
  compaction inside the copied prefix — the fork: the 13 records it copied from the auditor, then
  two messages of its own totalling 4,904 output tokens. The gap is a straight cut, so the fork's
  later records point back at parents this excerpt does not carry — the extractor reads uuids,
  not the chain
- both `.meta.json` files, whole but for a redacted `description`. The fork's carries
  `isFork: true` and `agentType: "fork"`; the auditor's carries neither

## The borrowed compaction

One `system/compact_boundary` record is **borrowed**, because 5a88789c never compacted and the
shape it carries is the one the store's `compactions.replayed` column exists for: a compaction a
fork inherited with the prefix it copied. It sits in both subagent files, once as the auditor's
own record and once as the fork's copy of it — which is how Claude Code really writes the pair.

| Record | Source session | CC version | Shape it carries |
| --- | --- | --- | --- |
| `compact_boundary` `53858e9c-25e4-48a6-95d3-7f9baa5946de` | `ce02402d-f64d-4101-9d16-4e73b8fd99cc`, `agent-ab5d20dd7c636ae54.jsonl` line 121 and `agent-a366c7c1a464c9c09.jsonl` line 1 | 2.1.207 | one compaction in two of a session's transcripts: the run that compacted, and the fork spawned off it |

The two recorded copies are byte-identical but for `agentId`, which each file rewrites to its own
— so this fixture does the same. Rewritten besides that: `sessionId` to the host session,
`gitBranch` and `slug` to the host's pseudonyms, `content` to `[redacted]`, and the timestamp to
`2026-07-21T22:05:03.220Z`, which puts it after the last record the fork copied and before the
fork's own first one. The recorded timestamp is nine days before this session opened, and it
would have become the session's `started_at`. Everything else is as recorded, including the
`compactMetadata` numbers and the uuids inside it, which name records neither excerpt carries.

Four of the canonical store's 1,367 compactions are copies in this shape, spread over two
sessions — `ce02402d` above and `c7c4cae9` (measured 2026-08-30).

## Redaction

As `spine/` — see that README.
