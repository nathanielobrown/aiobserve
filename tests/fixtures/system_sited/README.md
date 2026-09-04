# system_sited

A session whose only record carrying a `cwd` is a thin `system` subtype, so the project, the
branch, the version and the entrypoint have nowhere else to come from.

| | |
| --- | --- |
| Source session | `637fb3f1-ab2c-427e-b876-304be9f7bb8e` |
| Claude Code version | 2.1.205 |
| Records | the session's four, whole: `mode`, `permission-mode`, `system/informational`, `last-prompt` |

The operator switched permission mode and closed the session without prompting, so the notice
Claude Code wrote about the switch is the only sited record in the file. Five of the 3,647 threads
in the store are sited by an `informational` record this way, and every record of every thin
`system` subtype carries all four fields — `away_summary` 374, `scheduled_task_fire` 85,
`informational` 19, `agents_killed` 4, `stop_hook_summary` 2 (scanned 2026-09-04 over 705,431
records in 630 sessions). Ten threads carry no `cwd` at all, which is the shape this one is not.

**Redaction.** The notice text, the mode and the permission mode are replaced with `[redacted]`
and `gitBranch` is rewritten. The envelope — uuids, the timestamp, `cwd`, `version` and
`entrypoint` — is the session's own, since it is what the test reads.
