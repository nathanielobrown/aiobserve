# `model_only/` — a session that ran a slash command and nothing else

Redacted excerpt of `bec99999-cbb7-4d11-9a58-3ad3d0e1c8cf.jsonl`, **Claude Code 2.1.215**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Lines 9–11 of 12: the three `user` records that make
the session's one `/model` turn. It extracts to `turns = 1, agent_runs = 0, api_calls = 0`, which is
the shape under test — a session whose turns drove no model response at all.

45 of the 571 recorded mycelia sessions are in this shape (`aiobserve query sessions`, 2026-08-13),
and enrichment used to describe every one of them from a render with no work in it. The gate in
`src/aiobserve/enrich/store.py` skips them; this recording is what proves it.

Four recordings carry the shape. This one was picked for its date: 2026-07-20 falls inside the
window every `$as_of` in `tests/analyze/conftest.py` opens over the whole corpus, so the corpus keeps
a window wider than itself and the session reaches the coverage denominator.

The nine records left behind are bookkeeping and an earlier `/context` turn — `mode`,
`permission-mode`, two `file-history-snapshot`, two `bridge-session`, and the three the `/context`
command wrote. The first kept record's `parentUuid` still points at one of them; a trimmed fixture
dangles there by design, and the extractor threads the turn regardless.

## Redaction

Every string outside the structural keep-list is `[redacted]`; `gitBranch` is pseudonymised to
`fixture-branch-N`. The tag wrappers survive with their contents redacted, as `spine/`'s slash turns
do — a record's tag is what makes it a command turn rather than a prompt, so flattening it would
change the shape. The command *name* survives for the same reason; its argument does not. `cwd`
stays, since `project_dir` is what puts the session inside the mycelia corpus the analyze tier
filters on.
