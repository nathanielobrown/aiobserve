# `server_tools/` — tools Anthropic ran server-side, and a model fallback

Redacted excerpt built on `088d63aa-71d3-4108-965e-5147e3eaddbd.jsonl`, **Claude Code 2.1.201**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 8 records in the main transcript and 7 in one subagent
transcript. `sessionId`/`session_id` on every borrowed record was rewritten to the host session so the
files parse as one session; everything else is as recorded, timestamps and Claude Code versions
included.

The session was sourced for the shapes the extractor was blind to before version 7: `server_tool_use`
(45 blocks in the corpus), `advisor_tool_result` (44) and `fallback` (3), scanned 2026-08-07. Every
recorded `server_tool_use` names the `advisor` tool and takes no arguments.

## Main transcript

| Line | Source | Why it is here |
| --- | --- | --- |
| 1 | 088d63aa line 13 | the prompt that opens the turn, so the calls hang off a turn like any other |
| 2–5 | 088d63aa lines 19–22 | one message (`msg_015jtFKf3C8FjiYD3M2JT27H`): thinking, the `server_tool_use` `srvtoolu_01TK5pPoxEdDu3g975oMijMg`, the `advisor_tool_result` answering it with an `advisor_redacted_result`, and the text that followed. The answer rides in the same message — no user record ever answers one |
| 6–7 | `62291f25-854b-4be2-b3f1-32f485b9125b` main lines 117–118 | the other advisor outcome: `srvtoolu_01KUMaS97sNkE7Z12UW4HMEp` answered by an `advisor_tool_result_error` carrying `error_code: unavailable` |
| 8 | `5b451fe6-32ba-4688-a7ed-31e50da598f1` subagent `agent-a08ca03ffc1f9bba1.jsonl` line 17, **Claude Code 2.1.206** | the only other block kind the corpus hid: a `fallback` from `claude-fable-5` to `claude-opus-4-8`. All 3 recorded fallbacks agree with their record's `message.model` on the `to` side, which is why only `from` becomes a column |

## `subagents/agent-a3b37063695183556.jsonl`

7 records from `62291f25`'s subagent, lines 82–88, all under `msg_01KxXZD4XaHEh8QaC7W2StBW`: two
thinking blocks, two local `tool_use` calls with a `tool_result` each, and the `server_tool_use`
`srvtoolu_01FHMDigqBGzPfr9CkXyA91v` issued between them and never answered — one of the 45 in the
corpus is not. The mix is the point: a message holding both kinds shows the local pair sharing the
batch's synthetic start while the server-side call keeps its own.

`agent-a3b37063695183556.meta.json` is the run's meta, trimmed to `agentType`, `description`,
`spawnDepth` and `toolUseId`.

## Redaction

As `spine/` — see that README. Every string outside the structural keep-list became `[redacted]`,
`gitBranch` became `fixture-branch-1` and `slug` became `fixture-slug-1`. The advisor's
`encrypted_content` is redacted like any other payload; nothing readable was ever in it.
