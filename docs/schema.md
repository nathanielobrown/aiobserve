# Telemetry schema

Every Claude Code telemetry field hyphae reads, what it means, and the recording that proves it. Read this before writing a query or an analysis: misreading a field turns a bad premise into a confident finding.

The span schema will arrive with the span importer. Its source shapes come from `mac_settings/claude-otel/`, which we have not documented here. Until that importer exists, don't describe span fields from memory.

## Every schema claim needs a recording

For each field, cite a recorded session and the Claude Code version that wrote it. Claude Code owns these shapes and can change them without notice.

Prefer a checked-in fixture. The fixture directory's README names its source session and version, so readers can verify the claim. If no fixture can preserve the evidence, name the corpus scan and its date. Mark an inferred mechanism as an inference.

The transcript-field tables under the next heading are generated. A field's meaning and its citation are declared on the record model that carries it, in `src/hyphae/extract/records/`; document a new field there and run `mise run cogs`. A field declared without a citation fails the generator instead of printing an empty cell.

What the fields mean together — which records start a turn, which timestamps were measured — is [reading transcript records](transcript-reading.md).

Where the files these records come from sit on disk, and how the extractor joins them, is [session layout](session-layout.md).

## Transcript records are typed JSON objects

A transcript stores one JSON object per line. Each object has a `type`. `hyphae.extract.records.registry` registers every type it has seen and the readers crash on unknown types. Treat that registry—not the tables below—as the current census.

### Record identity and session context

<!-- aigarden:cog sh "uv run python -m tools.gen_schema identity" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `type` | every record | The record shape. Known values include `user`, `assistant`, `system`, `attachment`, `summary`, and about a dozen bookkeeping types | `tests/fixtures/registry_zoo/` — holds one record of every registered type |
| `subtype` | `system` | The system event. The registry zoo holds ten, including `turn_duration`, `compact_boundary`, and `api_error` | `tests/fixtures/registry_zoo/` — one record of every registered subtype |
| `sessionId` | `user`, `assistant`, `system`, `custom-title`, `ai-title`, `agent-name`, `pr-link` | The session id Claude Code wrote into the record. Nothing reads it: the extractor takes the session id from the file name | `tests/fixtures/spine/`, CC 2.1.221 |
| `session_id` | `user`, `assistant`, `system` | A second session id in snake_case, which does not always agree with `sessionId`: a resumed transcript copies the original id here while `sessionId` follows the file, and 58 of 99 fixture records disagree. Nothing reads either | `tests/fixtures/resume_pair/`, CC 2.1.205 — 52 of 54 disagree with `sessionId` |
| `agentId` | `user`, `assistant`, `system`, `fork-context-ref` | The agent run the record belongs to. A subagent's transcript is `<session>/subagents/agent-<agentId>.jsonl`, so the id is its file name without the prefix | `tests/fixtures/spine/`, CC 2.1.221 — every record of each subagent thread |
| `uuid` | `user`, `assistant`, `system` | The record id within its file. It is not unique: rewinding can write new records under existing uuids, and the extractor keeps the last | `tests/fixtures/dup_uuid/`, CC 2.1.211 — five uuids twice each |
| `parentUuid` | `user`, `assistant`, `system` | The record this one answers, or null at the start of a thread. A `<local-command-stdout>` record points at the command turn whose output it is | `tests/fixtures/spine/`, CC 2.1.221 |
| `timestamp` | `user`, `assistant`, `system`, `pr-link` | A UTC ISO-8601 timestamp with a `Z` suffix. File order is not timestamp order; adjacent records can move backward by one millisecond | `tests/fixtures/spine/`, CC 2.1.221 |
| `cwd` | `user`, `assistant`, `system` | The project directory, absolute and symlink-free. Resolve a command-line path before matching it — `hyphae.projects.resolve_project` does. Early bookkeeping records omit it, so reading only the first record yields nulls | `tests/fixtures/spine/`, CC 2.1.221 — the first three records have none |
| `gitBranch` | `user`, `assistant`, `system` | The branch checked out when the record was written | `tests/fixtures/spine/`, CC 2.1.221 |
| `version` | `user`, `assistant`, `system` | The Claude Code version that wrote the record, and the version every schema claim here is dated by | `tests/fixtures/spine/`, CC 2.1.221 |
| `entrypoint` | `user`, `assistant`, `system` | How the session was launched, such as `cli` | `tests/fixtures/spine/`, CC 2.1.221; absent from `tests/fixtures/legacy_entrypoint/`, CC 1.0.128 — the oldest corpus transcripts |
| `userType` | `user`, `assistant`, `system` | Who the record is attributed to. Every fixture record says `external`, so no other value is recorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `sessionKind` | `user`, `assistant`, `system` | What kind of session Claude Code was recording. Redacted in the one fixture that carries it, so no value is recorded | `tests/fixtures/resume_pair/`, CC 2.1.205 |
| `slug` | `user`, `assistant`, `system` | A short name Claude Code gives the session. The fixtures redact it, so its presence is what is recorded and not how it is derived | `tests/fixtures/spine/`, CC 2.1.221 |
| `isMeta` | `user`, `system` | Claude Code wrote the record on the user's behalf, such as a caveat or a hook echo. It is not a prompt | `tests/fixtures/spine/`, CC 2.1.221 |
| `isCompactSummary` | `user` | Claude Code wrote the record after compaction to replace the dropped context. It is not a prompt, and every one has a `compact_boundary` record beside it | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `isSidechain` | `user`, `assistant`, `system` | The record belongs to a subagent stream. On a main thread, skip it because the subagent's own file records the work better. On a subagent thread every record carries it, and skipping those would remove every turn | `tests/fixtures/spine/`, CC 2.1.221 — holds both main and subagent records |
| `forkedFrom` | `user`, `assistant`, `system` | Where the session was forked from, on every record the fork carried over. One corpus session has it, on 299 records here and 151 more that are archived unread. Nothing reads it: a fork's copied rows are found by their content | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.220` writes it |
| `forkedFrom.sessionId` | `user`, `assistant`, `system` | The session the fork was cut from | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `forkedFrom.messageUuid` | `user`, `assistant`, `system` | The record in that session the fork was cut at | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
<!-- aigarden:end -->

