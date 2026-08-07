# Testing plan: trace pipeline

Obligations for `plans/trace-pipeline/design.md` revision 3, grouped by the design's slices so each implementer knows what is due with their code. Every leaf is an obligation; the *Evidence:* clause names the artifact that discharges it. An auditor traces each leaf to that artifact.

Two rules shape everything below. Fixtures are **redacted excerpts of real sessions** under `tests/**/fixtures/`, trimmed to the records the test needs, with the Claude Code version in a sidecar (`.claude/rules/testing.md`). Leaves whose data is invented say so and say why — an unlabeled invented fixture reads as evidence it isn't.

## Levels

Four places tests run. Each leaf below sits at the level closest to real behavior its seam allows.

- **unit (extractor)** — `tests/extract/test_claude_code.py` (split by topic as it grows: `test_claude_code__turns.py`, `__forks.py`, `__registry.py`). No I/O but reading fixture files: redacted transcript records in, a whole `SessionTrace` out, compared as one object. The world is stood in for by recorded mycelia sessions.
- **unit (pricing)** — `tests/extract/test_pricing.py`, slice 4. A pure function over a usage dict lifted from a real assistant record.
- **integration (exporter)** — `tests/export/test_duckdb.py`. A real DuckDB file on `tmp_path`, never a mock; assertions are SQL. `SessionTrace` values are **built directly in the test and are invented** — deliberately: the exporter's contract is about rows, keys, transactions, and views, not about record shapes, and small hand-built traces make the key collisions legible. The record shapes those traces stand for are proved one level up.
- **end-to-end (pipeline + CLI)** — `tests/test_pipeline.py`. A fixture projects-root on `tmp_path` holding redacted real sessions, the real extractor, a real DuckDB exporter. For refresh-orchestration leaves, a counting wrapper around the real `Extractor` records which sessions were re-extracted — the protocol is the seam that makes "no-op" observable at all.

Every wait is bounded and every test runs offline; nothing here touches a network.

## Fixture corpus

Redact each from the named session, trim to the records the leaf needs, and write the version into the sidecar. All paths are under `~/.claude/projects/-Users-nob-repos-mycelia/`; all were confirmed present on disk while writing this plan.

| Fixture | Source | CC version | What it carries |
| --- | --- | --- | --- |
| `spine/` | `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b.jsonl` | 2.1.221 | prompts, chained assistant records, turn_duration, custom-title, agent-name; its `subagents/` and `tool-results/` (3 offload files) feed slices 2–3 |
| `chained_message/` | `1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9.jsonl` | 2.1.198–201 | `msg_0111NCLcApSP6Uzne8rbpgFa`: two records 39ms apart, `parentUuid` chained, thinking then text; also 18 `compact_boundary` records |
| `registry_zoo/` | one record per archive-only type, drawn from the census: `worktree-state` from `…--claude-worktrees-activities/82cf47d1…`, `relocated` from `…--claude-worktrees-compact-context-short/be5dd046…`, `summary` + the rest from main-dir sessions | mixed, per-record sidecar | one redacted record of every parsed and archive-only type, and every system subtype |
| `dup_uuid/` | `e684d4da-e05b-49a4-b91e-2c409568a934.jsonl` | 2.1.220 | 356 within-file duplicate-uuid pairs; pick one differing in `gitBranch`/`forkedFrom` and one of the 7 differing in `message.usage` |
| `legacy_title/` | `716eac06-bba4-408b-8a3b-5421ecf8ee8e.jsonl` | 2.1.187 | `ai-title` instead of `custom-title` |
| `workflow/` | `8d930c77-9e60-4784-9885-6d4c226280f7` + `subagents/workflows/wf_c30cc877-997/` | 2.1.207 | a `Workflow` fan-out: launching call, its result text carrying the wf id, the agents, the journal |
| `fork_copied/` | `1de7cf38…/subagents/agent-a845aedac75b66869.jsonl` + sibling `agent-a8a9bed6d2008caf6.jsonl` | 2.1.198–201 | copied-history fork pair: 43 shared `message.id`s, 177 shared uuids, spawnDepth 2 |
| `fork_origin/` | `5a88789c-1da7-4f32-b631-40a7e243334b/subagents/`: `agent-a61a059e3610e6fb4` and its copies `agent-ac7625b733cf720b1`, `agent-afc389b0ce22102f6` | 2.1.215 | the 10 records that exist in no non-fork transcript, copied onward with identical timestamps — the case symmetric flagging zero-counts |
| `fork_byref/` | `07a769d7-828c-4edb-b3ce-af51e2712aa3/subagents/agent-afa3946951a08a798.jsonl` | 2.1.202 | opens with `fork-context-ref` carrying `parentSessionId`/`parentLastUuid`/`contextLength`; the session also has a `tool-results/` dir |
| `teammate/` | as built, `10d0349d-0705-4e23-aa64-5b1b97698b2e/subagents/agent-aarchitect-5144001ac50718bc.jsonl` | 2.1.211 | `<teammate-message>` prompts inside a subagent transcript, and a run with no spawning call |
| `resume_pair/` | `0a76f771-5f5b-447e-852a-664fc972ea7c.jsonl` (2.1.205) and `2352492b-1437-4427-ad51-70f35c75f663.jsonl` (2.1.202–205) | as noted | independent session files sharing an early conversation prefix verbatim, `message.id` and timestamps intact |
| `dup_agent_id/` | `8320539c…/subagents/agent-a00b0aab844a0121f.jsonl` and `b53a27cb…/subagents/agent-a00b0aab844a0121f.jsonl` | per sidecar | one agentId in two sessions as byte-identical resume-copied subagent files |

