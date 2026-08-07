# `spine/` — the slice-1 whole-object fixture

Redacted excerpt of `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl`, **Claude Code 2.1.221**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 25 records, drawn from lines 1–3, 5–7, 9, 16, 19–24,
123, 126, 140 and 895–900 of the original, in original order.

Four records are **borrowed** from other sessions because 4208c1bd contains no instance of the shape.
Their `sessionId`/`session_id` were rewritten to the host session so the file parses as one session;
everything else is as recorded, including timestamps and Claude Code versions from the day they were
written:

| Records | Source session | CC version | Shape it carries |
| --- | --- | --- | --- |
| `<bash-input>`, `<bash-stdout>` | `64cca9e3-00b3-4faf-8c28-0ae6b3d5f789` lines 37–38 | 2.1.212 | machine tags that are never turns |
| block-content text prompt | `2d1b86d1-dedb-4789-83b3-c2bb763627cc` line 18 | 2.1.220 | a turn whose content is blocks, not a string |
| `<teammate-message>` | `10d0349d-0705-4e23-aa64-5b1b97698b2e`'s architect subagent, line 1 | 2.1.211 | a turn tag that carries attributes, and the only tag exclusive to subagent transcripts |

## What each record is here for

- lines 1–3 — bookkeeping types that carry no `cwd`, so `project_dir` must come from the first record that does
- `<local-command-caveat>` — `isMeta`, so filtered before the tag registry ever sees the tag
- two slash-command records — one leading with `<command-name>`, one with `<command-message>`; both orderings occur
- `<local-command-stdout>`, `<task-notification>`, `<bash-input>`, `<bash-stdout>` — archived, never turns
- `msg_011CdmMjFXDofyYSMxYtXa5n` — five assistant chunks sharing one `message.id`, interleaved with a
  `tool_result` user record, carrying `attributionSkill`, `effort` and `stop_reason`
- `msg_011Cdmz3NQtuzwN3cqYvvkuN` — three chunks with no `attributionSkill`. Its lone tool call ends
  the excerpt: the original answered it, and cutting the answer gives the shape of a session killed
  mid-call
- two `system/turn_duration` records — `active_ms` is their sum
- a plain-string prompt and a block-content prompt — the two turn-opening shapes

## The session directory

`4208c1bd-.../subagents/` holds one of the session's subagents, `agent-ac461ef46b4bb8e32.jsonl`:
lines 1–6 of 43, **Claude Code 2.1.221**. It carries `isSidechain: true` and an `agentId`, the two
fields that place a record under a source other than `main`, and its `cwd` is the worktree the
subagent ran in rather than the session's. The borrowed `<teammate-message>` record closes the file,
chained onto its last record: a teammate's instruction arrives mid-run and opens a second turn.

Its `.meta.json` came too, `description` redacted and the rest as recorded — it is the linkage from a
subagent back to the `toolUseId` that spawned it, which agent runs read.

## Redaction

Every string outside a small keep-list of structural fields is `[redacted]`. Kept: record and message
types, uuids, timestamps, session ids, `version`, `model`, `stop_reason`, `requestId`, `effort`,
`attributionSkill`, usage numbers, tool names and tool_use ids, and the slash-command *names*.
`gitBranch` and `slug` are pseudonymised to `fixture-branch-N` / `fixture-slug-N`, preserving which
records shared a value. No prompt text, tool input, tool output, thinking, or file path survives.