Of 240 encoded working directories under `~/.claude/projects` on the recording machine, 181 lie under a symlinked root—153 under `-private-var` and 28 under `-private-tmp`—but none uses the unresolved `-var-…` or `-tmp-…` spelling (scanned 2026-08-15). The likely mechanism is Node's `process.cwd()`, which returns a physical path, but that mechanism is inferred. No fixture demonstrates it because every fixture session ran under an unsymlinked path.

### User and assistant content

<!-- aigarden:cog sh "uv run python -m tools.gen_schema content" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message` | `user`, `assistant` | The API message the record carried: a role and its content | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.role` | `user`, `assistant` | `user` or `assistant`, repeating what the record's own `type` says | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content` | `user`, `assistant` | Either a string or a list of the blocks below. A `user` record whose list holds a `tool_result` is plumbing, not a prompt | `tests/fixtures/spine/`, CC 2.1.220 — for the block form |
| `message.content.text` | `user`, `assistant` | Prose, under `text`: the model's answer, or a prompt written in block form | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.text.text` | `user`, `assistant` | The prose itself, which can be empty | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.thinking` | `assistant` | The model's reasoning, under `thinking`, beside the `signature` that lets it be replayed | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.thinking.thinking` | `assistant` | The reasoning text, which no store column carries | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.thinking.signature` | `assistant` | The opaque token that lets the reasoning be replayed to the model. Every fixture `thinking` block carries one | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.image` | `user` | A picture in a message's own content list, pasted by the operator rather than returned by a tool. Three records in the canonical store hold one (scanned 2026-09-04) | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — 3 blocks in a `user` content list, 633 inside a `tool_result` |
| `message.content.image.source` | `user` | The picture itself, as a `type`, a `media_type` and base64 `data`. Nothing has opened it, so its interior is undeclared — and its `data` is the largest value a transcript holds | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — 3 blocks in a `user` content list, 633 inside a `tool_result` |
| `message.content.tool_use` | `assistant` | A local tool request. Most records contain one, but 23 records in the mycelia corpus contain two or more, so counting records undercounts calls (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221; `tests/fixtures/parallel_tools/`, CC 2.1.211 — two calls in one record |
| `message.content.tool_use.id` | `assistant` | The call id. A `tool_result` block names it in `tool_use_id`, and a subagent's meta names it in `toolUseId`. Unique within a session, not across the store | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_use.name` | `assistant` | The tool asked for, such as `Bash` or `Agent` | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_use.input` | `assistant` | The arguments, shaped by the tool. On a `Skill` call it names the invoked skill in `skill`, with `args` on 81 of 326 corpus calls; that records invocation, while `attributionSkill` records what was loaded when the reply returned. They can disagree, and a skill reached through a slash command creates no `Skill` call (57 sessions, CC 2.1.195–2.1.221; scanned 2026-08-08) | `tests/fixtures/spine/`, CC 2.1.221; corpus scan: 57 sessions, CC 2.1.195–2.1.221, scanned 2026-08-08 — the `Skill` shape |
| `message.content.tool_use.caller` | `assistant` | Who asked for the call, as an object holding a `kind`. Every one of the 214,583 corpus blocks says `direct` (scanned 2026-09-04), and nothing has opened it, so its interior is undeclared | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result` | `user` | A local tool's reply, written in the `user` record that answers the call | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.tool_use_id` | `user` | The `tool_use` block this answers | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.is_error` | `user` | Present when the tool failed. Success omits it: 66,653 of 154,169 corpus result blocks have no `is_error` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.content` | `user` | A string, or a list of `text`, `image`, and `tool_reference` blocks. Only text carries into `ToolCall.result` | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.content.text` | `user` | Prose a tool returned, inside a block-form `tool_result`. The only part that carries into `ToolCall.result` | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.content.text.text` | `user` | What the tool printed | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.content.image` | `user` | A picture a tool returned, inside a block-form `tool_result`. It carries no text, so nothing of it reaches `ToolCall.result` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — 3 blocks in a `user` content list, 633 inside a `tool_result` |
| `message.content.tool_result.content.image.source` | `user` | The picture itself, as a `type`, a `media_type` and base64 `data`. Nothing has opened it, so its interior is undeclared — and its `data` is the largest value a transcript holds | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — 3 blocks in a `user` content list, 633 inside a `tool_result` |
| `message.content.tool_result.content.tool_reference` | `user` | A tool the result pointed at rather than anything the tool said | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.tool_result.content.tool_reference.tool_name` | `user` | The tool the result named | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content.server_tool_use` | `assistant` | A tool request Anthropic ran server-side, with the same fields as `tool_use`. It shares the assistant stream but joins no batch, so its own timestamp is the call's start. All 45 corpus blocks, across five sessions, call `advisor` with empty `input` (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.server_tool_use.id` | `assistant` | The call id, which the `advisor_tool_result` block answering it repeats in `tool_use_id` | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.server_tool_use.name` | `assistant` | The server-side tool asked for; every corpus block says `advisor` | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.server_tool_use.input` | `assistant` | The arguments, empty on every corpus block, so no argument shape is recorded | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.advisor_tool_result` | `assistant` | The answer to a `server_tool_use`, stored in the same assistant message rather than in a `user` record. The corpus contains answers for 44 of 45 calls; one call has no answer (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 — both result shapes and the unanswered call |
| `message.content.advisor_tool_result.tool_use_id` | `assistant` | The `server_tool_use` block this answers | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.advisor_tool_result.content` | `assistant` | The result object, whose `type` says which shape it is | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.advisor_tool_result.content.type` | `assistant` | Either `advisor_tool_result_error` or `advisor_redacted_result`. Neither shape carries readable output | `tests/fixtures/server_tools/`, CC 2.1.201 — holds both |
| `message.content.advisor_tool_result.content.error_code` | `assistant` | Why the advisor failed, on the error shape | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.advisor_tool_result.content.encrypted_content` | `assistant` | The advisor's answer, unreadable: the transcript records that it answered and nothing of what it said | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `message.content.fallback` | `assistant` | A retry on another model. The block also carries a `to`, but all three corpus blocks occur in one session and agree with `message.model` there, so only `from` adds information (scanned 2026-08-07). This is not a `model_consent_fallback`, which changes the whole session's model | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `message.content.fallback.from` | `assistant` | The model the request first went to | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `message.content.fallback.from.model` | `assistant` | The model this side of the retry names | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `message.content.fallback.to` | `assistant` | The model it retried on. All three corpus blocks agree with `message.model` here, so nothing reads it (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `message.content.fallback.to.model` | `assistant` | The model this side of the retry names | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `toolUseResult` | `user` | The tool's structured report beside the result block. Most are objects, but 3,590 of 137,255 corpus values are strings and 795 are lists (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220; `tests/fixtures/fork_origin/`, CC 2.1.215 — a string-valued one |
| `toolUseResult.persistedOutputPath` | `user` | The path to output too large for the transcript. Claude Code writes the full output to `<session>/tool-results/<name>.txt` and leaves a preview in `content`. The path is absolute, so only its file name travels; the corpus holds 321 such results (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |
| `toolUseResult.runId` | `user` | The fan-out id a `Workflow` call returns, matching the `wf_<id>` directory that holds its agents' transcripts. It is the only link from those transcripts to the call that launched them | `tests/fixtures/workflow/`, CC 2.1.207 |
| `promptId` | `user` | An id Claude Code gives the record's prompt. It is not the record's own `uuid` — the two differ on all 84 fixture records that carry both | `tests/fixtures/spine/`, CC 2.1.221 |
| `promptSource` | `user` | Where the prompt came from. Redacted in every fixture, so no value is recorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `origin` | `user` | Where the record came from, as an object holding a `kind`. Nothing has opened it, so its interior is undeclared | `tests/fixtures/spine/`, CC 2.1.221 |
| `permissionMode` | `user` | The permission mode in force when the record was written: `default`, `auto` and `bypassPermissions` in the fixtures | `tests/fixtures/spine/`, CC 2.1.221 |
| `thinkingMetadata` | `user` | The thinking budget in force, as a `level`, a `disabled` flag and `triggers`. Nothing has opened it, so its interior is undeclared | `tests/fixtures/legacy_entrypoint/`, CC 1.0.128 |
| `classifierMetaLines` | `user` | What a classifier noted about the prompt, as a JSON document held in a string rather than an object — 952 of the 960 corpus values parse and 8 do not. Nothing has opened it, so its interior is undeclared | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `isVisibleInTranscriptOnly` | `user` | The record is shown when reading the transcript back and nowhere else. Recorded only as true, so the false shape is unrecorded | `tests/fixtures/compaction/`, CC 2.1.198 |
| `sourceToolAssistantUUID` | `user` | The assistant record this one answers, by uuid. Nothing reads it: a result is joined to its call through `tool_use_id` | `tests/fixtures/spine/`, CC 2.1.221 |
| `sourceToolUseID` | `user` | The tool call this record answers, by `tool_use` id. It names the same link as `sourceToolAssistantUUID` and never appears beside it — 613 corpus records carry one and none carries both. Nothing reads either | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `interruptedMessageId` | `user` | The reply an interruption stopped. One fixture record carries it | `tests/fixtures/spine/`, CC 2.1.220 |
| `imagePasteIds` | `user` | The images pasted into the prompt, by id. Two corpus records carry the list | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `mcpMeta` | `user` | What an MCP tool returned beside its result, as an object holding `_meta` and `structuredContent`. One corpus record carries it, which is too thin to declare an interior on | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
<!-- aigarden:end -->

### API replies, models, and tokens

<!-- aigarden:cog sh "uv run python -m tools.gen_schema api" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `message.id` | `assistant` | The API reply id, and the key for merging records. One reply can span several records, one per content block; counting lines triples the API-call count | `tests/fixtures/spine/`, CC 2.1.221 — eight records for two replies |
| `message.type` | `assistant` | The API envelope's own kind: `message` on every fixture reply | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.model` | `assistant` | The model that answered. `<synthetic>` marks Claude Code's placeholder for an interrupt or a cancelled request: of about 290,000 corpus assistant records, 205 are synthetic, all reporting zero tokens and omitting `usage.inference_geo` (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.201 — holds a `<synthetic>` reply |
| `message.stop_reason` | `assistant` | Why generation stopped, such as `tool_use` or `end_turn` | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.stop_sequence` | `assistant` | The stop sequence that ended generation. Null on every fixture reply but one, which carries an empty string, so a real sequence is unrecorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.stop_details` | `assistant` | More about why generation stopped, beside `stop_reason`. Null on every fixture reply, so its interior is unrecorded as well as undeclared | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.container` | `assistant` | The container a code-execution reply ran in. Recorded once, as null, so its shape is unrecorded | `tests/fixtures/spine/`, CC 2.1.201 |
| `message.context_management` | `assistant` | What context management did to the request, as an `applied_edits` list. Nothing has opened it, so its interior is undeclared | `tests/fixtures/parallel_tools/`, CC 2.1.211 |
| `message.diagnostics` | `assistant` | Why the prompt cache missed, when it did: a `cache_miss_reason` naming the cause and what it cost. Null when the cache hit. Nothing has opened it | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.usage` | `assistant` | Token usage for the whole reply. Every record sharing a `message.id` repeats the totals, so summing records multiplies usage by the number of chunks | `tests/fixtures/spine/`, CC 2.1.221 — five identical copies under one id |
| `usage.input_tokens` | `assistant` | Tokens sent that neither hit nor filled the cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.output_tokens` | `assistant` | Tokens the model generated | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_read_input_tokens` | `assistant` | Tokens served from the cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_creation_input_tokens` | `assistant` | Tokens written to the cache. It should equal the sum of the two `cache_creation` splits, but 53 of about 290,000 mycelia assistant records disagree, and cost uses the split (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.cache_creation` | `assistant` | Cache-creation tokens split by TTL. Every assistant record in the mycelia corpus has this object, so the absent shape remains unrecorded (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `cache_creation.ephemeral_5m_input_tokens` | `assistant` | Tokens written to the five-minute cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `cache_creation.ephemeral_1h_input_tokens` | `assistant` | Tokens written to the one-hour cache | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.output_tokens_details` | `assistant` | How the generated tokens break down. Claude Code added it late: 114 corpus records in 2 sessions carry it, all written by `2.1.259` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `output_tokens_details.thinking_tokens` | `assistant` | How many of `output_tokens` the model spent thinking. Across the 114 corpus records carrying the object it runs from 0 to 3,241, never above `output_tokens`, so it is a share of that total and not an addition to it | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `usage.server_tool_use` | `assistant` | How many server-side tool requests the reply made, by kind. Zero on every fixture reply, `server_tools/` included, so a non-zero count is unrecorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.service_tier` | `assistant` | The API service tier the reply was served on | `tests/fixtures/spine/`, CC 2.1.221 — `standard` wherever the fixtures leave it unredacted |
| `usage.speed` | `assistant` | The speed tier the reply was served at. Absent from 30 of the 108 fixture replies, across versions that carry it elsewhere, so its absence is not a version fact | `tests/fixtures/spine/`, CC 2.1.221 — `standard` wherever the fixtures leave it unredacted |
| `usage.inference_geo` | `assistant` | Where inference ran, or `not_available`. The one `<synthetic>` reply — Claude Code's own placeholder rather than a model answer — nulls it, along with `service_tier`, `speed` and `iterations` | `tests/fixtures/spine/`, CC 2.1.221 |
| `usage.iterations` | `assistant` | Token counts for each pass a reply took, in this object's own shape. Cost uses the totals above, and nothing has opened these, so their interior is undeclared | `tests/fixtures/spine/`, CC 2.1.221 |
| `attributionSkill` | `assistant` | The skill loaded when the reply returned. Absent when none was loaded | `tests/fixtures/spine/`, CC 2.1.221 |
| `attributionAgent` | `assistant` | The agent the reply is attributed to, beside `attributionSkill`. Redacted in the fixtures, so no value is recorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `advisorModel` | `assistant` | The model behind a server-side advisor call. Only `server_tools/` records one, which is also the only fixture holding a `server_tool_use` block | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `effort` | `assistant` | The reasoning-effort setting as an opaque string, such as `"high"` | `tests/fixtures/spine/`, CC 2.1.221 |
| `requestId` | `assistant` | The API request id the reply came back on | `tests/fixtures/spine/`, CC 2.1.221 |
| `isApiErrorMessage` | `assistant` | The reply is Claude Code's own report of an API error rather than the model's. Recorded once, as false, so the true shape is unrecorded | `tests/fixtures/spine/`, CC 2.1.201 |
| `error` | `assistant` | What failed, when the reply is Claude Code's error report. Every one of the 222 corpus records carrying it also says `isApiErrorMessage`, and the values are `rate_limit`, `server_error`, `oauth_org_not_allowed`, `authentication_failed` and `model_not_found` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `errorDetails` | `assistant` | More about that failure, as a free string. Six corpus records carry it, each beside an `error` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `apiErrorStatus` | `assistant` | The HTTP status behind the failure. The 181 corpus records carrying one say 429, 403, 529, 404 or 500 | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `apiBlockIndex` | `assistant` | Which block of the reply this record holds, counting from zero within the API message. Claude Code added it late: 220 corpus records over 90 message ids, all written by `2.1.259`, running 0 to 5 | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `attributionMcpServer` | `assistant` | The MCP server the reply is attributed to, beside `attributionSkill`. Always written with `attributionMcpTool`: 4,732 corpus records in 27 sessions carry both and none carries one alone | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `attributionMcpTool` | `assistant` | The tool on that server, beside `attributionMcpServer` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
<!-- aigarden:end -->

### System events and session labels

<!-- aigarden:cog sh "uv run python -m tools.gen_schema events" -->
| Field | Records | Meaning | Evidence |
| --- | --- | --- | --- |
| `durationMs` | `system` / `turn_duration` | The turn's wall-clock duration in milliseconds. Sum these to measure active session time; the transcript's timestamp span includes idle hours | `tests/fixtures/spine/`, CC 2.1.221 |
| `messageCount` | `system` / `turn_duration` | A message count Claude Code writes beside the duration. It reaches 466 in one fixture turn, so it counts more than the turn's own records; nothing reads it | `tests/fixtures/spine/`, CC 2.1.221 |
| `pendingBackgroundAgentCount` | `system` / `turn_duration` | How many background agent runs were still going when the turn ended | `tests/fixtures/spine/`, CC 2.1.221 |
| `pendingWorkflowCount` | `system` / `turn_duration` | How many workflows were still going when the turn ended, beside `pendingBackgroundAgentCount`. Three corpus records carry it, each saying 1 | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `compactMetadata` | `system` / `compact_boundary` | The compaction's own numbers. Read compaction from this object rather than inferring it from the nearest assistant call; all 1,026 corpus boundaries carry it (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.trigger` | `system` / `compact_boundary` | `auto` when Claude Code hit the context limit, `manual` when the operator asked: 933 and 93 of 1,026 corpus boundaries (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 — one of each |
| `compactMetadata.preTokens` | `system` / `compact_boundary` | Context size before the compaction | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.postTokens` | `system` / `compact_boundary` | Context size after it | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.durationMs` | `system` / `compact_boundary` | How long the compaction itself took | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.cumulativeDroppedTokens` | `system` / `compact_boundary` | Tokens every compaction in the thread has dropped so far, this one included, so it does not reduce to `preTokens` minus `postTokens` | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.preCompactDiscoveredTools` | `system` / `compact_boundary` | The tools the thread had discovered before compacting, by name | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.preservedMessages` | `system` / `compact_boundary` | Which records survived, as an anchor uuid and the uuids kept. Nothing has opened it, so its interior is undeclared | `tests/fixtures/compaction/`, CC 2.1.198 |
| `compactMetadata.preservedSegment` | `system` / `compact_boundary` | The span of records the compaction kept, by head, anchor and tail uuid. Nothing has opened it, so its interior is undeclared | `tests/fixtures/compaction/`, CC 2.1.198 |
| `toolDenialKind` | `user` | Why a tool call was refused. The 306 corpus records carrying one say `automode-blocked`, `permission-rule`, `automode-unavailable`, `user-rejected` or `automode-parsing-error` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `userFeedback` | `user` | What the operator said when refusing a tool call. Both corpus records carrying it also say `toolDenialKind: user-rejected` | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `toolEndsTurn` | `user` | The tool result ends the turn rather than feeding another reply. Recorded only as true, on 108 records in 2 corpus sessions, so the false shape is unrecorded | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `turnCompanion` | `user` | The record rides along with a turn rather than opening one. Recorded only as true, on 5 records written by `2.1.259`, so the false shape is unrecorded | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `queuePriority` | `user` | Where a queued prompt sits in the queue. All 89 corpus values are `later`, so no other is recorded | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 |
| `queueSkipAttachments` | `user` | The queued prompt went in without its attachments. Recorded only as true, on 3 records written by `2.1.259`, so the false shape is unrecorded | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `scheduledTaskId` | `user` | The scheduled task that wrote the record. One corpus record carries it | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `scheduledFireId` | `user` | The one firing of that task, beside `scheduledTaskId`. The same corpus record carries both | corpus scan: the canonical store, 705,431 records in 630 sessions, scanned 2026-09-04 — only `2.1.259` writes it |
| `logicalParentUuid` | `system` / `compact_boundary` | The record the boundary answers in the conversation, beside `parentUuid`, which answers the file. Nothing reads it | `tests/fixtures/compaction/`, CC 2.1.198 |
| `level` | `system` | How loud the event is: `info`, `warning`, `error` and `suggestion` in the fixtures | `tests/fixtures/compaction/`, CC 2.1.198 |
| `content` | `system` | The event's own text. On a `local_command` it is the `<local-command-stdout>` body, which Claude Code writes here rather than on a `user` record for 37 of 316 corpus outputs; the body can span lines and can be empty | `tests/fixtures/model_only/`, CC 2.1.215 — an empty `/clear` body |
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
