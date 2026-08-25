# Telemetry schema

This document defines the Claude Code telemetry fields that aiobserve reads. Read it before writing a query or analysis. Misreading a field can turn a bad premise into a confident finding.

The span schema will arrive with the span importer. Its source shapes come from `mac_settings/claude-otel/`, which we have not documented here. Until that importer exists, don't describe span fields from memory.

## Every schema claim needs a recording

For each field, cite a recorded session and the Claude Code version that wrote it. Claude Code owns these shapes and can change them without notice.

Prefer a checked-in fixture. The fixture directory's README names its source session and version, so readers can verify the claim. If no fixture can preserve the evidence, name the corpus scan and its date. Mark an inferred mechanism as an inference.

## Transcript records are typed JSON objects

A transcript stores one JSON object per line. Each object has a `type`. `aiobserve.extract.claude_code` registers every type it has seen and crashes on unknown types. Treat that registry—not the tables below—as the current census.

### Record identity and session context

| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `type` | every record | The record shape. Known values include `user`, `assistant`, `system`, `attachment`, `summary`, and about a dozen bookkeeping types | `tests/fixtures/registry_zoo/` contains one record of every registered type |
| `subtype` | `system` | The system event. The registry zoo contains ten, including `turn_duration`, `compact_boundary`, and `api_error` | `tests/fixtures/registry_zoo/` |
| `uuid` | most records | The record id within its file. It is not unique: rewinding can write new records under existing uuids | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `timestamp` | most records | A UTC ISO-8601 timestamp with a `Z` suffix. File order is not timestamp order; adjacent records can move backward by one millisecond | `tests/fixtures/spine/`, CC 2.1.221 |
| `cwd`, `gitBranch`, `version`, `entrypoint` | records that carry session context | The project directory, branch, Claude Code version, and launch method. Early bookkeeping records may omit all four, so reading only the first record yields nulls. `entrypoint` is absent from the oldest corpus transcripts | `tests/fixtures/spine/` has no `cwd` on lines 1–3; `tests/fixtures/legacy_entrypoint/`, CC 1.0.128, has no `entrypoint` |
| `isMeta` | `user` | Claude Code wrote the record on the user's behalf, such as a caveat or hook echo. It is not a prompt | `tests/fixtures/spine/`, CC 2.1.221 |
| `isCompactSummary` | `user` | Claude Code wrote the record after compaction to replace the dropped context. It is not a prompt | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `isSidechain` | `user`, `assistant` | The record belongs to a subagent stream. In a main transcript, skip it because the subagent file records the work better. In a subagent transcript, every record carries it; skipping those records would remove every turn | `tests/fixtures/spine/` contains both main and subagent records, CC 2.1.221 |

`cwd` is absolute and contains no symlinks. Resolve a command-line project path before matching it; `aiobserve.sessions.resolve_project` does this. Of 240 encoded working directories under `~/.claude/projects` on the recording machine, 181 lie under a symlinked root—153 under `-private-var` and 28 under `-private-tmp`—but none uses the unresolved `-var-…` or `-tmp-…` spelling (scanned 2026-08-15). The likely mechanism is Node's `process.cwd()`, which returns a physical path, but that mechanism is inferred. No fixture demonstrates it because every fixture session ran under an unsymlinked path.

### User and assistant content

