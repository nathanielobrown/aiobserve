# `legacy_title/` — a session named by Claude Code, never by its operator

Redacted excerpt of `0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba.jsonl`, **Claude Code 2.1.196**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. The whole session — 10 records — including its two
`ai-title` records and no `custom-title`, which is the shape under test.

The directory name is a misnomer inherited from the testing plan. `ai-title` is not a legacy
spelling: sessions still write it at 2.1.221, the newest version on this machine, and 13 of the 398
titled mycelia sessions hold both types (scanned 2026-08-07). The two record types differ by
authorship, not by version — Claude Code writes `ai-title`, the operator's rename writes
`custom-title` — and the parser prefers the operator's.

## Redaction

Every string outside the structural keep-list is `[redacted]`. `aiTitle` and `gitBranch` are
pseudonymised to `fixture-title-N` / `fixture-branch-N`, preserving which records shared a value. No
prompt text, tool input, tool output, or file path survives.