Invented fixtures, each labeled in a comment where it is used: an unknown record `type`, an unknown `system` subtype, a novel `<tag>` prompt, a duplicate uuid whose `message.content` differs, an unrecognized layout under `subagents/`, a non-fork cross-transcript uuid overlap, a truncated final line, a corrupt middle line, and an offload-name collision across two sessions. Each is a shape the corpus does not contain — which is the point of the crash rule, and the reason no recorded session can stand in.

---

## Slice 1 — seam and spine

### unit (extractor)

- A small redacted session extracts to the expected whole `SessionTrace`: session id from the filename stem, `project_dir`/`git_branch`/`version`/`entrypoint` from the first record carrying `cwd` (not the first record — the file opens on bookkeeping types that carry none), `active_ms` as the sum of `system/turn_duration` `durationMs`, `started_at`/`ended_at`. *Evidence:* `spine/`; one `assert trace == SessionTrace(...)` with the whole object spelled out, absolute paths lifted from actual.
- **A message split across chained records merges into one `ApiCall`.** **As built:** covered from `spine/` instead — its 8 assistant records under 2 message ids prove the merge, so no `chained_message/` fixture exists. *Evidence:* `chained_message/` — `msg_0111NCLcApSP6Uzne8rbpgFa`'s two records; assert exactly one `ApiCall`, `started_at` equal to the `parentUuid` record's timestamp, `ended_at` equal to the second chunk's timestamp, `text` and `thinking` both populated from their respective blocks. Bolded: 67% of `(message.id, file)` pairs in the corpus span several records, so a per-line parser silently triples the API-call count.
- Token fields distinguish absent from zero: `cache_5m`/`cache_1h` are `None` when `usage.cache_creation` is absent and carry the split when present. *Evidence:* `spine/` with two assistant records, one of each shape; assert `None`, not `0`.
- `attribution_skill`, `request_id`, `effort`, `stop_reason` reach `ApiCall` as recorded, `effort` as an opaque string. *Evidence:* `spine/` (`attributionSkill` and `effort` both observed at 2.1.221); whole-object assertion.
- A plain-string prompt opens a turn keyed by the prompt record's uuid, with the full text and a session-local index. *Evidence:* `spine/`; assert the `Turn` whole.
- **A `<task-notification>` string user record is not a turn and is still archived.** *Evidence:* `spine/` trimmed to hold both a real prompt and a notification; assert the turn count counts only the prompt, that no `Turn.prompt` starts with `<`, and that the notification appears in `raw_records`. Bolded: this is the ~3.6x turn inflation the prior importer shipped.
- `<local-command-stdout>`, `<bash-stdout>`, `<bash-input>` are likewise archived, never turns. *Evidence:* one record of each in the same fixture; turn count unchanged.
- A slash-command record becomes a turn with `command_name` and `command_args` parsed, in **both** tag orderings. *Evidence:* two records from `spine/`, one leading with `<command-name>` and one with `<command-message>` (both orderings occur in the corpus); assert the parsed fields on each.
- A block-content user record with text or image and no `tool_result` is a turn; one carrying a `tool_result` block is not. *Evidence:* `spine/`; two records, one turn.
- `isMeta` and `isCompactSummary` user records are not turns. *Evidence:* `spine/` plus one `isCompactSummary` record from `chained_message/`; turn count excludes both.
- **An unknown record `type` crashes, naming the type, the session, and the line number — and never the record's content.** *Evidence:* invented fixture (labeled — a corpus-present type would not be unknown); assert the exception message contains the type name and the line number, and assert the record's payload string is absent from it. Bolded: the message is the one place a private transcript could leak into a log.
- An unknown `system` subtype crashes the same way. *Evidence:* invented fixture (labeled); same assertions.
- **A novel leading `<tag>` in a prompt string crashes rather than counting as a turn or being skipped.** *Evidence:* invented fixture (labeled); assert the exception names the tag. Bolded: this is what stops the next notification type from re-inflating turn counts silently.
- **Every type and subtype the corpus contains parses without crashing.** *Evidence:* `registry_zoo/` — one redacted record of each of the parsed types, each archive-only type, and all nine system subtypes; assert `extract()` returns and that each archive-only record lands in `raw_records` with its `type` intact. Bolded: the registry's completeness is the blocker that sank revision 1, and this fixture is the only regression net for it (see *Obligations the seams can't reach* for what it still cannot prove).
- **Duplicate uuids within one transcript resolve last-occurrence-wins.** **As built:** `dup_uuid/` is sourced from `8ee00a94`, not the `e684d4da` this plan named — a corpus scan found `e684d4da` has no usage-differing pair, and only 7 exist corpus-wide, 5 of them in `8ee00a94`. *Evidence:* `dup_uuid/` — a pair differing in `gitBranch`/`forkedFrom` and a pair differing in `message.usage`; assert the emitted row carries the **second** occurrence's values in both cases, and that exactly one row exists per uuid. Bolded: keep-first and keep-last give different token totals on 4 real sessions, so the policy is load-bearing, not cosmetic.
- A duplicate uuid whose `message.content` differs crashes. *Evidence:* invented fixture (labeled — no session exhibits it); assert the exception names the uuid.

