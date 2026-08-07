# `offload/` — a tool result Claude Code moved out of the transcript

Redacted excerpt of `7e37bb35-4dcb-4e16-85be-55ac510c168e.jsonl`, **Claude Code 2.1.220**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 4 records: lines 9, 33–35 of the original, in
original order, plus one file of the session's `tool-results/` directory.

The session was sourced for the offload shape: no main transcript of the `spine/` or `dup_uuid/`
sessions carries a `persistedOutputPath`, and only 321 tool results in the whole mycelia corpus do
(scanned 2026-08-07).

## What each record is here for

- line 9 — the prompt that opens the turn, so the call hangs off a turn like any other
- lines 33–34 — one assistant message, a thinking chunk and the `tool_use` chunk that issued
  `toolu_01JXs55LXLHvzWt8KczuYfyD`, a lone call whose start is measured rather than synthetic
- line 35 — the answering `tool_result`, whose `toolUseResult` carries `persistedOutputPath` and
  `persistedOutputSize` while `content` holds only the preview

## Redaction

As `spine/` — see that README — with two additions. `persistedOutputPath` keeps its **file name**
and nothing else: the recorded value is an absolute path on the machine that wrote it, and the name
is what links a call to its file. The offloaded file itself was replaced wholesale, so its 161 bytes
no longer match the `persistedOutputSize` of 30702 the record reports; that mismatch is the fixture's,
not Claude Code's. The session's second offloaded file was dropped.
