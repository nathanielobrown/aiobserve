# `fork_byref/` — a fork that inherited its context by reference

Redacted excerpt of `07a769d7-828c-4edb-b3ce-af51e2712aa3.jsonl`, **Claude Code 2.1.202**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. Lines 1–3 of 263, plus one subagent transcript.

The session was sourced for the other fork shape. This fork copies nothing: its file opens with a
`fork-context-ref` record naming the session and the record it picked up from, and everything
after that is its own. 26 of the 52 fork transcripts on this machine open that way (scanned
2026-08-07); the rest copy the history instead, as `fork_origin/` does.

Nothing here is a replay, so the fixture is the negative case for the flagging rule. What it does
exercise is the turnless opening: the transcript's first records answer a prompt that lives in
another file, so they belong to no turn of its own.

## What each file is here for

- the main transcript, lines 1–3 — the bookkeeping records that open a transcript
- `subagents/agent-afa3946951a08a798.jsonl`, lines 1–7 of 72 — the `fork-context-ref` record and
  the work that follows it: three assistant messages and the tool results answering them, and no
  prompt at all. The inherited context is `parentLastUuid: 97e2004c-…`; the record's
  `contextLength: 63` is carried but unread — what it counts is not established
- `.../agent-afa3946951a08a798.meta.json` — whole but for a redacted `description`. It carries
  `isFork: true` and `agentType: "fork"`, the same as a copied-history fork's: the leading record
  is what tells the two variants apart

## Redaction

As `spine/` — see that README.