### integration (exporter)

- A `SessionTrace` round-trips: every column of `sessions`, `turns`, `api_calls` reads back equal to what was exported, `None` staying `NULL`. *Evidence:* export then `SELECT *`; compare row tuples against the input dataclasses field by field.
- **Re-exporting a session replaces it wholly.** *Evidence:* export a trace with five turns, then the same session with three; assert `turns` holds exactly the three and that no table retains a row from the first version. Bolded: the whole idempotency story rests on delete-then-insert being total across every table, and a table added in a later slice and forgotten in the delete is the exact bug this catches.
- A replace touches no other session's rows. *Evidence:* export two sessions, re-export one; assert the other's row counts and contents are untouched.
- `extract_state` records fingerprint, transcript path, extractor, extractor version, and `extracted_at`; `fingerprints()` returns exactly the exported session-to-fingerprint map. *Evidence:* SQL on `extract_state` plus an equality assertion on the `fingerprints()` dict.
- **A composite key scopes an id to its transcript: the same `id` under two different `source` values coexists; a genuine `(session_id, source, id)` repeat raises.** *Evidence:* one trace with two `ApiCall`s sharing `message.id` under `source="main"` and an agentId — both rows present; a second trace repeating the full triple — DuckDB raises a constraint error. Bolded: a global PK crashes on ~2.6% of the corpus, and `(session_id, id)` alone still collides inside a fork session.
- A `schema_version` mismatch crashes on open with a message telling the operator to delete the DB and re-extract. *Evidence:* open a DB, rewrite `meta.schema_version`, reopen; assert the raise and that the message contains "re-extract".
- A failed export leaves the DB exactly as it was. *Evidence:* export a session, then export a trace whose own `turns` list repeats a composite key; assert the insert raises and that the session's original rows survive unchanged.

### end-to-end (pipeline + CLI)

