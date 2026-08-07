# Design: trace pipeline

Extract Claude Code sessions into a canonical trace model and load them into DuckDB, incrementally and idempotently, behind seams that admit other agents and other sinks later.

Designed against real sessions and full-corpus scans: `~/.claude/projects/-Users-nob-repos-mycelia/4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl` (v2.1.221, subagents + `tool-results/`), `8d930c77-9e60-4784-9885-6d4c226280f7` (workflow fan-out), `1de7cf38-…/subagents/agent-a845aedac75b66869` (copied-history fork), `07a769d7-…/subagents/agent-afa3946951a08a798` (by-reference fork); record-type, turn-tag, and id-collision censuses covered every record in every main, subagent, and journal file across the main and worktree dirs — ~560K records over ~4,100 files at last count, zero parse errors, and growing live. These properties are verified for one corpus — one person's mycelia sessions; re-check on a second project before treating any as universal.

## Problem

`aiobserve` can locate sessions (`src/aiobserve/sessions.py`) but cannot read them. Analysis, enrichment, and the trace viewer all need one queryable store. Three constraints decide the shape:

- Claude Code prunes transcripts from disk after ~20–30 days, so the store must be the durable archive — raw records and offloaded tool outputs go into the DB, not pointers to files
- DuckDB permits transactional per-session replace, which makes the prior importer's append-only OTLP machinery (ledger files, prefix-diff resend, settle windows) unnecessary for this path
- Transcripts duplicate history: a resume copies the ancestor session's records verbatim into the new file, and a fork subagent can replay its parent agent's records under its own agentId — identity and rollups must be designed for copies, not uniqueness

## Call paths, current → proposed

Current: `cli.main` → `sessions.find_sessions(project)` → print one line per session.

Proposed: `cli.main("extract")` → `pipeline.refresh(extractor, exporter)` →

1. `extractor.sessions(project)` — cheap: session ids + file fingerprints, via `sessions.find_sessions` and the session directory walk
2. `exporter.fingerprints()` — what the sink already holds
3. for each new or changed session: `extractor.extract(source)` → `SessionTrace` → `exporter.export(trace, fingerprint)` — one transaction: delete every row for that session id, insert the new rows

Sessions present in the DB but gone from disk are kept: the DB is the archive.

## File-tree diff

```
src/aiobserve/
  model.py               NEW  canonical trace model (frozen dataclasses)
  pipeline.py            NEW  Extractor/Exporter protocols, SessionSource, refresh()
  extract/
    __init__.py          NEW
    claude_code.py       NEW  transcript records → SessionTrace; record-type and turn-tag registries
    pricing.py           NEW  Anthropic pricing table + compute_cost (slice 4)
  export/
    __init__.py          NEW
    duckdb.py            NEW  schema DDL, per-session replace, fingerprints
  cli.py                 CHANGED  add `extract <project> [--db] [--projects-root]`
  sessions.py            CHANGED  session file walk also yields tool-results/* and wf_*/journal.jsonl
tests/
  extract/test_claude_code.py + fixtures/   NEW  redacted real records, versions in sidecars
  export/test_duckdb.py                     NEW
  test_pipeline.py                          NEW
docs/schema.md           CHANGED  field entries with session + version citations, per slice
pyproject.toml           CHANGED  runtime dep: duckdb
```

## Key contracts

### Identity and duplication

Record ids are natural keys from the data, but none is globally unique: 155 `message.id`s, 778 record uuids, and 156 tool_use ids span ≥2 main transcripts (resume copies history into the new session file, timestamps intact), and a copied-history fork shares hundreds of uuids with its parent agent inside one session. So:

