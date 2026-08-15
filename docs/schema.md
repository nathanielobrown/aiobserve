# Telemetry schema

What each field in an AI coding session means and where it comes from. Read this before writing a query or an analysis — misreading a field produces a confident wrong finding.

The span schema is still missing: it arrives with the OTel importer, extracted from `mac_settings/claude-otel/` in a later step. Until then, do not describe a span field here from memory.

## The rule for adding a field

Every entry names a **recorded session** that demonstrates the field and the **Claude Code version** that produced it. The harness owns these shapes and changes them without notice, so a field documented from memory is a guess that reads like a fact.

Cite a fixture where one exists — its directory README names the source session and version, and the file is checked in, so readers can verify the claim rather than take it on trust.

## Transcript records

Every line of a transcript is a JSON object with a `type`. `aiobserve.extract.claude_code` registers each type it has seen and crashes on one it has not; the registry's members are the census, not this table.

| Field | Where it comes from | What it means | Seen in |
| --- | --- | --- | --- |
| `type` | every record | Which record shape this line is. `user`, `assistant`, `system`, `attachment`, `summary` and a dozen bookkeeping types | `tests/fixtures/registry_zoo/` — one record of every type |
| `subtype` | `system` records | Which of the ten system events this is: `turn_duration`, `compact_boundary`, `api_error`, … | `tests/fixtures/registry_zoo/` |
| `uuid` | most records | The record's id within its file. **Not unique**: a rewind rewrites records under uuids the file already used | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `timestamp` | most records | ISO-8601, always UTC, always `Z`-suffixed. Records are **not** written in timestamp order — two adjacent records can run backwards by a millisecond | `tests/fixtures/spine/`, CC 2.1.221 |
| `cwd`, `gitBranch`, `version`, `entrypoint` | records that carry them | The session's project directory, branch, Claude Code version, and how it was launched. The first few records of a file are bookkeeping types that carry none of them, so a parser reading record 1 gets nulls. `entrypoint` postdates the oldest sessions in the corpus and is absent on 1.0.128 transcripts | `tests/fixtures/spine/` — lines 1–3 carry no `cwd`; `tests/fixtures/legacy_entrypoint/`, CC 1.0.128 |
| `cwd` is absolute and **symlink-free** | every record that carries one | A project typed at a command line must be resolved before matching it against one — `aiobserve.sessions.resolve_project` does that. Of the 240 encoded cwds under `~/.claude/projects` on the recording machine, 181 sit under a symlinked root — 153 `-private-var`, 28 `-private-tmp` — and none carries the unresolved `-var-…` or `-tmp-…` spelling (scanned 2026-08-15). **Inferred** mechanism: Node's `process.cwd()` returns the physical path | no fixture can hold one — every fixture session ran under an unsymlinked path; corpus scan only |
| `isMeta` | `user` records | Claude Code wrote this on the user's behalf — a caveat, a hook echo. Not a prompt | `tests/fixtures/spine/`, CC 2.1.221 |
| `isCompactSummary` | `user` records | The summary written back into the transcript after a compaction. Not a prompt | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `isSidechain` | `user` and `assistant` records | The record belongs to a subagent's stream rather than the main one. In a main transcript it marks delegated work that the subagent's own file records better, so those records are skipped. Inside a subagent transcript **every** record carries it, so treating it as an exclusion there leaves the agent turnless | `tests/fixtures/spine/` — main and subagent sides, CC 2.1.221 |
| `message.content` | `user` records | Either a string or a list of blocks. A block list can hold `text`, `image`, or `tool_result` — a `tool_result` block makes the record plumbing, not a prompt | `tests/fixtures/spine/`, CC 2.1.220 (block form) |
| `tool_use` block | `assistant` records | One tool the model asked for: `id`, `name`, `input`. Usually one per record, but 23 records of the mycelia corpus hold two or more (scanned 2026-08-07), so reading per record undercounts | `tests/fixtures/spine/`, CC 2.1.221; `tests/fixtures/parallel_tools/`, CC 2.1.211 — one record of two |
| `tool_use.input` on a `Skill` call | `assistant` records | Which skill was invoked: `skill`, plus `args` on 81 of the corpus's 326 `Skill` calls (scanned 2026-08-08, across 57 sessions and CC 2.1.195–2.1.221). This is the *invocation*; `attributionSkill` is what was loaded when a reply came back, and the two disagree — a skill reached through a slash command invokes no `Skill` call at all | no fixture holds one; corpus scan only |
| `tool_result` block | `user` records | What came back, quoting the call's id in `tool_use_id`. `content` is a string or a list of `text`, `image` and `tool_reference` blocks. `is_error` is **absent on success** — 66,653 of the corpus's 154,169 result blocks omit it (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `server_tool_use` block | `assistant` records | A tool Anthropic ran server-side rather than Claude Code running it locally. Same fields as `tool_use`. All 45 in the corpus, across 5 sessions, name the `advisor` tool and carry an empty `input` (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 |
| `advisor_tool_result` block | `assistant` records | The answer to a `server_tool_use`, in the **same message** as the call — no user record answers one. Its `content.type` is `advisor_tool_result_error`, which names an `error_code`, or `advisor_redacted_result`, whose `encrypted_content` we cannot read. Either way, the transcript records that the advisor answered but nothing it said. 44 answer the 45 calls; one call was never answered (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.201 — both outcomes and the unanswered call |
| `fallback` block | `assistant` records | Claude Code retried the request on another model: `from.model` and `to.model`. All 3 in the corpus, in one session, agree with their record's `message.model` on the `to` side, so only `from` carries new information (scanned 2026-08-07) | `tests/fixtures/server_tools/`, CC 2.1.206 |
| `toolUseResult` | `user` records answering a tool | The tool's own structured report, beside the block. A dict on most results, a bare string on 3,590 and a list on 795 of the corpus's 137,255 (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |
| `toolUseResult.persistedOutputPath` | `user` records answering a tool | Output too big for the transcript, written to `<session>/tool-results/<name>.txt` with only a preview left in `content`. 321 results carry it (scanned 2026-08-07). The path is absolute on the machine that recorded it; only the file name travels | `tests/fixtures/offload/`, CC 2.1.220 |
| `message.id` | `assistant` records | The API reply's id and the key that merges its records. **One reply spans several records** — one per content block — so a per-line parser triples the API-call count | `tests/fixtures/spine/`, CC 2.1.221 — 8 records, 2 replies |
| `message.usage` | `assistant` records | Tokens for the whole reply. **Every chunk of one `message.id` repeats the same numbers**, so summing per record multiplies a reply's tokens by its chunk count | `tests/fixtures/spine/`, CC 2.1.221 — five identical copies under one id |
| `usage.cache_creation` | `assistant` records | The cache-creation total split by TTL: `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. Present on every assistant record in the mycelia corpus (scanned 2026-08-07), so the absent shape is unrecorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `attributionSkill` | `assistant` records | The skill that was running when the reply came back, absent when none was | `tests/fixtures/spine/`, CC 2.1.221 |
| `effort` | `assistant` records | The reasoning-effort setting, as an opaque string (`"high"`) | `tests/fixtures/spine/`, CC 2.1.221 |
| `requestId`, `stop_reason` | `assistant` records | The API request id and why generation stopped | `tests/fixtures/spine/`, CC 2.1.221 |
| `durationMs` | `system`/`turn_duration` records | Wall-clock milliseconds the turn took. Summing these is the only measure of a session's active time — its timestamp span includes the hours it sat idle | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.model` | `assistant` records | The model that answered — or `<synthetic>`, Claude Code's own placeholder for an interrupt or a cancelled request. 205 of the corpus's ~290,000 assistant records are synthetic (scanned 2026-08-07); all report zero tokens, and none carries `usage.inference_geo` | `tests/fixtures/spine/`, CC 2.1.201 |
| `originalModel`, `fallbackModel`, `choice`, `persistedAsDefault` | `system`/`model_consent_fallback` records | The harness ran the session on a model other than the one asked for because that one needed credits the account lacked. `choice` is what the operator did at the prompt (`cancelled`), and `persistedAsDefault` says whether the swap outlived the session. This differs from the `fallback` content block, which records a single request retried on another model | `tests/fixtures/registry_zoo/`, session `cb76d8e4-cb08-4693-bbec-d4bfa97b1f5c`, CC 2.1.221 |
| `compactMetadata` | `system`/`compact_boundary` records | What a compaction dropped: `trigger` (`auto` on 933 of the corpus's 1,026 boundaries, `manual` on 93), `preTokens`, `postTokens`, `durationMs`. All four are on every boundary (scanned 2026-08-07) | `tests/fixtures/compaction/`, CC 2.1.198 |
| `customTitle`, `aiTitle` | `custom-title` and `ai-title` records | What the session is called. Claude Code writes `ai-title` itself and rewrites it as the session goes; `custom-title` is the operator's rename. Both are current, and 13 of the 398 titled mycelia sessions hold both (scanned 2026-08-07) | `tests/fixtures/spine/` and `tests/fixtures/legacy_title/`, CC 2.1.196–2.1.201 |
| `agentName` | `agent-name` records | The persona the session ran under, rewritten alongside the title. A single-field record, like the titles: no uuid, no timestamp | `tests/fixtures/spine/`, CC 2.1.201 |
| `prNumber`, `prUrl`, `prRepository` | `pr-link` records | A pull request the session touched, written once per mention. All 2,885 in the corpus carry exactly these three plus `type`, `sessionId` and `timestamp` — no uuid, so a link is keyed by its line. The same PR repeats within a session, so the number is not a key | `tests/fixtures/spine/`, CC 2.1.221 |

### A prompt's leading tag says who wrote it

A `user` record whose content is a string often opens with an XML-ish tag, and the tag decides whether the record is a turn:

- **A turn**: `<command-name>` and `<command-message>` (a slash command, in either order, with `<command-args>` alongside), `<teammate-message>`
- **Not a turn**: `<task-notification>`, `<local-command-stdout>`, `<bash-input>`, `<bash-stdout>` — Claude Code writing to itself

Counting every string `user` record as a turn inflates the turn count several-fold — the mycelia corpus holds 2,157 `<task-notification>` records against 968 real prompts ([the trace-pipeline design](../plans/trace-pipeline/design.md)). The extractor crashes on an unregistered tag rather than guessing because the next machine tag would silently re-inflate the count.

A tag can carry attributes: `<teammate-message teammate_id="team-lead" summary="…">`. So the name ends at whitespace as well as at `>`, and the whole opening tag stays in `Turn.prompt` — the sender is part of what the record says. `<teammate-message>` appears only in subagent transcripts (132 records, one mycelia session, scanned 2026-08-07), which is why a main-transcript census misses it.

*Seen in* `tests/fixtures/spine/` — both slash-command orderings at CC 2.1.221, `<bash-input>`/`<bash-stdout>` at CC 2.1.212, `<teammate-message>` at CC 2.1.211.

### A slash command's own output is archived against the turn that ran it

`<local-command-stdout>` is not a turn, but it answers one: Claude Code writes what a slash command printed into a record whose `parentUuid` is the command turn's uuid. Most command turns drive no model response at all, so that record is the only trace of what happened.

It arrives in two shapes, and a reader must handle both:

- a `user` record carrying the tag at `message.content` — 279 of the mycelia corpus's 316 (scanned 2026-08-13)
- a `system` record with `subtype: local_command` carrying it at `content` — the other 37

The body between the tags may be empty, and that is a state rather than an absence: all 21 recorded `/clear` outputs are empty, against a median body of 71 characters and a longest of 2,038. It may also span several lines, so a reader that stops at the first finds nothing.

The same record is replayed into a resumed session against the plain turn it now hangs off — 183 records in the corpus — so a `parentUuid` naming a turn that ran no command means the output belongs to no turn here, not that the archive is malformed.

*Seen in* `tests/fixtures/spine/` at CC 2.1.221 (`user` carrier), `tests/fixtures/model_only/` at CC 2.1.215 (`system` carrier, and an empty `/clear` body), and `tests/fixtures/resume_pair/` at CC 2.1.202 (the replay).

### A parallel batch's timestamps rank by execution, not by issue

One assistant message can issue several tool calls at once, and Claude Code usually writes a record
per call in the order it ran them. The records therefore carry different timestamps
for calls the model issued together: of 23,371 multi-call messages in the mycelia corpus, 156 wrote
one shared timestamp (scanned 2026-08-07). Reading a record's own timestamp as the call's start
turns queue position into duration.

So all calls in a batch start at the earliest timestamp in the batch and carry
`ToolCall.duration_synthetic` to show that the start was assigned rather than measured. A lone call keeps
its own timestamp and the flag is false.

**The batch is the records, not the calls.** A record can hold several `tool_use` blocks, and those
calls were issued at once rather than ranked — so a message whose calls all sit in one record
keeps that record's measured start. Counting blocks instead flags a real start as assigned.

*Seen in* `tests/fixtures/spine/`, CC 2.1.221 — three calls under `msg_011CdmMjFXDofyYSMxYtXa5n` — and
`tests/fixtures/parallel_tools/`, CC 2.1.211, which holds one message of each shape.

### A server-side call is timed from its own record

A `server_tool_use` sits in the same stream as the local calls, but it is not part of their batch:
Claude Code did not run it, so its record marks the request going out rather than a queue position. It
keeps its own timestamp, `duration_synthetic` is false, and it ends when Claude Code writes the
`advisor_tool_result` block in the same message.

The call becomes a `tool_calls` row like any other, flagged `server_side`. Otherwise, "which
tools did this session use" gets the wrong answer — as it did while the block was unregistered:
it produced no row, no text and no crash, so a session that used the advisor looked like one
that had not.

*Seen in* `tests/fixtures/server_tools/`, CC 2.1.201 — a subagent message holding two local calls and
one server-side call.

### A file can repeat a uuid

Rewinding a session rewrites records under uuids the file already used. Both occurrences stay in the file. The extractor keeps the **last**, which is the state the session ended in — the choice is load-bearing because the two occurrences report different token usage, and keep-first changes the totals on four sessions of the mycelia corpus.

A pair whose `message.content` differs would mean the conversation itself was rewritten. No recorded pair does; the extractor crashes if one ever does.

*Seen in* `tests/fixtures/dup_uuid/`, CC 2.1.211 — five uuids, each twice.

### A compaction always writes the summary that replaced the context

Every `system`/`compact_boundary` record has an `isCompactSummary` user record beside it: 1,026 of each across the mycelia corpus, and no file where the two counts differ (scanned 2026-08-07, CC 2.1.191–2.1.221). So the extractor reads a compaction from the boundary's own `compactMetadata` rather than inferring it from the nearest assistant call, as the prior importer did.

Subagents compact far more often than main transcripts do, so a compaction belongs to the file that hit the limit, not to the session.

*Seen in* `tests/fixtures/compaction/`, CC 2.1.198 — two boundaries, one `auto` and one `manual`, each with its summary.

### A cache-creation total can disagree with its own split

`usage.cache_creation_input_tokens` and the `ephemeral_5m`/`ephemeral_1h` pair inside `usage.cache_creation` report the same number, and they disagree on 53 of the corpus's ~290,000 assistant records (scanned 2026-08-07). The extractor keeps both: `cache_creation_tokens` from the total, `cache_5m_tokens`/`cache_1h_tokens` from the split. Cost uses the split where it exists, so those 53 calls price against a figure the total does not confirm.

### Records contain raw U+2028 and U+2029

Line separators appear inside string values, unescaped. Splitting a transcript with Python's `splitlines()` cuts records in half; split on `"\n"`.

## Sources of session data

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — the transcript of one session, one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/` — the session's own directory, laid out below. `aiobserve.sessions` walks this tree
- Claude Code's own OpenTelemetry export — a thinner, live schema. Enabled per-machine, not per-repo.

The encoded directory name is the session's working directory with each `/` replaced by `-`, so `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The tree is shared across Claude accounts: `~/.claude-black/projects` is a symlink to `~/.claude/projects`, so a transcript's path does not tell you which account produced it.

### What a session directory holds

104 of the 575 mycelia transcripts have a directory beside them (scanned 2026-08-07), holding these
file kinds and no others. A file the extractor cannot place crashes it because Claude Code prunes
the directory within weeks, and a skipped file is a schema change nobody sees.

| Path under `<session-id>/` | Count | What it is | Archived as |
| --- | --- | --- | --- |
| `subagents/agent-<id>.jsonl` | 2275 | a subagent's own transcript | source `<id>` |
| `subagents/agent-<id>.meta.json` | 2275 | what spawned that subagent — `toolUseId`, `agentType`, `spawnDepth` | `agent_runs` |
| `subagents/workflows/wf_<id>/agent-<id>.jsonl` | 180 | an agent of a parallel fan-out; same records, one level deeper | source `<id>` |
| `subagents/workflows/wf_<id>/agent-<id>.meta.json` | 180 | only ever `agentType` and `spawnDepth` — a workflow agent has no spawning tool call | `agent_runs` |
| `subagents/workflows/wf_<id>/journal.jsonl` | 6 | the fan-out's own log: `started` and `result` records keyed by agent | source `wf_<id>/journal` |
| `tool-results/<name>` | 567 | a tool output too large for the transcript, named by `persistedOutputPath` | `offload_files` |
| `workflows/wf_<id>.json` | 6 | the workflow definition | nothing reads it |
| `workflows/scripts/<name>.js` | 6 | the script that drove the run | nothing reads it |

`<id>` in a subagent's file name is usually hex, but a session can name its agents (`agent-audit-pr291-79ea2c606313e623.jsonl`), so the source is the whole stem after `agent-`.

*Seen in* `tests/fixtures/spine/` (a subagent), CC 2.1.221; `tests/fixtures/workflow/` (a fan-out and its journal), CC 2.1.207; `tests/fixtures/offload/` (a persisted result), CC 2.1.220.

### A subagent's meta says why it ran

Every subagent transcript has a `meta.json` beside it, and every meta has a transcript: 2764 of each on this machine, none unpaired (scanned 2026-08-07). We have never seen half a pair, so the extractor crashes on one.

| Key | Metas carrying it | What it means |
| --- | --- | --- |
| `agentType` | 2764 | which agent definition ran — `general-purpose`, `auditor`, `workflow-subagent`, or a name the session invented. Not a closed set |
| `spawnDepth` | 2763 | 1 for a run the session spawned itself, deeper for a subagent's subagent, 0 for a teammate. The one meta without it is a 2.1.186 session, so absence is a state, not a parse failure |
| `description` | 2584 | the one-line task summary from the spawning call |
| `toolUseId` | 2510 | the `Agent` call that asked for the run |
| `model` | 753 | the alias the caller named, e.g. `opus` |
| `parentAgentId` | 389 | the agent that spawned this one |
| `isFork` | 52 | the run replays another transcript's history, or continues it by reference |
| `taskKind`, `teamName`, `color`, `planModeRequired`, `permissionMode` | 71 | a teammate: `taskKind` is `in_process_teammate` |
| `name`, `worktreePath`, `worktreeBranch`, `customAgentType`, `stoppedByUser` | 94, 86, 86, 39, 3 | not read yet |

254 metas name no `toolUseId`: 180 workflow agents, 71 teammates, and 3 forks. A teammate is started by the team mechanism rather than by a tool call, so its run has no spawning call at all — recorded as such, with a warning, because dropping orphans is how the prior importer came to report 100% direct tool calls.

*Seen in* `tests/fixtures/spine/` (a spawned run and a nested one), CC 2.1.221; `tests/fixtures/teammate/` (an orphan), CC 2.1.211.

### A workflow agent joins through the run id its launcher reported

A fan-out's agents are not spawned one at a time, so their metas name no call. The `Workflow` call that launched the run answers with `toolUseResult.runId`, and that id is the `wf_<id>` directory its agents write into — the only link from a fan-out's transcripts back to a tool call. All 6 workflow runs on this machine carry it (scanned 2026-08-07).

*Seen in* `tests/fixtures/workflow/`, CC 2.1.207 — the `Workflow` call, its result, and the `wf_c30cc877-997` directory it names.

### A fork continues a conversation another transcript started

A fork's meta carries `isFork: true` and `agentType: "fork"` — the two agree on all 52 fork metas on this machine (scanned 2026-08-07). Its first record says which of two shapes it is:

| First record | Forks | What the file holds |
| --- | --- | --- |
| `fork-context-ref` | 26 | nothing copied. The record names `parentSessionId`, `parentLastUuid` and `contextLength`, and the work starts mid-conversation |
| `user` or `system` | 26 | the parent's records copied verbatim, uuids and timestamps included, then the fork's own work |

A copied record is therefore in two files at once. 51 pairs of transcripts overlap that way, every pair with a fork on one side, and 25 of them are fork-to-fork: a fork's own work copied onward again. Each record belongs to the transcript that ran it first; the copies are flagged `replayed` and left in place, so the archive still shows what the fork's file recorded while no count reads the work twice. Under this rule 1617 records across 9 sessions are replays, and none of them sits in a non-fork transcript — which is the check, since a non-fork copy would mean the ordering named the wrong file first.

**Transcripts order by `(spawnDepth, first timestamp, agentId)`, main first.** Depth has to lead: a copied-history fork opens on the record it copied, so 46 of the 51 overlapping pairs tie on first timestamp, and breaking those ties by agentId gives 335 records of six real transcripts' work to the fork that copied them. A fork is spawned by the transcript it copies, so it is always the deeper of the two.

The one meta that names no `spawnDepth` sorts last. Its transcript — `-Users-nob-repos-mac-settings/c31ecec9-…/subagents/agent-a20276f6d8a4e5309.jsonl`, CC 2.1.186 — shares no uuid with any sibling, so its position changes nothing.

*Seen in* `tests/fixtures/fork_origin/` (a copied-history fork and the auditor it copied), CC 2.1.215; `tests/fixtures/fork_byref/` (a `fork-context-ref` opening), CC 2.1.202.