- `refresh()` over a fixture projects-root ingests every session it finds. *Evidence:* a root holding two redacted sessions; assert the DB's per-table row counts equal what `extract()` returns for each, summed.
- **Re-running `refresh()` with nothing changed extracts nothing and writes nothing.** *Evidence:* counting `Extractor` wrapper reports zero `extract()` calls on the second pass, and `extract_state.extracted_at` is byte-identical before and after. Bolded: this is the property that lets the pipeline run on a timer.
- **A grown session is replaced, not appended.** *Evidence:* append real records to the fixture transcript, refresh, and compare every table against a DB built from scratch over the grown file — the row sets are equal, and the turn count has grown. Bolded: the prior pipeline's worst bug froze every resumed session forever.
- **A late-arriving subagent file is detected through the fingerprint, though the main transcript never changed.** *Evidence:* refresh; copy a redacted subagent transcript into the session's `subagents/` dir without touching the main file; refresh again; assert the counting wrapper re-extracted that session and that `extract_state.fingerprint` changed. Bolded: the fingerprint covering more than the main transcript is the design's answer to a whole failure class, and nothing else tests it.
- A bumped extractor version re-extracts the whole corpus. *Evidence:* monkeypatch the extractor's version, refresh; assert `extract()` was called once per session and the fingerprints all changed.
- A session in the DB whose file is gone from disk keeps its rows. *Evidence:* delete a fixture transcript, refresh; assert that session's row counts are unchanged and it is absent from the new `sessions()` listing.
- `cli.main("extract", project, "--db", ...)` drives the same path and produces the same DB. *Evidence:* invoke in-process (no subprocess, no timeout to bound); assert the DB at `--db` holds the sessions `refresh()` would have written.

---

## Slice 2 — tool calls and the archive

### unit (extractor)

- A `tool_use` block pairs with its `tool_result` by tool_use id, carrying name, input JSON, flattened result text, and `is_error`. *Evidence:* `spine/` trimmed to one assistant record with a tool call and its result record; whole-object `ToolCall` assertion.
- A tool call with no result is `incomplete=True` and still exported. *Evidence:* `spine/` trimmed to cut the result record — the real shape of a session that ended mid-call; assert the row exists with `incomplete=True`.
- **Parallel tool calls in one assistant message all carry `duration_synthetic=True`; a lone call carries `False`.** *Evidence:* `spine/` with an assistant record issuing two or more `tool_use` blocks; assert the flag on each and that both share a `started_at`. Bolded: without the flag, every parallel-call duration in the DB reads as measured and analyses silently rank on noise.
- **`raw_records` holds one row per line of every source file.** *Evidence:* for each file in the fixture session — main transcript, each subagent transcript, each `wf_*/journal.jsonl` — assert `len([r for r in raw_records if r.source == s])` equals that file's line count. Bolded: this is the archive-completeness invariant, and it is the only leaf that fails when a new record type is quietly dropped instead of archived.
- `RawRecord.uuid` and `timestamp` are `None` for the types that carry neither, not empty strings. *Evidence:* `registry_zoo/` — `mode`, `summary`, `worktree-state`, `custom-title` (neither field), `pr-link` and `queue-operation` (timestamp only), `attachment` (both); assert each.
- **A tool result offloaded to disk keeps its pointer, its stub, and its content.** **As built:** `OffloadFile.content` is decoded text, not bytes, so corpus-wide text queries reach it; 9 of the corpus's 567 offload files are not valid UTF-8 (a fetched PDF, output cut mid-character), and those decode with replacement characters under `lossy_decode=True` while `size_bytes` still reports the file. A second leaf covers that, on invented bytes — no recorded example is redactable. *Evidence:* `fork_byref/`'s session or `spine/` — a record whose `toolUseResult.persistedOutputPath` names a `tool-results/` file; assert `ToolCall.offload_file` names it, `ToolCall.result` holds the preview stub the transcript recorded, and an `OffloadFile` row carries the redacted file's full bytes and `size_bytes`. Bolded: the offload files hold exactly the largest tool outputs, and the ~30-day prune destroys them.
- Workflow journal records archive under `source = "wf_<id>/journal"`. *Evidence:* `workflow/`; assert the `started`/`result` rows and their source.
- **As built, two leaves moved up from slice 3**, because slice 2 is where the walk first classifies a session's whole directory: the workflow *definition* and *script* a session keeps under `workflows/` are registered as known-but-unread (they would choke a JSON-lines parser), and any other unplaceable file raises. Both plant files by name over the `spine/` transcript — the names are the point.
- **As built, `fixture_source` builds over the whole session directory**, not the transcript alone, so no test can pass on files the pipeline would really see. The slice-1 counts that meant "the main transcript's records" were rescoped to `source == MAIN_SOURCE`.

### integration (exporter)

