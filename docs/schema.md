# Telemetry schema

What a field in an AI coding session means and where it comes from. Read this before writing a query or an analysis — a wrong reading of a field produces a confident wrong finding.

The span schema is still missing: it arrives with the OTel importer, extracted from `mac_settings/claude-otel/` in a later step. Until then, do not describe a span field here from memory.

## The rule for adding a field

Every entry names a **recorded session** that demonstrates the field, and the **Claude Code version** that produced it. The harness owns these shapes and changes them without notice, so a field documented from memory is a guess that reads like a fact.

Cite a fixture where one exists — its directory README names the source session and version, and the file is checked in, so the claim can be re-read rather than taken on trust.

## Transcript records

Every line of a transcript is a JSON object with a `type`. `aiobserve.extract.claude_code` registers each type it has seen and crashes on one it has not; the registry's members are the census, not this table.

| Field | Where it comes from | What it means | Seen in |
| --- | --- | --- | --- |
| `type` | every record | Which record shape this line is. `user`, `assistant`, `system`, `attachment`, `summary` and a dozen bookkeeping types | `tests/fixtures/registry_zoo/` — one record of every type |
| `subtype` | `system` records | Which of the nine system events this is: `turn_duration`, `compact_boundary`, `api_error`, … | `tests/fixtures/registry_zoo/` |
| `uuid` | most records | The record's id within its file. **Not unique**: a rewind rewrites records under uuids the file already used | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `timestamp` | most records | ISO-8601, always UTC, always `Z`-suffixed. Records are **not** written in timestamp order — two adjacent records can run backwards by a millisecond | `tests/fixtures/spine/`, CC 2.1.221 |
| `cwd`, `gitBranch`, `version`, `entrypoint` | records that carry them | The session's project directory, branch, Claude Code version, and how it was launched. The first few records of a file are bookkeeping types that carry none of them, so a parser reading record 1 gets nulls. `entrypoint` postdates the oldest sessions in the corpus and is absent on 1.0.128 transcripts | `tests/fixtures/spine/` — lines 1–3 carry no `cwd`; `tests/fixtures/legacy_entrypoint/`, CC 1.0.128 |
| `isMeta` | `user` records | Claude Code wrote this on the user's behalf — a caveat, a hook echo. Not a prompt | `tests/fixtures/spine/`, CC 2.1.221 |
| `isCompactSummary` | `user` records | The summary written back into the transcript after a compaction. Not a prompt | `tests/fixtures/dup_uuid/`, CC 2.1.211 |
| `isSidechain` | `user` and `assistant` records | The record belongs to a subagent's stream rather than the main one. `false` on every record of every main-transcript fixture here — the `true` case is not yet recorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `message.content` | `user` records | Either a string or a list of blocks. A block list can hold `text`, `image`, or `tool_result` — a `tool_result` block makes the record plumbing, not a prompt | `tests/fixtures/spine/`, CC 2.1.220 (block form) |
| `tool_use` block | `assistant` records | One tool the model asked for: `id`, `name`, `input`. Usually one per record, but 23 records of the mycelia corpus hold two or more (scanned 2026-08-07), so a per-record reading undercounts | `tests/fixtures/spine/`, CC 2.1.221 |
| `tool_result` block | `user` records | What came back, quoting the call's id in `tool_use_id`. `content` is a string or a list of `text`, `image` and `tool_reference` blocks. `is_error` is **absent on success** — 66,653 of the corpus's 154,169 result blocks omit it (scanned 2026-08-07) | `tests/fixtures/spine/`, CC 2.1.221 |
| `toolUseResult` | `user` records answering a tool | The tool's own structured report, beside the block. A dict on most results, a bare string on 3,590 and a list on 795 of the corpus's 137,255 (scanned 2026-08-07) | `tests/fixtures/offload/`, CC 2.1.220 |
| `toolUseResult.persistedOutputPath` | `user` records answering a tool | Output too big for the transcript, written to `<session>/tool-results/<name>.txt` with only a preview left in `content`. 321 results carry it (scanned 2026-08-07). The path is absolute on the machine that recorded it; only the file name travels | `tests/fixtures/offload/`, CC 2.1.220 |
| `message.id` | `assistant` records | The API reply's id, and the key that merges its records. **One reply spans several records** — one per content block — so a per-line parser triples the API-call count | `tests/fixtures/spine/`, CC 2.1.221 — 8 records, 2 replies |
| `message.usage` | `assistant` records | Tokens for the whole reply. **Every chunk of one `message.id` repeats the same numbers**, so summing per record multiplies a reply's tokens by its chunk count | `tests/fixtures/spine/`, CC 2.1.221 — five identical copies under one id |
| `usage.cache_creation` | `assistant` records | The cache-creation total split by TTL: `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. Present on every assistant record in the mycelia corpus (scanned 2026-08-07), so the absent shape is unrecorded | `tests/fixtures/spine/`, CC 2.1.221 |
| `attributionSkill` | `assistant` records | The skill that was running when the reply came back, absent when none was | `tests/fixtures/spine/`, CC 2.1.221 |
| `effort` | `assistant` records | The reasoning-effort setting, as an opaque string (`"high"`) | `tests/fixtures/spine/`, CC 2.1.221 |
| `requestId`, `stop_reason` | `assistant` records | The API request id and why generation stopped | `tests/fixtures/spine/`, CC 2.1.221 |
| `durationMs` | `system`/`turn_duration` records | Wall-clock milliseconds the turn took. Summing these is the only measure of a session's active time — its timestamp span includes the hours it sat idle | `tests/fixtures/spine/`, CC 2.1.221 |

### A prompt's leading tag says who wrote it

A `user` record whose content is a string often opens with an XML-ish tag, and the tag decides whether the record is a turn:

- **A turn**: `<command-name>` and `<command-message>` (a slash command, in either order, with `<command-args>` alongside), `<teammate-message>`
- **Not a turn**: `<task-notification>`, `<local-command-stdout>`, `<bash-input>`, `<bash-stdout>` — Claude Code writing to itself

Counting every string `user` record as a turn inflates the turn count several-fold — the mycelia corpus holds 2,157 `<task-notification>` records against 968 real prompts ([the trace-pipeline design](../plans/trace-pipeline/design.md)). The extractor crashes on an unregistered tag rather than guessing, because the next machine tag would re-inflate the count silently.

*Seen in* `tests/fixtures/spine/` — both slash-command orderings at CC 2.1.221, `<bash-input>`/`<bash-stdout>` at CC 2.1.212.

### A parallel batch's timestamps rank by execution, not by issue

One assistant message can issue several tool calls at once, and Claude Code writes a record per
call in the order it got round to running them. The records therefore carry different timestamps
for calls the model issued together: of 23,371 multi-call messages in the mycelia corpus, 156 wrote
one shared timestamp (scanned 2026-08-07). Reading a record's own timestamp as the call's start
turns queue position into duration.

So a batch's calls all start at the earliest timestamp in the batch, and carry
`ToolCall.duration_synthetic` to say the start was assigned rather than measured. A lone call keeps
its own timestamp and the flag is false.

*Seen in* `tests/fixtures/spine/`, CC 2.1.221 — three calls under `msg_011CdmMjFXDofyYSMxYtXa5n`.

### A file can repeat a uuid

Rewinding a session rewrites records under uuids the file already used. Both occurrences stay in the file. The extractor keeps the **last**, which is the state the session ended in — the choice is load-bearing, since the two occurrences report different token usage, and keep-first changes the totals on four sessions of the mycelia corpus.

A pair whose `message.content` differs would mean the conversation itself was rewritten. No recorded pair does; the extractor crashes if one ever does.

*Seen in* `tests/fixtures/dup_uuid/`, CC 2.1.211 — five uuids, each twice.

### Records contain raw U+2028 and U+2029

Line separators appear inside string values, unescaped. Splitting a transcript with Python's `splitlines()` cuts records in half; split on `"\n"`.

## Sources of session data

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — the transcript of one session, one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl` — one subagent the session spawned, beside an `agent-<id>.meta.json`. A parallel fan-out nests them another level, under `subagents/workflows/wf_<id>/`. `aiobserve.sessions` walks this tree
- Claude Code's own OpenTelemetry export — a thinner, live schema. Enabled per-machine, not per-repo.

The encoded directory name is the session's working directory with each `/` replaced by `-`, so `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The tree is shared across Claude accounts: `~/.claude-black/projects` is a symlink to `~/.claude/projects`, so a transcript's path does not tell you which account produced it.
