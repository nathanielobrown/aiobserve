# `parallel_tools/` — the two shapes of a parallel tool batch

Redacted excerpt of `5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515.jsonl`, **Claude Code 2.1.211**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 9 records, all from that session's main transcript,
in recorded order with their own timestamps and uuids.

One message issues both its calls from a single record; another, eight minutes later, issues one
call per record, 19 seconds apart. Both are recorded shapes — 23 records of the mycelia corpus hold
two or more `tool_use` blocks (`docs/schema.md`) — and they are here together because the batch
rule reads *records*: blocks sharing a record were issued in one moment and keep that record's measured start,
while separate records rank the calls by the order Claude Code got round to running them.

| Line | Source line | Why it is here |
| --- | --- | --- |
| 1 | 445 | the prompt that opens the turn, so the calls hang off a turn like any other |
| 2 | 447 | `msg_011Cd6RyHnMi8h4ZAceminTf` whole: thinking, text, and **two `SendMessage` calls in one record** |
| 3–4 | 448–449 | the two `tool_result` records answering them |
| 5 | 502 | `msg_011Cd6SbrBGHDLxr2oKBJZCf` opening with thinking alone |
| 6 | 503 | that message's first call, `SendMessage`, in a record of its own |
| 7 | 504 | its result |
| 8 | 505 | the same message's second call, `Agent`, 19s after the first — the queue position a per-record start would report as duration |
| 9 | 506 | its result |

## Redaction

As `spine/` — see that README — with two tightenings, since this excerpt's calls carry agent
addressing: every string under a tool's `input` or under `toolUseResult` is `[redacted]` whatever its
key, so no agent id or run name survives. `gitBranch` and `slug` are pseudonymised. Session ids,
uuids, timestamps, tool names, tool_use ids and usage numbers are as recorded.