- `tool_calls` and `raw_records` round-trip and take part in the per-session replace. *Evidence:* re-export a shrunk trace; assert no stale tool or raw rows survive.
- `offload_files` keys `(session_id, name)`: the same offload name in two sessions coexists. *Evidence:* two traces with an identical offload name (**invented** — zero cross-session name collisions exist today, but the stems are random, so the key must hold before one does).

### end-to-end

- Touching an offload file re-extracts its session. *Evidence:* refresh; rewrite a `tool-results/` file; refresh; assert the counting wrapper re-extracted that session and the `offload_files` content changed.

---

## Slice 3 — agent runs

### unit (extractor)

- A subagent transcript's turns, API calls, and tool calls all carry `source = agentId`, and the main transcript's carry `source = "main"`. *Evidence:* `spine/` with one of its subagents; assert the `source` on every row of each kind.
- Inside a subagent transcript the `isSidechain` exclusion is dropped, so its delegated prompt still opens a turn. *Evidence:* `spine/`'s subagent — every record is `isSidechain: true`; assert the first turn exists and holds the delegated prompt.
- **`<teammate-message>` opens a turn.** *Evidence:* `teammate/`; assert the turn exists with the message as its prompt. Bolded: 133 such records in 4 sessions crash an extractor whose registry omits the tag, and they only occur in subagent transcripts — the place a main-transcript census never looks.
- `meta.json` linkage: `toolUseId` becomes `AgentRun.tool_use_id`, with `agent_type`, `description`, `spawn_depth` from the same file. *Evidence:* `spine/`'s subagent meta; whole-object `AgentRun` assertion.
- A `meta.json` with no `model` yields `model=None` rather than crashing — the one documented-absence `.get`. *Evidence:* `4208c1bd…/subagents/agent-aec078eda6312bc54.meta.json`, which genuinely lacks the key. **As built,** the fixture `spine/`'s own nested agent lacks the key too, so the assertion rides the nested-run test rather than pulling a second session in. **A second documented absence appeared:** one meta of 2764 carries no `spawnDepth` (CC 2.1.186), so the field is nullable; the missing key is the whole record, so that shape is planted, not fixtured.
- A workflow agent joins by its `wf_<id>` directory matched against the launching `Workflow` call's result text, and carries `workflow_id`. *Evidence:* `workflow/`; assert `workflow_id`, `tool_use_id` pointing at the `Workflow` call, and `agent_type == "workflow-subagent"`. **As built,** the join is not on result *text*: the answering record carries `toolUseResult.runId`, equal to the directory name, on 6 of 6 runs.
- **An agent run whose spawning call is missing exports as an orphan with a warning, never dropped.** *Evidence:* `workflow/` with the launching call trimmed out (a deliberate trim standing for the real compacted-away case, noted in a comment); assert the `AgentRun` row with `tool_use_id=None` and a captured warning naming the agent. Bolded: silently dropping orphans hides whole delegated workloads, which is how the prior importer reported 100% direct tool calls. **As built,** no trim was needed — a teammate is a real orphan (`teammate/`, `tool_use_id=None`, `spawn_depth=0`). The orphan population is 254 of 2764 metas: 180 workflow agents, 71 teammates, 3 forks.
- **An unrecognized layout under `subagents/` raises.** *Evidence:* invented directory (labeled — the corpus has only the two known layouts); assert the raise names the offending path.
- A nested subagent carries its `parent_agent_id` and a `spawn_depth` above 1. *Evidence:* `fork_copied/` (spawnDepth 2); assert both fields.
- **A copied-history fork flags replays first-seen-wins, never symmetrically.** *Evidence:* `fork_origin/` — the 10 records native to `a61a059e` and copied into `ac7625b7`; assert every one of those rows is `replayed=False` under `a61a059e` and `replayed=True` under `ac7625b7`, and that no record is `replayed=True` in both. Bolded: symmetric flagging zero-counts work that really happened, in a session that exists today.
- Transcript ordering is by first-record timestamp with agentId as tie-break. *Evidence:* `fork_copied/` for the timestamp case; an **invented** pair with identical first timestamps (labeled — the tie does not occur in the corpus) for the tie-break.
  - **As built: not built — the rule is wrong on real data, and the fork work below stops here.** A copied-history fork's first record *is* its parent's first record, uuid and timestamp alike, so the tie is the norm: across the 14 fork sessions, 236 overlapping transcript pairs tie on first timestamp, and in 12 of them the fork's agentId sorts first. Six real non-fork transcripts would have their own work flagged `replayed` — 81,961 of 291,904 output tokens (28%) reattributed to a fork. Separately, the 10 records the `fork_origin/` bullet cites are all `attachment` records, so they carry no tokens and cannot show rollup parity. Suggested rule, unbuilt: order by `(spawn_depth, first-record timestamp, agentId)`, which puts a parent ahead of its fork and still makes `a61a059e` first among its depth-2 sibling forks.
