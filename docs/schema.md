# Telemetry schema

Every Claude Code telemetry field hyphae reads, what it means, and the recording that proves it. Read this before writing a query or an analysis: misreading a field turns a bad premise into a confident finding.

The span schema will arrive with the span importer. Its source shapes come from `mac_settings/claude-otel/`, which we have not documented here. Until that importer exists, don't describe span fields from memory.

## Every schema claim needs a recording

For each field, cite a recorded session and the Claude Code version that wrote it. Claude Code owns these shapes and can change them without notice.

Prefer a checked-in fixture. The fixture directory's README names its source session and version, so readers can verify the claim. If no fixture can preserve the evidence, name the corpus scan and its date. Mark an inferred mechanism as an inference.

The transcript-field tables under the next heading are generated. A field's meaning and its citation are declared on the record model that carries it, in `src/hyphae/extract/records/`; document a new field there and run `mise run cogs`. A field declared without a citation fails the generator instead of printing an empty cell.

## Transcript records are typed JSON objects

A transcript stores one JSON object per line. Each object has a `type`. `hyphae.extract.record_types` registers every type it has seen and the readers crash on unknown types. Treat that registry—not the tables below—as the current census.

### Record identity and session context

<!-- aigarden:cog sh "uv run python -m tools.gen_schema identity" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `type` | every record | The record shape. Known values include `user`, `assistant`, `system`, `attachment`, `summary`, and about a dozen bookkeeping types | `tests/fixtures/registry_zoo/` — holds one record of every registered type |
| `subtype` | `system` | The system event. The registry zoo holds ten, including `turn_duration`, `compact_boundary`, and `api_error` | `tests/fixtures/registry_zoo/` — one record of every registered subtype |
| `sessionId` | `user`, `assistant`, `system`, `custom-title`, `ai-title`, `agent-name`, `pr-link` | The session id Claude Code wrote into the record. Nothing reads it: the extractor takes the session id from the file name | `tests/fixtures/spine/`, CC 2.1.221 |
| `uuid` | `user`, `assistant`, `system` | The record id within its file. It is not unique: rewinding can write new records under existing uuids, and the extractor keeps the last | `tests/fixtures/dup_uuid/`, CC 2.1.211 — five uuids twice each |
| `parentUuid` | `user`, `assistant`, `system` | The record this one answers, or null at the start of a thread. A `<local-command-stdout>` record points at the command turn whose output it is | `tests/fixtures/spine/`, CC 2.1.221 |
| `timestamp` | `user`, `assistant`, `system`, `pr-link` | A UTC ISO-8601 timestamp with a `Z` suffix. File order is not timestamp order; adjacent records can move backward by one millisecond | `tests/fixtures/spine/`, CC 2.1.221 |
| `cwd` | `user`, `assistant`, `system` | The project directory, absolute and symlink-free. Resolve a command-line path before matching it — `hyphae.sessions.resolve_project` does. Early bookkeeping records omit it, so reading only the first record yields nulls | `tests/fixtures/spine/`, CC 2.1.221 — the first three records have none |
| `gitBranch` | `user`, `assistant`, `system` | The branch checked out when the record was written | `tests/fixtures/spine/`, CC 2.1.221 |
| `version` | `user`, `assistant`, `system` | The Claude Code version that wrote the record, and the version every schema claim here is dated by | `tests/fixtures/spine/`, CC 2.1.221 |
| `entrypoint` | `user`, `assistant`, `system` | How the session was launched, such as `cli` | `tests/fixtures/spine/`, CC 2.1.221; absent from `tests/fixtures/legacy_entrypoint/`, CC 1.0.128 — the oldest corpus transcripts |
| `isMeta` | `user`, `system` | Claude Code wrote the record on the user's behalf, such as a caveat or a hook echo. It is not a prompt | `tests/fixtures/spine/`, CC 2.1.221 |
| `isCompactSummary` | `user` | Claude Code wrote the record after compaction to replace the dropped context. It is not a prompt, and every one has a `compact_boundary` record beside it | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `isSidechain` | `user`, `assistant`, `system` | The record belongs to a subagent stream. On a main thread, skip it because the subagent's own file records the work better. On a subagent thread every record carries it, and skipping those would remove every turn | `tests/fixtures/spine/`, CC 2.1.221 — holds both main and subagent records |
<!-- aigarden:end -->

