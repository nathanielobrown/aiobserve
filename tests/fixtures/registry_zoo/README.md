# `registry_zoo/` — one record of every type the corpus contains

A synthetic *file* built from real *records*: one redacted record of every parsed type, every
archive-only type, and all nine `system` subtypes, each lifted from a real session under
`~/.claude/projects/-Users-nob-repos-mycelia/` and rewritten to one fixture session id. Nothing about
a record's own fields is invented.

The last three records — `fork-context-ref`, `started`, `result` — never occur in a main transcript.
They live here anyway so the registry has a regression net for them before slice 3 builds the fixtures
that show them in their native files.

| Record type | Source | CC version |
| --- | --- | --- |
| `mode`, `permission-mode`, `bridge-session`, `file-history-snapshot`, `custom-title`, `agent-name`, `pr-link`, `system/turn_duration`, `system/local_command` | `0164a230-513c-48fd-9d36-65feec35dd23.jsonl` | 2.1.207 |
| `user`, `assistant`, `attachment`, `ai-title`, `last-prompt`, `queue-operation` | `006ab3eb-24b0-46c7-bfa3-9db16d7eadc9.jsonl` | 2.1.196 |
| `agent-setting` | `9caf667e-56ec-4267-9281-b168882aaf5b.jsonl` | 2.1.211 |
| `file-history-delta` | `08483117-689d-4a79-91fa-963f821eee02.jsonl` | 2.1.220 |
| `relocated`, `worktree-state`, `system/agents_killed` | `10d0349d-0705-4e23-aa64-5b1b97698b2e.jsonl` | 2.1.211 |
| `summary` | `4b443ab7-98f8-4c1d-859f-9bdcafbabdd3.jsonl` | 1.0.128 |
| `system/away_summary` | `034aae5c-30af-4c86-a79e-d8257eb0ea54.jsonl` | 2.1.215 |
| `system/compact_boundary` | `0164a230…/subagents/agent-a1d0bc50fe316ed8e.jsonl` | 2.1.207 |
| `system/informational` | `2352492b-1437-4427-ad51-70f35c75f663.jsonl` | 2.1.205 |
| `system/scheduled_task_fire` | `17e0f606-7988-46b9-b3aa-ecc0cf2325da.jsonl` | 2.1.212 |
| `system/api_error` | `…--claude-worktrees-bridge-cse-01U6WqpfSie9fWQr1W96vT3G/52f75c33-a08d-4776-8971-7202f1e5b27f.jsonl` | 2.1.206 |
| `system/stop_hook_summary` | `4f16ec79-1afc-45f9-8095-370c7cd66cfd.jsonl` | 2.1.205 |
| `fork-context-ref` | `07a769d7…/subagents/agent-afa3946951a08a798.jsonl` | 2.1.202 |
| `started`, `result` | `426e7c0f…/subagents/workflows/wf_266c06f9-6ae/journal.jsonl` | no `version` field |

Every record above was re-located in the corpus by a field it still carries — a uuid, a `messageId`, a
`leafUuid`, an `agentId`, or a timestamp. Five bookkeeping types (`mode`, `permission-mode`,
`custom-title`, `agent-name`, `agent-setting`) carry no field unique to one session; each fixture line
is byte-identical, modulo `sessionId`, to a record in the session cited for it. The cited files are
single-version, which is what fixes a CC version on a record that carries none.

The `summary` record's source session reports `version: "1.0.128"` on every record it holds, far below
the rest of the corpus. The design's open questions flag that value as unexplained; it is carried here
as recorded.

## Redaction

As `spine/` — see that README.