- `AgentRun.started_at` is the first non-replayed record, not the replayed history's first timestamp. *Evidence:* `fork_origin/`'s `ac7625b7`; assert `started_at` falls after the copied block's timestamps.
- **A by-reference fork opens mid-conversation.** *Evidence:* `fork_byref/`; assert `is_fork=True`, `fork_context_uuid` equal to the `fork-context-ref` record's `parentLastUuid`, and that every record before the first local prompt has `turn_id=None`. Bolded: a phantom turn here mis-attributes an entire fork's cost.
- The two fork variants are told apart by their leading record, not by their meta. *Evidence:* `fork_copied/` and `fork_byref/` — assert both metas carry `agentType: "fork"`/`isFork: true`, and that only the by-reference one gets a `fork_context_uuid`.
- A cross-transcript uuid overlap in which **neither** transcript is a fork crashes; an overlap involving a fork does not. *Evidence:* the crash from an invented pair (labeled — zero such pairs exist in 40 real overlap pairs); the non-crash from `fork_copied/`, whose parent is not a fork and which must parse clean.

### integration (exporter)

- `agent_runs` keys `(session_id, id)`: one agentId in two sessions exports twice. *Evidence:* `dup_agent_id/` — build a trace per session and export both; assert two rows and no constraint error. **As built,** the second session is a `replace()` of the extracted run rather than a second fixture tree — the key is the whole claim, and the test cites the two real cross-session agentIds instead of copying their transcripts in. It also asserts the other half: a repeat *within* one session raises.
- **`session_rollups` excludes replayed rows.** *Evidence:* export the `fork_origin/` trace; assert the session's token and cost totals equal the sum over non-replayed rows, and specifically that the origin fork's 10 fresh records are counted exactly once — not twice (no flagging) and not zero times (symmetric flagging). Bolded: this is the query-layer half of the replay contract, and the extractor test alone cannot show a total.
- Replayed rows remain queryable in the base tables. *Evidence:* `SELECT` on `api_calls` returns the fork's copies; only the views drop them.

---

## Slice 4 — session texture

### unit (extractor)

- A `system/compact_boundary` record yields a `Compaction` with trigger, pre/post tokens, and duration from `compactMetadata`. *Evidence:* `chained_message/` (18 boundaries); whole-object assertion on one, plus a count equal to the fixture's `compact_boundary` records.
- Compactions are 1:1 with `isCompactSummary` records in the same fixture. *Evidence:* assert the two counts match — the claim the design used to reject the prior nearest-call inference.
- Title comes from the last `custom-title` record, and from `ai-title` on a legacy session. *Evidence:* `spine/` trimmed to two `custom-title` records (assert the later wins) and `legacy_title/` at 2.1.187.
- `agent_name` comes from the last `agent-name` record. *Evidence:* `spine/`.
- `pr-link` records become `PrLink` rows keyed by transcript line number, so a repeated `pr_number` in one session yields two rows. *Evidence:* `spine/` with two pr-link records sharing a number; assert both rows and their distinct `line_no`.
- **An unparseable final line is dropped with a warning; an unparseable line anywhere else crashes.** *Evidence:* **invented** (labeled — zero malformed lines in ~560K records, so this tolerance is prospective): truncate a fixture's last line mid-JSON and assert `record count == lines - 1` plus the warning; corrupt a middle line and assert the raise. Bolded: the two halves must be tested together, since a tolerance that leaks to any line turns a schema change into silent data loss.
- A `<synthetic>` model prices at zero and sets `synthetic=True`. *Evidence:* one of the 98 real `"model":"<synthetic>"` assistant records in the main-dir corpus, redacted into `spine/`.

### unit (pricing)

- `compute_cost` applies the table: input, output, cache read at 0.1x input, cache write split by TTL (5m x1.25, 1h x2.0) when `usage.cache_creation` is present, all-5m when it is absent. *Evidence:* usage objects lifted verbatim from real assistant records (both shapes occur in `spine/`), against hand-computed expected values in the test.
- A model absent from the pricing table does not silently price at zero. *Evidence:* assert the behavior the implementer chooses — see *Obligations the seams can't reach*, where this contract gap is flagged; the leaf is not dischargeable until the design says which.

