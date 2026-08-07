# `dup_uuid/` — within-transcript duplicate uuids

Redacted excerpt of `8ee00a94-b01a-4394-b447-b065f74b11af.jsonl`, **Claude Code 2.1.211**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 10 records: lines 863–865, 873–874 and 1273–1277 of
the original, in original order.

The testing plan named `e684d4da` for this leaf. A scan of every main transcript found that session
has no pair differing in `message.usage`: corpus-wide only 7 such pairs exist, 5 of them in this
session. Sourcing from 8ee00a94 puts both halves of the leaf — an envelope-only rewrite and a
usage-differing rewrite — in one file.

## The five duplicated uuids

Each appears twice; the second occurrence is the one the extractor must keep.

| uuid prefix | Type | What differs between the two |
| --- | --- | --- |
| `7c5ceb12` | assistant | `gitBranch`, and `message.usage` — first records 3237/113217/2629 tokens, second records zeros |
| `aa660a1a` | assistant | same pair of differences |
| `1cf5dead` | system/turn_duration | `gitBranch` only |
| `7db20c1f` | system/compact_boundary | `gitBranch` only |
| `f80851a6` | user, `isCompactSummary` | `gitBranch` only |

So last-occurrence-wins is observable twice over: in `ApiCall` token counts and in `Session.git_branch`.
The `isCompactSummary` record also discharges the "not a turn" leaf.

## Redaction

As `spine/` — see that README. `gitBranch` is pseudonymised, but the *pattern* of which duplicate
carries which branch is preserved, since that is what the test asserts on.