- Every row is keyed `(session_id, source, id)` where `source` is `"main"` or the agentId of the transcript the record came from. Primary keys are composite; a collision inside one transcript still crashes loudly
- Within one transcript, duplicate-uuid lines are rewind/in-file-fork envelope rewrites (995 in the corpus: 258 byte-identical, 737 differing in `gitBranch`, `promptId`, `forkedFrom`, `parentUuid`, sometimes `toolUseResult` or `message.usage`). **Last occurrence wins** — the file's final word, matching the last-`custom-title`-wins convention, and the state Claude Code itself would resume from. The residual crash: duplicates whose `message.content` differs, which no session exhibits and which would mean the conversation itself was rewritten under one uuid
- **Copied-history forks** (meta `agentType: "fork"` / `isFork: true`; both fork variants share this meta — the leading record distinguishes them): a uuid appearing in several transcripts of one session is native to the **first-seen transcript** (transcripts ordered by first-record timestamp, agentId as tie-break) and `replayed=True` everywhere later — never symmetric flagging, which would zero-count work that originated in a fork and was then copied onward (occurs today: session `5a88789c`, fork `a61a059e`). Replayed rows stay in the DB (the viewer shows the fork as recorded) but every rollup view excludes them, and `AgentRun.started_at` is the first non-replayed record. A cross-transcript uuid overlap where **neither** transcript is a fork crashes — every overlapping pair in the corpus involves a fork
- **By-reference forks** (first record is `fork-context-ref`, carrying `parentSessionId`, `parentLastUuid`, `contextLength`): nothing is copied; the transcript opens mid-conversation, so records before the first local prompt carry `turn_id = None`
- **Resume copies across sessions**: each session's rows stand as its file recorded them; per-session rollups are file-local truths. The corpus-level rollup view dedups every replicated entity — turns, api calls, and tool calls alike — by its natural id, attributing each to its first-seen session, so corpus totals count copied history once

### Canonical model (`model.py`)

Frozen dataclasses. A `SessionTrace` is flat lists keyed by ids — a 1:1 mirror of the relational schema, not a nested tree:

```python
class SessionTrace:      # everything from one session: main transcript + all subagent transcripts
    extractor: str       # provenance, e.g. "claude_code"
    extractor_version: str
    session: Session
    turns: list[Turn]
    api_calls: list[ApiCall]
    tool_calls: list[ToolCall]
    agent_runs: list[AgentRun]
    compactions: list[Compaction]
    pr_links: list[PrLink]
    offload_files: list[OffloadFile]
    raw_records: list[RawRecord]

class Session:   # id = filename stem; project_dir/git_branch/version/entrypoint from first record carrying cwd;
                 # title = last custom-title (legacy: ai-title); agent_name; started_at/ended_at;
                 # active_ms = sum of system/turn_duration durationMs; transcript_path
class Turn:      # id = prompt record uuid; session_id; source; index; prompt (full text);
                 # command_name/command_args parsed from <command-name>/<command-message>/<command-args> tags;
                 # replayed; started_at/ended_at
class ApiCall:   # id = message.id; session_id; source; turn_id (None = fork continuation, no local prompt);
                 # index; model; effort; stop_reason; attribution_skill; request_id;
                 # started_at = parentUuid record's ts; ended_at = last chunk ts (one message = several chained
                 # records sharing message.id, one per content block — merged by walking parentUuid);
                 # input/output/cache_read/cache_creation tokens; cache_5m/cache_1h tokens (None when
                 # usage.cache_creation absent); text; thinking (full, concatenated); cost_usd; synthetic; replayed
class ToolCall:  # id = tool_use id; session_id; source; api_call_id; index; name; input (JSON str);
                 # result (full text, or the on-disk preview stub when offloaded — see offload_file);
                 # offload_file (None unless toolUseResult.persistedOutputPath names one); is_error; incomplete;
                 # started_at/ended_at; duration_synthetic (parallel calls share a start); replayed
class AgentRun:  # id = agentId; session_id; parent_agent_id; tool_use_id (None = orphan, exported loudly);
                 # agent_type; description; model (absent in some meta.json); workflow_id; spawn_depth;
                 # is_fork; fork_context_uuid (parentLastUuid from fork-context-ref, else None);
                 # started_at/ended_at from its own transcript, first non-replayed record
class Compaction:# id = compact_boundary record uuid; session_id; timestamp; trigger (auto|manual);
                 # pre_tokens; post_tokens; duration_ms   — from system/compact_boundary compactMetadata
class PrLink:    # session_id; line_no (the record's transcript line — pr-link records carry no uuid,
                 # and pr_number repeats across sessions); pr_number; pr_url; pr_repository; timestamp
class OffloadFile:# session_id; name (e.g. "bhnwe0t84.txt"); content; size_bytes — tool-results/* ingested
                 # because they hold exactly the largest tool outputs, which pruning would otherwise destroy
class RawRecord: # session_id; source ("main", agent id, or "wf_<id>/journal"); line_no;
                 # uuid; timestamp (both None on most bookkeeping types); type; raw (JSON str)
```

### Turn boundaries

