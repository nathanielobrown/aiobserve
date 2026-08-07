# `resume_pair/` — one conversation recorded under two session ids

Two redacted excerpts from `~/.claude/projects/-Users-nob-repos-mycelia/`. A `/resume` writes the
whole prior transcript into the new session's file, so the same records — `message.id`, tool ids,
timestamps and all — are on disk twice under different session ids.

| File | Source lines | CC version | Role |
| --- | --- | --- | --- |
| `2352492b-1437-4427-ad51-70f35c75f663.jsonl` | 1–5, 1362–1403 | 2.1.202 | the session that ran the work, 2026-07-07 |
| `0a76f771-5f5b-447e-852a-664fc972ea7c.jsonl` | 1–32, 62–70 | 2.1.205 | the resume that copied it forward, 2026-07-09 |

They share 4 API calls, 5 tool calls and 1 compaction verbatim. The resume adds one call of its own,
`msg_011CcsBSmj5PNQCMowYZqET7` — the number `corpus_rollups` must report for it.

## Why each excerpt keeps its opening records

Lines 1–5 of the ancestor and 1–32 of the resume are not part of the shared block; they are there to
keep the two sessions' `started_at` two days apart. Trimmed to the shared block alone, both sessions
start in the same millisecond, and `first_seen` falls back to the id — which would crown the resume
as the original and invert everything the fixture is for.

## Redaction

Every string outside the structural keep-list is `[redacted]`, dictionary keys included: a
`file-history-snapshot` keys its map by absolute path, so the walk replaces any key that is not a
plain identifier. `gitBranch` and `slug` are pseudonymised. No prompt text, tool input, tool output,
thinking, or file path survives.