| Field or block | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message.content` | `user` | Either a string or a list of `text`, `image`, or `tool_result` blocks. A record containing a `tool_result` is plumbing, not a prompt | `tests/fixtures/spine/`, CC 2.1.220 for the block form |
| `tool_use` | `assistant` | A local tool request with `id`, `name`, and `input`. Most records contain one, but 23 records in the mycelia corpus contain two or more, so counting records undercounts calls (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221; `tests/fixtures/parallel_tools/`, CC 2.1.211, contains two calls in one record |
| `tool_use.input` on `Skill` | `assistant` | The invoked skill in `skill`, with `args` on 81 of 326 corpus calls. This records invocation; `attributionSkill` records what was loaded when the reply returned. They can disagree, and a skill reached through a slash command creates no `Skill` call (57 sessions, CC 2.1.195–2.1.221; scanned 2026-08-08) | corpus scan only |
| `tool_result` | `user` | A local tool's reply. `tool_use_id` names the call. `content` is a string or a list of `text`, `image`, and `tool_reference` blocks. Success omits `is_error`: 66,653 of 154,169 corpus result blocks omit it (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `server_tool_use` | `assistant` | A tool request that Anthropic ran server-side, with the same fields as `tool_use`. All 45 corpus blocks, across five sessions, call `advisor` with empty `input` (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `advisor_tool_result` | `assistant` | The answer to `server_tool_use`, stored in the same message rather than a `user` record. `content.type` is either `advisor_tool_result_error`, with an `error_code`, or `advisor_redacted_result`, with unreadable `encrypted_content`. The corpus contains answers for 44 of 45 calls; one call has no answer (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201, contains both result types and the unanswered call |
| `fallback` | `assistant` | A retry on another model, with `from.model` and `to.model`. All three corpus blocks occur in one session and agree with `message.model` on `to`, so only `from` adds information (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `toolUseResult` | `user` records answering a tool | The tool's structured report beside the result block. Most are objects, but 3,590 of 137,255 corpus values are strings and 795 are lists (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |
| `toolUseResult.persistedOutputPath` | `user` records answering a tool | The path to output too large for the transcript. Claude Code writes the full output to `<session>/tool-results/<name>.txt` and leaves a preview in `content`. The corpus contains 321 such results. The recorded path is absolute; only its file name travels (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |

### API replies, models, and tokens

| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message.id` | `assistant` | The API reply id and the key for merging records. One reply can span several records, one per content block. In the fixture, eight records represent two replies; counting lines triples the API-call count | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.usage` | `assistant` | Token usage for the whole reply. Every record with the same `message.id` repeats the totals, so summing records multiplies usage by the number of chunks | `tests/fixtures/spine/`, CC 2.1.221, contains five identical copies under one id |
| `usage.cache_creation` | `assistant` | Cache-creation tokens split by TTL into `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. Every assistant record in the mycelia corpus has this object, so the absent shape remains unrecorded (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.model` | `assistant` | The model that answered. `<synthetic>` marks Claude Code's placeholder for an interrupt or cancelled request. Of about 290,000 corpus assistant records, 205 are synthetic; all report zero tokens and omit `usage.inference_geo` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.201 |
| `attributionSkill` | `assistant` | The skill loaded when the reply returned. Absent when no skill was loaded | `tests/fixtures/spine/`, CC 2.1.221 |
| `effort` | `assistant` | The reasoning-effort setting as an opaque string, such as `"high"` | `tests/fixtures/spine/`, CC 2.1.221 |
| `requestId`, `stop_reason` | `assistant` | The API request id and the reason generation stopped | `tests/fixtures/spine/`, CC 2.1.221 |

### System events and session labels

| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `durationMs` | `system` / `turn_duration` | The turn's wall-clock duration in milliseconds. Sum these values to measure active session time; the transcript's timestamp span includes idle hours | `tests/fixtures/spine/`, CC 2.1.221 |
| `originalModel`, `fallbackModel`, `choice`, `persistedAsDefault` | `system` / `model_consent_fallback` | Claude Code used a different session model because the requested model needed credits the account lacked. `choice` records the operator's response (`cancelled`), and `persistedAsDefault` says whether the change outlived the session. This differs from a `fallback` block, which records one request retried on another model | `tests/fixtures/registry_zoo/`, session `cb76d8e4-cb08-4693-bbec-d4bfa97b1f5c`, CC 2.1.221 |
| `compactMetadata` | `system` / `compact_boundary` | The compaction's `trigger`, `preTokens`, `postTokens`, and `durationMs`. All 1,026 corpus boundaries contain all four fields; 933 triggers are `auto` and 93 are `manual` (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 |
| `customTitle`, `aiTitle` | `custom-title`, `ai-title` | The session title. Claude Code writes and revises `ai-title`; the operator sets `custom-title`. Both remain current, and 13 of 398 titled mycelia sessions contain both (scanned 2026-08-07) | `tests/fixtures/spine/` and `tests/fixtures/legacy_title/`, CC 2.1.196–2.1.201 |
| `agentName` | `agent-name` | The persona used for the session. Claude Code rewrites it with the title. Like title records, it contains one value and has no uuid or timestamp | `tests/fixtures/spine/`, CC 2.1.201 |
| `prNumber`, `prUrl`, `prRepository` | `pr-link` | A pull request mentioned by the session. Claude Code writes one record per mention. All 2,885 corpus records contain these fields plus `type`, `sessionId`, and `timestamp`, but no uuid. Key each link by its line; the same PR can recur within a session (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |

## Read transcript records by these rules

### A leading tag distinguishes prompts from harness messages

When a `user` record contains a string, its leading XML-like tag often determines whether the record starts a turn.

- Count `<command-name>` and `<command-message>` as a turn. They mark a slash command, can appear in either order, and carry `<command-args>` beside them. The wrapper is the whole prompt: all 451 command turns in the canonical store hold the tags and nothing else, so a command turn's `prompt` says no more than its `command_name` and `command_args` do (scanned 2026-08-24)
- Count `<teammate-message>` as a turn
- Don't count `<task-notification>`, `<local-command-stdout>`, `<bash-input>`, or `<bash-stdout>`. Claude Code wrote these to itself

Counting every string-valued `user` record as a turn inflates the total several-fold. The mycelia corpus contains 2,157 `<task-notification>` records but 968 prompts ([trace-pipeline design](../plans/trace-pipeline/design.md)). The extractor crashes on an unregistered tag instead of guessing, because the next machine-written tag would silently inflate the count again.

Tags can carry attributes, as in `<teammate-message teammate_id="team-lead" summary="…">`. Parse the name only to the first whitespace or `>`. Keep the full opening tag in `Turn.prompt` because it identifies the sender. The 132 corpus `<teammate-message>` records all occur in subagent transcripts from one mycelia session, so a census of main transcripts misses them (scanned 2026-08-07).

*Evidence:* `tests/fixtures/spine/` contains both slash-command orderings at CC 2.1.221, `<bash-input>` and `<bash-stdout>` at CC 2.1.212, and `<teammate-message>` at CC 2.1.211.

### Attach slash-command output to the command turn

`<local-command-stdout>` does not start a turn. It records what a slash command printed, and its `parentUuid` points to the command turn. Many command turns produce no model reply, making this output the only record of what happened.

Claude Code writes the output in two shapes:

- A `user` record with the tag in `message.content`: 279 of 316 mycelia records
- A `system` record with `subtype: local_command` and the tag in `content`: the other 37

The text between the tags can span lines, so don't stop at the first line. It can also be empty. All 21 recorded `/clear` outputs are empty, compared with a median body length of 71 characters and a maximum of 2,038 (scanned 2026-08-13).

A resumed session can replay the same output under the plain turn that now precedes it. The corpus contains 183 such records. If `parentUuid` points to a turn that ran no command, the output has no owning turn in that transcript; the archive is not malformed.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains the `user` carrier; `tests/fixtures/model_only/`, CC 2.1.215, contains the `system` carrier and an empty `/clear` body; `tests/fixtures/resume_pair/`, CC 2.1.202, contains the replay.

### Start parallel local calls at the batch's first timestamp

One assistant message can issue several local tool calls at once. Claude Code usually writes one record per call in execution order, so calls issued together receive different timestamps. Only 156 of 23,371 multi-call messages in the mycelia corpus use one timestamp for the whole batch (scanned 2026-08-07). Treating each record timestamp as its call's start mistakes queue position for duration.

Start every call in such a batch at the earliest record timestamp and set `ToolCall.duration_synthetic` to show that the start was assigned rather than measured. A lone call keeps its own timestamp and sets the flag to false.

Define the batch by records, not calls. One record can contain several `tool_use` blocks; those calls were issued together and keep their shared, measured record timestamp. Counting blocks would mark that real start as synthetic.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains three calls under `msg_011CdmMjFXDofyYSMxYtXa5n`; `tests/fixtures/parallel_tools/`, CC 2.1.211, contains one message of each shape.

### Time a server-side call from its own record

A `server_tool_use` shares the assistant stream with local calls but does not join their batch. Claude Code did not execute it, so its record marks the request rather than a queue position. Keep that timestamp, set `duration_synthetic` to false, and end the call when Claude Code writes the `advisor_tool_result` in the same message.

Store the call in `tool_calls` with `server_side` set. Before the extractor registered this block, it produced no row, text, or crash; sessions that used the advisor looked as though they had not.

*Evidence:* `tests/fixtures/server_tools/`, CC 2.1.201, contains a subagent message with two local calls and one server-side call.

### Keep the last record when a uuid repeats

Rewinding leaves both the old and new records under the same uuid. Their token usage differs. The extractor keeps the last record because it reflects the session's final state; keeping the first changes token totals in four mycelia sessions.

No recorded duplicate pair changes `message.content`. Such a change would mean that Claude Code rewrote the conversation itself, so the extractor crashes if it finds one.

*Evidence:* `tests/fixtures/dup_uuid/`, CC 2.1.211, contains five uuids twice each.

### Read compaction from the boundary record

Every `system` / `compact_boundary` record has a corresponding `user` record with `isCompactSummary`. The mycelia corpus contains 1,026 of each, with matching counts in every file (CC 2.1.191–2.1.221; scanned 2026-08-07). Read compaction from the boundary's `compactMetadata` rather than inferring it from the nearest assistant call.

Subagents compact much more often than main transcripts. Attribute a compaction to the file that reached the limit, not to the session as a whole.

*Evidence:* `tests/fixtures/compaction/`, CC 2.1.198, contains one `auto` and one `manual` boundary, each with its summary.

### Preserve both cache-creation totals

`usage.cache_creation_input_tokens` should equal the sum of `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` under `usage.cache_creation`, but 53 of about 290,000 mycelia assistant records disagree (scanned 2026-08-07). The extractor stores the total as `cache_creation_tokens` and the split as `cache_5m_tokens` and `cache_1h_tokens`. Cost uses the split when present, so those 53 calls use a value that the total does not confirm.

### Split records only on newline characters

String values can contain raw U+2028 and U+2029 separators. Python's `splitlines()` treats them as record boundaries and breaks JSON objects. Split transcript files on `"\n"`.

## Session data comes from three places

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` stores one session transcript as one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/` stores the session directory described below; `aiobserve.sessions` walks this tree
- Claude Code's OpenTelemetry export provides a thinner live schema and is enabled per machine, not per repository

Claude Code forms `<encoded-cwd>` by replacing each `/` in the working directory with `-`: `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. This tree is shared across Claude accounts because `~/.claude-black/projects` is a symlink to `~/.claude/projects`. A transcript path therefore does not identify the account that wrote it.

### A session directory holds transcripts, metadata, and offloaded output

Of 575 mycelia transcripts, 104 have a session directory beside them (scanned 2026-08-07). Those directories contain only the path shapes below. The extractor crashes on an unknown path because Claude Code prunes these directories within weeks; silently skipping a file could erase the only evidence of a schema change.

| Path below `<session-id>/` | Count | Contents | Archive destination |
| --- | ---: | --- | --- |
| `subagents/agent-<id>.jsonl` | 2,275 | A subagent transcript | source `<id>` |
| `subagents/agent-<id>.meta.json` | 2,275 | The spawn metadata: `toolUseId`, `agentType`, and `spawnDepth` | `agent_runs` |
| `subagents/workflows/wf_<id>/agent-<id>.jsonl` | 180 | A parallel fan-out agent transcript, one level deeper | source `<id>` |
| `subagents/workflows/wf_<id>/agent-<id>.meta.json` | 180 | Only the workflow agent's `agentType` and `spawnDepth`; it has no spawning tool call | `agent_runs` |
| `subagents/workflows/wf_<id>/journal.jsonl` | 6 | The fan-out log, with `started` and `result` records keyed by agent | source `wf_<id>/journal` |
| `tool-results/<name>` | 567 | Tool output named by `persistedOutputPath` because it was too large for the transcript | `offload_files` |
| `workflows/wf_<id>.json` | 6 | The workflow definition | not read |
| `workflows/scripts/<name>.js` | 6 | The script that drove the workflow | not read |

A subagent id is often hexadecimal, but sessions can assign names such as `agent-audit-pr291-79ea2c606313e623.jsonl`. Use the complete stem after `agent-` as the source.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains a subagent; `tests/fixtures/workflow/`, CC 2.1.207, contains a fan-out and journal; `tests/fixtures/offload/`, CC 2.1.220, contains a persisted result.

### Subagent metadata records why the agent ran

Each observed subagent transcript has a neighboring `meta.json`, and each meta has a transcript: 2,764 pairs on the recording machine, with no unpaired files (scanned 2026-08-07). Because no recording establishes how half a pair should behave, the extractor crashes if it finds one.

| Key | Metas | Meaning |
| --- | ---: | --- |
| `agentType` | 2,764 | The agent definition, such as `general-purpose`, `auditor`, `workflow-subagent`, or a session-defined name. This is not a closed set |
| `spawnDepth` | 2,763 | `1` for an agent spawned by the session, higher for nested agents, and `0` for a teammate. Its absence in one CC 2.1.186 session is a recorded state, not a parse error |
| `description` | 2,584 | The one-line task summary from the spawning call |
| `toolUseId` | 2,510 | The `Agent` call that requested the run |
| `model` | 753 | The model alias chosen by the caller, such as `opus` |
| `parentAgentId` | 389 | The agent that spawned this run |
| `isFork` | 52 | The run replays another transcript's history or continues it by reference |
| `taskKind`, `teamName`, `color`, `planModeRequired`, `permissionMode` | 71 | Teammate fields; `taskKind` is `in_process_teammate` |
| `name`, `worktreePath`, `worktreeBranch`, `customAgentType`, `stoppedByUser` | 94, 86, 86, 39, 3 | Recorded but not yet read |

Of the 254 metas without `toolUseId`, 180 belong to workflow agents, 71 to teammates, and three to forks. The team mechanism starts a teammate without a tool call. Preserve that orphaned run with a warning; dropping it would recreate the prior importer's false claim that all agent runs came from direct tool calls.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains a spawned and nested run; `tests/fixtures/teammate/`, CC 2.1.211, contains an orphaned teammate.

### Join a workflow agent through its launcher's run id

A fan-out does not spawn agents one by one, so its agent metas name no call. The launching `Workflow` call returns `toolUseResult.runId`, which matches the `wf_<id>` directory containing the agent transcripts. This is the only link from those transcripts to their launching tool call. All six workflow runs on the recording machine contain it (scanned 2026-08-07).

*Evidence:* `tests/fixtures/workflow/`, CC 2.1.207, contains the `Workflow` call, its result, and the named `wf_c30cc877-997` directory.

### Attribute copied history to the transcript that ran it first

All 52 observed fork metas pair `isFork: true` with `agentType: "fork"` (scanned 2026-08-07). The first transcript record identifies one of two fork shapes:

| First record | Forks | Meaning |
| --- | ---: | --- |
| `fork-context-ref` | 26 | The file copies no records. The opening record names `parentSessionId`, `parentLastUuid`, and `contextLength`; work begins mid-conversation |
| `user` or `system` | 26 | The file copies the parent's records verbatim, including uuids and timestamps, then appends the fork's work |

A copied record then appears in two files. The corpus contains 51 overlapping transcript pairs, each with a fork on one side; 25 are fork-to-fork, where one fork copies another's work. Attribute each record to the transcript that ran it first. Keep later copies but mark them `replayed`, so the archive retains what each file recorded without double-counting the work. This rule marks 1,617 records across nine sessions as replays. None appears in a non-fork transcript; such a replay would show that the ordering chose the wrong origin.

Order transcripts by `(spawnDepth, first timestamp, agentId)`, with the main transcript first. Depth must lead because a copied-history fork begins with its parent's timestamp. Of 51 overlapping pairs, 46 tie on the first timestamp; breaking those ties by agent id would wrongly assign 335 records from six original transcripts to their forks. A fork is spawned by the transcript it copies and is therefore deeper.

The one meta without `spawnDepth` sorts last. Its transcript, `-Users-nob-repos-mac-settings/c31ecec9-…/subagents/agent-a20276f6d8a4e5309.jsonl` from CC 2.1.186, shares no uuid with a sibling, so its position does not affect attribution.

*Evidence:* `tests/fixtures/fork_origin/`, CC 2.1.215, contains a copied-history fork and the auditor it copied; `tests/fixtures/fork_byref/`, CC 2.1.202, begins with `fork-context-ref`.