A `user` record that is not `isMeta`, not `isSidechain` (condition dropped inside subagent transcripts, where every record is sidechain), not `isCompactSummary`, with either block content containing text/image and no `tool_result`, or non-empty string content classified by a closed tag registry. Census of every string that would otherwise pass — main-dir main transcripts: 968 plain prompts and 317+112 slash-command records, versus 2,157 `<task-notification>`, 279 `<local-command-stdout>`, and 11+11 `<bash-stdout>`/`<bash-input>` — an unfiltered rule counts ~3.6x too many turns and fills `Turn.prompt` with notification XML. Subagent transcripts (all 2,623, worktrees included) add exactly one further tag: 133 `<teammate-message>`. So:

- plain string → a turn
- leading `<command-name>` or `<command-message>` (both orderings occur) → a slash-command turn; `command_name`/`command_args` parsed from the embedded tags
- leading `<teammate-message>` → a turn: an incoming instruction from a teammate agent, driving work exactly as a prompt does (subagent transcripts only)
- leading `<task-notification>`, `<local-command-stdout>`, `<bash-stdout>`, `<bash-input>` → machine records, never turns, archived in `raw_records`
- any other leading `<tag>` → crash. The set was closed over the full corpus, main and subagent files; a new tag is a schema change we need to see (a user pasting XML trips this once and we add the tag deliberately)

### Record-type registry (`extract/claude_code.py`)

Two `StrEnum`s, closed-world, built from a full-corpus census (every main, subagent, and journal file — a 1-in-40 sample provably misses rare types). **Parsed**: `assistant`, `user`, `system` (subtypes `turn_duration`, `compact_boundary` parsed; `away_summary`, `local_command`, `informational`, `scheduled_task_fire`, `api_error`, `agents_killed`, `stop_hook_summary` archive-only; unknown subtype crashes), `custom-title`, `ai-title` (legacy title, ≤v2.1.187), `agent-name`, `pr-link`, `fork-context-ref` (feeds `AgentRun.is_fork`/`fork_context_uuid`). **Archive-only**: `attachment`, `last-prompt`, `mode`, `permission-mode`, `bridge-session`, `file-history-snapshot`, `file-history-delta`, `agent-setting`, `queue-operation`, `summary`, `worktree-state`, `relocated`, and the workflow-journal types `started`/`result` (only in `wf_*/journal.jsonl`). Archive-only records land in `raw_records` untouched. A `type` in neither enum crashes, naming type, session, and line number — never the record content (transcripts are private).

Error stance elsewhere: `[]` access for fields that carry meaning, `.get` only where absence is documented (e.g. `isMeta`, `model` in meta.json). One tolerated malformation: an unparseable **final** line of a session's transcript is a partial write of a live file — dropped with a warning; the fingerprint changes when the write completes and the next refresh self-heals. Any other malformed line crashes (the current corpus has zero, in ~560K records).

Subagent linkage, as verified live: `meta.json.toolUseId` → the spawning `Agent` tool_use id; workflow agents join by their `wf_<id>` directory (meta carries `agentType: "workflow-subagent"`, no toolUseId) matched against the launching `Workflow` call's result text; an unmatched layout under `subagents/` raises. A run whose spawning call is missing exports as an orphan with a warning, never silently dropped.

### Extractor / Exporter seams (`pipeline.py`)

```python
class SessionSource(NamedTuple):
    id: str
    files: tuple[Path, ...]      # transcript + subagent transcripts + metas + wf journals + tool-results/*
    fingerprint: str             # sha256 over extractor version + sorted (relpath, size, mtime_ns)

class Extractor(Protocol):
    def sessions(self, project: Path) -> list[SessionSource]: ...
    def extract(self, source: SessionSource) -> SessionTrace: ...

class Exporter(Protocol):
    def fingerprints(self) -> dict[str, str]: ...            # session_id → fingerprint the sink holds
    def export(self, trace: SessionTrace, fingerprint: str) -> None: ...   # atomic replace-or-insert
```

The fingerprint covers subagent files and offload files because they change without the main transcript changing, and folds in the extractor version so a parser upgrade re-extracts the whole corpus automatically — otherwise `refresh()` would never revisit unchanged files under new parsing logic. (mtime in the fingerprint means a file copy re-extracts everything: idempotent, just slow.) Sink state lives in the sink (an `extract_state` table), not in a ledger file — the prior pipeline's ledger-vs-backend divergence was its worst failure class.

### DuckDB schema (`export/duckdb.py`)

