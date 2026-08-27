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

## The runs the messages address

The excerpt's three `SendMessage` calls carry a `to` naming a run of this session by id: two address
`a43bfe9fc86734ff1` and `aa52d3fe48cec7f58`, one addresses the second again. That id is a lookup, not
a label — it resolves to an `agent_runs` row and prints as that run's `agent_type` — so both runs
have an opening excerpt under `5f4b59fb-.../subagents/`, enough to give the id a row to resolve
against:

- `agent-a43bfe9fc86734ff1.jsonl`, lines 1 and 4–6 of 194 — `agentType: general-purpose`. Its
  prompt, one api call on `claude-fable-5` split over two records, and the result answering it
- `agent-aa52d3fe48cec7f58.jsonl`, lines 1–4 of 154 — `agentType: auditor`. Its prompt, one api call
  issuing two tools from one record, and both results

Each `.meta.json` came whole but for a redacted `description`. The `toolUseId` each names is the
`Agent` call that spawned it, and neither of those calls is in this excerpt: the runs are here to be
addressed, not to be spawned, and the extractor places a run from its `meta.json` either way.

## Redaction

As `spine/` — see that README — with one tightening and one loosening, since this excerpt's calls
carry agent addressing. Tightening: every string under `toolUseResult`, and every string under a
tool's `input` other than `to`, is `[redacted]` whatever its key, so no agent name, prompt or path
survives. Loosening: `SendMessage`'s `to` is kept as recorded.

**The sensitivity call.** A run id is an opaque token Claude Code minted for one session — no path,
no prompt, no credential, and meaningless outside a transcript the store already keeps whole. The
`agent_type` it resolves to is a role word out of the repo's own `.claude/agents/`. `summary`,
`message` and every other string under `input` stay redacted, because those are prose an agent wrote.

`gitBranch` and `slug` are pseudonymised. Session ids, uuids, timestamps, tool names, tool_use ids
and usage numbers are as recorded.