### integration (exporter)

- **`corpus_rollups` dedups turns, API calls, and tool calls alike, attributing each to its first-seen session.** *Evidence:* `resume_pair/` — export both sessions, whose shared prefix carries identical `message.id`s and timestamps; assert the corpus totals count the shared turns, API calls, and tool calls once each, attributed to whichever session the fixture's own timestamps make first (read them, don't assume — which of the pair is the ancestor is not established), while each session's own `session_rollups` still reports what its own file recorded. Bolded: revision 2 deduped API calls only, so copied tool calls and turns double-counted at corpus level — 156 tool_use ids span two or more main transcripts.
- `sessions.project_dir` scopes rollups, so one DB holding two projects reports each separately. *Evidence:* two traces with different `project_dir`; assert the filtered rollups.

### end-to-end

- **A session read mid-write self-heals on the next refresh.** *Evidence:* truncate a fixture transcript's last line, refresh, assert the partial record is absent; complete the line, refresh, assert the record is now present and the session's rows match a from-scratch extract. Bolded: this is what buys the design its "no settle window" decision, and nothing else tests it.

---

## Not covered, and why

- **Any live telemetry backend, and the OTLP exporter.** Out of scope in the design, and `.claude/rules/testing.md` forbids a default-path backend call. When OTLP arrives it brings its own delivery bookkeeping behind a marker and an env var.
- **A full-corpus census as a test.** 3,700 files and 2.1 GB of unredacted private transcripts on one machine. It stays a hand-run shell probe (commands in `handoffs/handoff_2026_08_07_audit-trace-pipeline-design.md`), re-run before any registry change. See the flag below — this absence is a real gap, not a free choice.
- **Whether the pricing table matches Anthropic's published prices.** No seam reaches it; the table is a constant, and a test asserting a constant against itself proves nothing. It needs an out-of-band check against the pricing page, recorded with a date.
- **Structured `toolUseResult` parsing, `queue-operation` task-notification bodies, and `wf_*/journal.jsonl` content.** Archived raw by design; only their archiving is an obligation, above.
- **Block ordering inside one assistant message.** The design accepts the loss and points the viewer at `raw_records`. Testing it would test a field we do not populate.
- **Concurrent refreshes.** No design contract says what two simultaneous runs do, so there is nothing to hold an implementation to.
- **DuckDB's own durability.** We rely on its transactions; testing them tests DuckDB.

## Obligations the seams can't reach

Each of these is a real obligation the design's contracts create, and each is reported rather than dropped or demoted to a level where it would prove less.

1. **Registry completeness cannot be proved by a fixture.** `registry_zoo/` proves the listed types parse; nothing in the test suite can show the list still covers a live, drifting, machine-local corpus. This is precisely the claim that failed in revision 1. Recommended discharge: a `@pytest.mark.slow` census test, skipped unless an env var names a real projects root, that asserts every type and subtype found there is in an enum. It is offline-safe and reads no content — only `type` and `subtype`. Until it exists, the guard is a human re-running the shell probe, which is a schedule, not a test.

2. **Export atomicity is only half-reachable.** A trace that violates its own composite key proves the insert path rolls back. Proving that a process killed mid-transaction leaves the DB consistent needs a subprocess and a signal, and would be testing DuckDB's WAL. Recommended: take the constraint-violation leaf, and record the residual reliance on DuckDB in a comment rather than leaving it implied.

3. **"The DB is the durable archive" is broader than any leaf.** The raw-count invariant and the keep-pruned-sessions leaf together cover what the design actually promises. The stronger reading — that a `SessionTrace` could be rebuilt from `raw_records` alone after the files are gone — is not a stated contract and has no test. Either state it in the design and test the round trip, or drop the phrasing.

4. **The pricing table's unknown-model behavior is unspecified.** `compute_cost` must do *something* with a model the table lacks — crash (consistent with the project's fail-fast stance), or record `None`. The design names neither, and new model names appear on a schedule we do not control. The leaf above is written but cannot be discharged until the design decides. This is a design finding, not a testing one.

5. **Mid-write concurrency is simulated, not reproduced.** The self-heal leaf truncates a file the test controls. The real scenario is reading while Claude Code writes, which no in-process test can stage. The simulation covers the parser's contract; it does not cover an interleaving we have never observed.