One table per model entity, columns 1:1 with the dataclass fields; composite natural keys `(session_id, source, id)` as primary keys on the record-derived tables. Every key is session-scoped — nothing in a transcript is globally unique: `agent_runs` keys `(session_id, id)` (two agentIds already span two sessions via resume-copied subagent files), `pr_links` keys `(session_id, line_no)`, `offload_files` keys `(session_id, name)`; only `sessions` and `extract_state` key on `session_id` alone. Plus:

- `extract_state(session_id PK, fingerprint, transcript_path, extracted_at, extractor, extractor_version)` — provenance copied from `SessionTrace`
- `meta(schema_version)` — checked on open; mismatch crashes with "delete the DB and re-extract". No migrations while the project is early
- rollup **views**, not stored aggregates: `session_rollups` (turn/call/tool counts, token and cost sums, wall vs active time — excluding `replayed` rows) and `corpus_rollups` (additionally dedups turns, api calls, and tool calls by their natural ids, first-seen session wins, so resume copies count once)

Full message text lives untruncated in the normalized columns (DuckDB compresses strings well; enrichment and the UI read text constantly, and a join-to-raw retrieval path would re-implement extraction in SQL). `raw_records` is the archive and schema-archaeology escape hatch; `offload_files` completes it for outputs Claude Code moved out of the transcript. One DB holds many projects: `sessions.project_dir` scopes queries; default path `data/traces.duckdb` (gitignored).

Phase-2 enrichment attaches as separate tables keyed by these natural ids (`session_id`, `source`, entity id). Per-session replace touches only the pipeline's own tables, so enrichment rows survive a re-extract and re-link by key; `extract_state.extracted_at` tells the enricher what is stale.

## Chosen test seam

`SessionTrace` is the seam. Extractor tests call `extract()` on redacted real fixtures under `tests/extract/fixtures/` (Claude Code version in a sidecar, per `.claude/rules/testing.md`) and compare whole objects. Exporter tests build small `SessionTrace` values directly and assert via SQL on a tmp-path DB. `tests/test_pipeline.py` runs `refresh()` over a fixture projects-root end to end — the same path the CLI drives.

## Slices

1. **Seam + spine.** `model.py`, `pipeline.py`, extractor producing Session/Turn/ApiCall (chunk merging, tokens, text, the full record-type registry with unknown-type crash, the turn-tag registry) on one redacted fixture, DuckDB exporter with those tables + `extract_state` + `meta`, fingerprint skip-unchanged, CLI `extract`. Verified by `test_pipeline.py` end to end plus whole-object extractor tests, including a task-notification-excluded turn case.
2. **Tool calls + the archive.** tool_use/tool_result pairing, `duration_synthetic`, `raw_records` passthrough of every line, `offload_files` from `tool-results/*`, journal archiving. Verified by extractor tests on a fixture with parallel tool calls, and a raw-count = line-count invariant test.
3. **Agent runs.** Subagent transcripts, meta linkage, workflow fan-outs, orphans, nesting, both fork variants with replay flagging; their turns/calls/tools flow through the same parser with `source` set. Fixtures redacted from a real workflow session and a real fork pair. Verified by extractor tests plus a rollup-parity test (fork replays excluded, subagent work counted once).
4. **Session texture.** Compactions from `compact_boundary`, `pricing.py` costs (synthetic-model flag), title/agent-name metadata, `pr_links`, live-file trailing-line tolerance, `corpus_rollups`. Each slice adds its fields to `docs/schema.md` with citations.

## Decisions

