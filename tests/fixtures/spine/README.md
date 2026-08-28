# `spine/` — the slice-1 whole-object fixture

Redacted excerpt of `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl`, **Claude Code 2.1.221**, from
`~/.claude/projects/-Users-nob-repos-mycelia/`. 35 records, drawn from lines 1–3, 5–7, 9, 16, 19–24,
123, 126, 140, 513, 531, 780–781 and 895–900 of the original. Order is the original's, except that
780–781 — the `Agent` call that spawned the subagent below, and its result — sit inside the last turn
so the spawning call and the delegated work stay in one excerpt, and the two `pr-link` records sit at
the end.

Eight records are **borrowed** from other sessions because 4208c1bd contains no instance of the shape.
Their `sessionId`/`session_id` were rewritten to the host session so the file parses as one session;
everything else is as recorded, including timestamps and Claude Code versions from the day they were
written:

| Records | Source session | CC version | Shape it carries |
| --- | --- | --- | --- |
| `<bash-input>`, `<bash-stdout>` | `64cca9e3-00b3-4faf-8c28-0ae6b3d5f789` lines 37–38 | 2.1.212 | machine tags that are never turns |
| block-content text prompt | `2d1b86d1-dedb-4789-83b3-c2bb763627cc` line 18 | 2.1.220 | a turn whose content is blocks, not a string |
| two `custom-title` + two `agent-name` (lines 1–2, 18–19) | `9cd5fc94-771d-4d24-ba06-9792e990510c` lines 1–2, 53–54 | 2.1.201 | a session renamed mid-run: 4208c1bd's own 47 title records all repeat one value, so only a session that was really renamed can show the last one winning |
| `<synthetic>` assistant reply (line 32) | `9cd5fc94-771d-4d24-ba06-9792e990510c` line 27 | 2.1.201 | Claude Code's own placeholder reply — zero tokens, `stop_reason: "stop_sequence"` |
| `ai-title` (line 35) | `9cd5fc94-771d-4d24-ba06-9792e990510c` line 3 | 2.1.201 | the title Claude Code generated, placed after both renames so the test shows `custom-title` winning on position as well as on kind |

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
- `msg_011CdmToQdxciYnDo9M2d7HN` and the `tool_result` answering it — the `Agent` call
  `toolu_015dP3eMe5GZn7BzFipupZwS`, which the subagent's `meta.json` names as what spawned it
- two `custom-title`/`agent-name` pairs and a trailing `ai-title` — the last operator rename names the
  session, and a generated title after it does not
- the `<synthetic>` reply — priced at a stated zero rather than left unpriced. Its 2026-07-06
  timestamp is the file's earliest, so it also sets the session's `started_at`
- two `pr-link` records — the same `prNumber` twice, four minutes apart, which is why a PR link is
  keyed by its transcript line rather than by its number

## The session directory

`4208c1bd-.../subagents/` holds two of the session's subagents, both **Claude Code 2.1.221**:

- `agent-ac461ef46b4bb8e32.jsonl`, lines 1–6 and 19–22 of 43 — the run the main transcript's `Agent`
  call asked for. Every record carries `isSidechain: true` and an `agentId`, the two fields that place
  a record under a source other than `main`, and its `cwd` is the worktree the subagent ran in rather
  than the session's. Lines 19–22 are its own two `Agent` calls and their results: one delegates in
  turn, and the run it started is the file below
- `agent-af6473ae437c9608d.jsonl`, lines 1–6 of 104 — a subagent's subagent, whose meta names
  `parentAgentId` and `spawnDepth: 2` and no `model`

Each `.meta.json` came too, whole but for a redacted `description` — the meta is the linkage from a
subagent back to the `toolUseId` that spawned it, which agent runs read.

## Redaction

Every string outside a small keep-list of structural fields is `[redacted]`. Kept: record and message
types, uuids, timestamps, session ids, `version`, `model`, `stop_reason`, `requestId`, `effort`,
`attributionSkill`, usage numbers, tool names and tool_use ids, the slash-command *names*,
`prNumber`, and the five tool-input fields below. `gitBranch`, `slug`, the two title fields, `agentName`, `prUrl` and `prRepository` are
pseudonymised to `fixture-<kind>-N`, preserving which records shared a value. No prompt text, tool
output, or thinking survives — including dictionary *keys*, since a file-history snapshot keys its
map by absolute path.

### The five tool-input fields the titles read

The viewer titles a tool call from a named field per tool (`src/hyphae/view/nodes.py:FORMATTERS`),
so a fixture with every input blanked can only prove that the page prints `[redacted]`. These five
are as recorded, and nothing else under `input` is:

| Tool | Field | What the nine calls hold |
| --- | --- | --- |
| `Read` | `file_path` | four paths in `/Users/nob/repos/mycelia`, three of them issue files |
| `Bash` | `command` | `date; ls …/issues/` and an `ls … \| head -60` of the same directory |
| `Bash` | `description` | the two labels those commands were given — kept because the row must show the *command* and not this |
| `Agent` | `description` | three task lines: `Grill doc: needs-design pair`, `Research 0149 multi-instance pg0`, `Research 0155 data-edge semantics` |
| `Agent` | `subagent_type` | `claude` and `Explore` |

**The sensitivity call.** Each kept value was read before it was kept. They are paths inside the
recording machine's own checkout of mycelia — a public-shaped repository layout, issue filenames and
a directory listing — and the role words out of that repo's `.claude/agents/`. No credential, no
customer data, and no prose anyone wrote: `prompt`, `message`, tool results and file contents stay
`[redacted]`, because those are what an agent read and wrote.