Of 240 encoded working directories under `~/.claude/projects` on the recording machine, 181 lie under a symlinked root—153 under `-private-var` and 28 under `-private-tmp`—but none uses the unresolved `-var-…` or `-tmp-…` spelling (scanned 2026-08-15). The likely mechanism is Node's `process.cwd()`, which returns a physical path, but that mechanism is inferred. No fixture demonstrates it because every fixture session ran under an unsymlinked path.

### User and assistant content

<!-- aigarden:cog sh "uv run python -m tools.gen_schema content" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message` | `user`, `assistant` | The API message the record carried: a role and its content | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content` | `user`, `assistant` | Either a string or a list of the blocks below. A `user` record whose list holds a `tool_result` is plumbing, not a prompt | `tests/fixtures/spine/`, CC 2.1.220 — for the block form |
| `text` | `user`, `assistant` | Prose, under `text`: the model's answer, or a prompt written in block form | `tests/fixtures/spine/`, CC 2.1.221 |
| `thinking` | `assistant` | The model's reasoning, under `thinking`, beside the `signature` that lets it be replayed | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_use` | `assistant` | A local tool request. Most records contain one, but 23 records in the mycelia corpus contain two or more, so counting records undercounts calls (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221; `tests/fixtures/parallel_tools/`, CC 2.1.211 — two calls in one record |
| `tool_use.id` | `assistant` | The call id. A `tool_result` block names it in `tool_use_id`, and a subagent's meta names it in `toolUseId`. Unique within a session, not across the store | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_use.name` | `assistant` | The tool asked for, such as `Bash` or `Agent` | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_use.input` | `assistant` | The arguments, shaped by the tool. On a `Skill` call it names the invoked skill in `skill`, with `args` on 81 of 326 corpus calls; that records invocation, while `attributionSkill` records what was loaded when the reply returned. They can disagree, and a skill reached through a slash command creates no `Skill` call (57 sessions, CC 2.1.195–2.1.221; scanned 2026-08-08) | `tests/fixtures/spine/`, CC 2.1.221; corpus scan: 57 sessions, CC 2.1.195–2.1.221, scanned 2026-08-08 — the `Skill` shape |
| `tool_result` | `user` | A local tool's reply, written in the `user` record that answers the call | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_result.tool_use_id` | `user` | The `tool_use` block this answers | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_result.content` | `user` | A string, or a list of `text`, `image`, and `tool_reference` blocks. Only text carries into `ToolCall.result` | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_result.is_error` | `user` | Present when the tool failed. Success omits it: 66,653 of 154,169 corpus result blocks have no `is_error` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `server_tool_use` | `assistant` | A tool request Anthropic ran server-side, with the same fields as `tool_use`. It shares the assistant stream but joins no batch, so its own timestamp is the call's start. All 45 corpus blocks, across five sessions, call `advisor` with empty `input` (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `advisor_tool_result` | `assistant` | The answer to a `server_tool_use`, stored in the same assistant message rather than in a `user` record. The corpus contains answers for 44 of 45 calls; one call has no answer (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 — both result shapes and the unanswered call |
| `advisor_tool_result.content` | `assistant` | The result object, whose `type` says which shape it is | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `advisor_tool_result.content.type` | `assistant` | Either `advisor_tool_result_error` or `advisor_redacted_result`. Neither shape carries readable output | `tests/fixtures/server_tools/`, CC 2.1.201 — holds both |
| `advisor_tool_result.content.error_code` | `assistant` | Why the advisor failed, on the error shape | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `advisor_tool_result.content.encrypted_content` | `assistant` | The advisor's answer, unreadable: the transcript records that it answered and nothing of what it said | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `fallback` | `assistant` | A retry on another model. The block also carries a `to`, but all three corpus blocks occur in one session and agree with `message.model` there, so only `from` adds information (scanned 2026-08-07). This is not a `model_consent_fallback`, which changes the whole session's model | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `fallback.from` | `assistant` | The model the request first went to | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `fallback.from.model` | `assistant` | The model this side of the retry names | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `toolUseResult` | `user` | The tool's structured report beside the result block. Most are objects, but 3,590 of 137,255 corpus values are strings and 795 are lists (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220; `tests/fixtures/fork_origin/`, CC 2.1.215 — a string-valued one |
| `toolUseResult.persistedOutputPath` | `user` | The path to output too large for the transcript. Claude Code writes the full output to `<session>/tool-results/<name>.txt` and leaves a preview in `content`. The path is absolute, so only its file name travels; the corpus holds 321 such results (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |
| `toolUseResult.runId` | `user` | The fan-out id a `Workflow` call returns, matching the `wf_<id>` directory that holds its agents' transcripts. It is the only link from those transcripts to the call that launched them | `tests/fixtures/workflow/`, CC 2.1.207 |
<!-- aigarden:end -->

### API replies, models, and tokens

<!-- aigarden:cog sh "uv run python -m tools.gen_schema api" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message.id` | `assistant` | The API reply id, and the key for merging records. One reply can span several records, one per content block; counting lines triples the API-call count | `tests/fixtures/spine/`, CC 2.1.221 — eight records for two replies |
| `message.model` | `assistant` | The model that answered. `<synthetic>` marks Claude Code's placeholder for an interrupt or a cancelled request: of about 290,000 corpus assistant records, 205 are synthetic, all reporting zero tokens and omitting `usage.inference_geo` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.201 — holds a `<synthetic>` reply |
| `message.stop_reason` | `assistant` | Why generation stopped, such as `tool_use` or `end_turn` | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.usage` | `assistant` | Token usage for the whole reply. Every record sharing a `message.id` repeats the totals, so summing records multiplies usage by the number of chunks | `tests/fixtures/spine/`, CC 2.1.221 — five identical copies under one id |
| `usage.input_tokens` | `assistant` | Tokens sent that neither hit nor filled the cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.output_tokens` | `assistant` | Tokens the model generated | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_read_input_tokens` | `assistant` | Tokens served from the cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_creation_input_tokens` | `assistant` | Tokens written to the cache. It should equal the sum of the two `cache_creation` splits, but 53 of about 290,000 mycelia assistant records disagree, and cost uses the split (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_creation` | `assistant` | Cache-creation tokens split by TTL. Every assistant record in the mycelia corpus has this object, so the absent shape remains unrecorded (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `cache_creation.ephemeral_5m_input_tokens` | `assistant` | Tokens written to the five-minute cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `cache_creation.ephemeral_1h_input_tokens` | `assistant` | Tokens written to the one-hour cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `attributionSkill` | `assistant` | The skill loaded when the reply returned. Absent when none was loaded | `tests/fixtures/spine/`, CC 2.1.221 |
| `effort` | `assistant` | The reasoning-effort setting as an opaque string, such as `"high"` | `tests/fixtures/spine/`, CC 2.1.221 |
| `requestId` | `assistant` | The API request id the reply came back on | `tests/fixtures/spine/`, CC 2.1.221 |
<!-- aigarden:end -->

### System events and session labels

<!-- aigarden:cog sh "uv run python -m tools.gen_schema events" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `durationMs` | `system` / `turn_duration` | The turn's wall-clock duration in milliseconds. Sum these to measure active session time; the transcript's timestamp span includes idle hours | `tests/fixtures/spine/`, CC 2.1.221 |
| `compactMetadata` | `system` / `compact_boundary` | The compaction's own numbers. Read compaction from this object rather than inferring it from the nearest assistant call; all 1,026 corpus boundaries carry it (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.trigger` | `system` / `compact_boundary` | `auto` when Claude Code hit the context limit, `manual` when the operator asked: 933 and 93 of 1,026 corpus boundaries (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 — one of each |
| `compactMetadata.preTokens` | `system` / `compact_boundary` | Context size before the compaction | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.postTokens` | `system` / `compact_boundary` | Context size after it | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.durationMs` | `system` / `compact_boundary` | How long the compaction itself took | `tests/fixtures/compaction/`, CC 2.1.198 |
| `content` | `system` / `local_command` | The `<local-command-stdout>` text, when Claude Code recorded the output as a `system` record rather than a `user` one: 37 of 316 corpus outputs. The body can span lines and can be empty | `tests/fixtures/model_only/`, CC 2.1.215 — an empty `/clear` body |
| `originalModel` | `system` / `model_consent_fallback` | The model the session asked for and did not get: it needed credits the account lacked | `tests/fixtures/registry_zoo/`, CC 2.1.221 |
| `fallbackModel` | `system` / `model_consent_fallback` | The model it ran on instead | `tests/fixtures/registry_zoo/`, CC 2.1.221 |
| `choice` | `system` / `model_consent_fallback` | What the operator answered, such as `cancelled` | `tests/fixtures/registry_zoo/`, CC 2.1.221 |
| `persistedAsDefault` | `system` / `model_consent_fallback` | Whether the change outlived the session | `tests/fixtures/registry_zoo/`, CC 2.1.221 |
| `customTitle` | `custom-title` | The session title the operator set. It stays current beside `aiTitle`, and 13 of 398 titled mycelia sessions carry both (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `aiTitle` | `ai-title` | The session title Claude Code wrote for itself, revised as work goes on | `tests/fixtures/legacy_title/`, CC 2.1.196; `tests/fixtures/spine/`, CC 2.1.221 |
| `agentName` | `agent-name` | Claude Code rewrites this with the title, so it holds no name of its own to show: all 84 of the canonical store's 596 sessions that carry one hold exactly that session's title (scanned 2026-08-25) | `tests/fixtures/spine/`, CC 2.1.201 — the record's shape; corpus scan: the canonical store, every version it holds, scanned 2026-08-25 |
| `prNumber` | `pr-link` | The pull request number. The same PR can recur within a session, so key each link by its line: all 2,885 corpus records carry these three fields plus `type`, `sessionId`, and `timestamp` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `prUrl` | `pr-link` | The pull request's URL | `tests/fixtures/spine/`, CC 2.1.221 |
| `prRepository` | `pr-link` | The `owner/name` repository it belongs to | `tests/fixtures/spine/`, CC 2.1.221 |
| `parentSessionId` | `fork-context-ref` | The conversation this transcript continues | `tests/fixtures/fork_byref/`, CC 2.1.202 |
| `parentLastUuid` | `fork-context-ref` | The parent record work resumes after | `tests/fixtures/fork_byref/`, CC 2.1.202 |
| `contextLength` | `fork-context-ref` | How much of the parent's context the fork carried over | `tests/fixtures/fork_byref/`, CC 2.1.202 |
<!-- aigarden:end -->

## Read transcript records by these rules

### A leading tag distinguishes prompts from other records

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

A resumed session can replay the same output under the plain turn that now precedes it. The corpus contains 183 such records. If `parentUuid` points to a turn that ran no command, the output has no owning turn in that thread; the archive is not malformed.

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

Every `system` / `compact_boundary` record has a corresponding `user` record with `isCompactSummary`. The mycelia corpus contains 1,026 of each, with matching counts in every file (CC 2.1.191–2.1.221; scanned 2026-08-07).

Subagents compact much more often than main threads. Attribute a compaction to the file that reached the limit, not to the session as a whole.

*Evidence:* `tests/fixtures/compaction/`, CC 2.1.198, contains one `auto` and one `manual` boundary, each with its summary.

### Preserve both cache-creation totals

The total and the split disagree in 53 of about 290,000 mycelia assistant records, as the table above records. The extractor stores the total as `cache_creation_tokens` and the split as `cache_5m_tokens` and `cache_1h_tokens`. Cost uses the split when present, so those 53 calls use a value that the total does not confirm.

### Split records only on newline characters

String values can contain raw U+2028 and U+2029 separators. Python's `splitlines()` treats them as record boundaries and breaks JSON objects. Split transcript files on `"\n"`.

## Session data comes from three places

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` stores one session transcript as one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/` stores the session directory described below; `hyphae.sessions` walks this tree
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

### Read a run's ask and answer off the call that spawned it

What a run was asked and what it answered are not in the meta. Both are on the spawning call: its `prompt` and its `result`. Read the field rather than the tool name, because the tool is not always `Agent` and a fan-out shares one call among many runs:

```sql
-- data/traces.duckdb, every agent run, no time window. Scanned 2026-08-25.
SELECT tc.name,
       count(*) AS runs,
       count(DISTINCT (tc.session_id, tc.id)) AS spawning_calls,
       count(json_extract_string(tc.input, '$.prompt')) AS with_prompt,
       count(tc.result) AS with_result
FROM agent_runs a
JOIN tool_calls tc ON tc.session_id = a.session_id
                  AND tc.id = a.tool_use_id AND tc.source <> a.id
GROUP BY ALL;
```

| `name` | Runs | Spawning calls | With `prompt` | With `result` |
| --- | ---: | ---: | ---: | ---: |
| `Agent` | 2,555 | 2,555 | 2,555 | 2,554 |
| `Workflow` | 180 | 6 | 0 | 180 |

So 180 of the 2,735 runs with a spawning call — 6.6% — have no ask to read, because a fan-out is launched once and the launcher is asked in other words. The one `Agent` run without a result is a run whose parent received nothing. No result in the store is JSON, so what comes back is prose.

Count runs, not calls. `tool_calls.id` is unique within a session, not across the store: the same query keyed on `id` alone counts 2,629 `Agent` rows, 74 of which belong to a session whose runs point at something else.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains an `Agent` call carrying a `prompt` and the result it returned; `tests/fixtures/workflow/`, CC 2.1.207, contains the `Workflow` call, whose input is a name and its arguments.

### Join a workflow agent through its launcher's run id

A fan-out does not spawn agents one by one, so its agent metas name no call. The launching `Workflow` call returns `toolUseResult.runId`, which matches the `wf_<id>` directory containing the agent transcripts. This is the only link from those transcripts to their launching tool call. All six workflow runs on the recording machine contain it (scanned 2026-08-07).

*Evidence:* `tests/fixtures/workflow/`, CC 2.1.207, contains the `Workflow` call, its result, and the named `wf_c30cc877-997` directory.

### Attribute copied history to the transcript that ran it first

All 52 observed fork metas pair `isFork: true` with `agentType: "fork"` (scanned 2026-08-07). The first transcript record identifies one of two fork shapes:

| First record | Forks | Meaning |
| --- | ---: | --- |
| `fork-context-ref` | 26 | The file copies no records. The opening record names `parentSessionId`, `parentLastUuid`, and `contextLength`; work begins mid-conversation |
| `user` or `system` | 26 | The file copies the parent's records verbatim, including uuids and timestamps, then appends the fork's work |

A copy is the original but for `agentId`, which each file rewrites to its own: of the 2,006 pairs of records that share a uuid across two transcripts of one session, on this machine's twelve such sessions, every pair differs there and no pair differs only elsewhere (scanned 2026-08-30). A copied record then appears in two files. The corpus contains 51 overlapping transcript pairs, each with a fork on one side; 25 are fork-to-fork, where one fork copies another's work. Attribute each record to the transcript that ran it first. Keep later copies but mark them `replayed`, so the archive retains what each file recorded without double-counting the work. This rule marks 1,617 records across nine sessions as replays. None appears in a non-fork transcript; such a replay would show that the ordering chose the wrong origin.

Order transcripts by `(spawnDepth, first timestamp, agentId)`, with the main transcript first. Depth must lead because a copied-history fork begins with its parent's timestamp. Of 51 overlapping pairs, 46 tie on the first timestamp; breaking those ties by agent id would wrongly assign 335 records from six original transcripts to their forks. A fork is spawned by the transcript it copies and is therefore deeper.

The one meta without `spawnDepth` sorts last. Its transcript, the subagent file `agent-a20276f6d8a4e5309.jsonl` under the `mac_settings` project, from CC 2.1.186, shares no uuid with a sibling, so its position does not affect attribution.

*Evidence:* `tests/fixtures/fork_origin/`, CC 2.1.215, contains a copied-history fork and the auditor it copied; `tests/fixtures/fork_byref/`, CC 2.1.202, begins with `fork-context-ref`.