- **Frozen dataclasses, not pydantic** — the model is built by our own parser, not deserialized from untrusted input; the parser's explicit `[]` access is the validation layer. Rejected: pydantic (a runtime dep guarding no boundary).
- **Flat `SessionTrace`, not a nested tree** — mirrors the relational schema, keeps export trivial and whole-object tests readable. Rejected: nesting (every exporter must flatten; enrichment/UI read the DB, not the object).
- **Composite natural keys `(session_id, source, id)`, not global ids or structural indices** — natural ids survive parser changes and join to raw, but resume and fork copies make them collide across transcripts; the composite scopes them to the transcript that recorded them. Rejected: global natural PKs (crash on ~2.6% of the corpus); the prior importer's `{sid}:turn:{i}` scheme (existed only to make append-only OTLP diffs reproducible).
- **Flag fork replays first-seen-wins, exclude from rollups** — keeps the archive faithful to what the file recorded while keeping every count single-counted, including work that originated in a fork and was copied onward. Rejected: dropping replayed rows (the viewer loses the fork's recorded context); ignoring them (double-counted tokens and cost); symmetric flagging (zero-counts a fork's fresh work — real in session `5a88789c`).
- **Last occurrence wins for within-transcript duplicate uuids** — rewind and in-file forking rewrite a record's envelope (`forkedFrom`, `parentUuid`, occasionally usage), and the last write is the state the session actually continued from, consistent with last-title-wins. Rejected: keep-first (reports superseded lineage and token counts); crash-on-any-difference (fires on 4 real sessions, 737 lines — a `message.content` difference remains the crash).
- **Resume dedup at the query layer (`corpus_rollups`), not at extract time** — a per-session extractor cannot see sibling sessions, and per-session rows should state what the file states. Rejected: cross-session dedup in the extractor (breaks the one-session-one-transaction replace model).
- **Closed turn-tag registry, crash on novel tags** — the prior importer's rule predates async-agent notifications and counts ~3.6x too many turns on this corpus. Rejected: prefix blocklist without the crash (the next new notification type silently inflates counts again).
- **Per-session transactional replace, not append + dedup or diff-resend** — idempotent by construction; grown and resumed sessions just re-extract. Rejected: ledger + prefix-diff (an OTLP constraint, and the prior pipeline's largest bug source: issues #2, #7).
- **State in the sink, not a ledger file** — `fingerprints()` reads `extract_state`, so "what the sink holds" cannot diverge from the sink. Rejected: `sent-sessions.json`.
- **Extractor version folded into the fingerprint** — a parser upgrade re-extracts everything without a bump-and-delete ritual, and provenance reaches the sink through `SessionTrace`. Rejected: fingerprinting files only (upgraded parsing never reaches already-ingested sessions).
- **No settle window** — a mid-write read self-heals on the next refresh. Rejected: 30-minute settle (delays live analysis for no correctness gain under replace semantics).
- **Raw + offload passthrough into the DB** — transcripts and `tool-results/` are pruned from disk after weeks; the DB is the durable copy, and offload files hold exactly the largest tool outputs. Rejected: storing paths; archiving transcripts but not offloads (silently loses the biggest results).
- **Full text in normalized columns** — enrichment and the UI are text-hungry; storage is cheap locally. Rejected: truncated columns + join-to-raw retrieval.
- **Compactions from `compact_boundary.compactMetadata`** — exact preTokens/postTokens/trigger, verified 1:1 with `isCompactSummary` across v2.1.205–2.1.221. Rejected: the prior nearest-assistant-call inference.
- **Keep DB rows for pruned sessions** — archive semantics. Rejected: mirroring disk.
- **One DB, project column** — cross-project queries free. Rejected: DB-per-project.

## Out of scope

- **OTLP exporter** and everything only it needs: ledger, prefix-diff resend, `--shift-into-window`, throttling, partial_success parsing, manifest reconciliation. The `Exporter` protocol's replace semantics is DuckDB-shaped; OTLP will need its own delivery bookkeeping behind the same `fingerprints()`/`export()` surface, designed when it is built.
- **Enrichment tables and the viewer UI** — only their attachment keys are designed here. One known viewer gap: block ordering inside one assistant message is lost (`text`/`thinking` concatenated); interleaved text→tool→text rendering must re-derive order from `raw_records`.
- **Parsing `queue-operation` task-notifications and `wf_*/journal.jsonl`** — the subagent's own transcript is authoritative for its work; both are archived raw, parseable later without re-ingest.
- **Structured `toolUseResult` parsing** — archived raw; normalized `result` uses the flattened text form, plus the `offload_file` pointer when output was persisted out of the transcript.
- **A second extractor** (Codex, Cursor) — the protocol is the deliverable; no speculative shared parsing helpers.

## Open questions

- `usage.iterations`, `inference_geo`, `speed`, and `effort` semantics are unestablished — carried raw (and `effort` as an opaque string); is that enough for phase-2 enrichment, or should any be promoted now?
- `version: "1.0.128"` appears on 4 records in 2 sessions, far below the 2.1.x cluster — a legitimately ancient session, or a field meaning something else on some record types? Worth a look before trusting `Session.version` in analyses.
- For by-reference forks, is `turn_id = None` plus `fork_context_uuid` enough for the phase-4 viewer to stitch the fork under its parent's timeline, or does it need the parent chain materialized?
