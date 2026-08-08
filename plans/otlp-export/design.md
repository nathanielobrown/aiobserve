# Design: OTLP export

Ship the trace store's sessions to an OTLP/HTTP backend — Honeycomb and Logfire first, any OTLP endpoint as the base case — through the existing `Extractor`/`Exporter` seams, unchanged.

Designed against the canonical store (schema 7, probed 2026-08-08, 575 sessions from the mycelia corpus): 294,362 spans corpus-wide under the mapping below (live turns + api calls + tool calls + compactions + 571 contentful roots + 46 orphan runs; matched run/tool pairs collapse into one span — minus the handful of fork-copied compactions the copied-prefix rule excludes, which the slice-3 dry run measures), 29,255 in the biggest session (`f1a1eb9a`); 6 tool calls with `ended_at IS NULL`, 57,460 with `duration_synthetic`, 46 orphan agent runs, 1,229 live api calls with `turn_id IS NULL`, 217 live api calls under *replayed* turns (5 sessions, 17 fork sources), 204 `<synthetic>` api calls, 45 server-side tool calls, 4 sessions with no timestamps — exactly the 4 with `project_dir IS NULL`, all childless. Prior art and its failure record: `/Users/nob/repos/mac_settings/claude-otel/import_transcripts.py` (0.18.0) and its `investigations/pipeline-issues.md` — issues #1, #2, #6, #7 are the constraints here.

## Problem

The store is local; nothing ships traces to an observability backend where waterfalls, aggregation, and sharing are free. The prior importer proved the value and the failure modes: OTLP backends are append-only, never dedupe, drop silently under load (HTTP 200 included), and its ledger/prefix-diff machinery for coping was its largest bug source. The pipeline design deferred OTLP with the instruction that it "brings its own delivery bookkeeping behind the same `fingerprints()`/`export()` surface" (`plans/trace-pipeline/design.md:195`). The shape-deciding constraint: the `Exporter` contract is atomic replace, and an append-only remote cannot replace — so the design must say honestly what delivery it promises instead.

## Call paths, current → proposed

Current: `cli.main("extract")` → `refresh(project, extractor=ClaudeCodeExtractor, exporter=DuckDbExporter)`.

Proposed: `cli.main("export-otlp")` → `refresh(project, extractor=StoreSource(db), exporter=OtlpExporter(backend, db))` — the same loop, untouched:

1. `StoreSource.sessions(project)` — reads `extract_state` joined to `sessions`, filtered to `project_dir` at or under `project`; returns `SessionSource(id, files=(), fingerprint=extract_state.fingerprint)`. Sessions with `project_dir IS NULL` are excluded by construction — all 4 in the corpus are childless bookkeeping shells — and `sessions()` crashes if a NULL-project session holds child rows, so the exclusion stays a bounded absence rather than a silent drop
2. `OtlpExporter.fingerprints()` — reads the `otlp_delivery` table for its backend, dropping rows whose recorded `mapper_version` is stale, so a span-shaping change re-sends everything just as an extractor upgrade re-extracts everything
3. per changed session: `StoreSource.extract(source)` rebuilds the `SessionTrace` from the store's rows (flat lists mirror the tables 1:1 — mechanical), then `OtlpExporter.export(trace, fingerprint)` shapes spans, POSTs them in checked batches, and writes the delivery row only after every batch is confirmed. The rebuilt trace's `extractor`/`extractor_version` are `extract_state`'s values verbatim — the parser that produced the rows, never `StoreSource`'s own name — so the round trip is whole-object equality and provenance survives the store

The source is the store, not the transcripts on disk: the store is the archive (pruned sessions exist only there), the backend then mirrors exactly what analyses and the viewer cite, and a run costs DuckDB reads instead of re-parsing ~550K records.

## File-tree diff

```
src/aiobserve/
  extract/store.py     NEW  StoreSource: canonical store rows → SessionTrace
  export/otlp.py       NEW  span shaping, protobuf encode, batched delivery, otlp_delivery table, Backend registry
  cli.py               CHANGED  export-otlp subcommand; key validation at startup, key never printed
tests/
  extract/test_store.py  NEW  round-trip: export a fixture trace to a tmp store, read it back whole
  export/test_otlp.py    NEW  in-process OTLP receiver; span-shape, delivery, and failure tests
docs/store.md          CHANGED  otlp_delivery table
docs/otlp-export.md    NEW  what ships by default, the delivery promise, backend setup
pyproject.toml         CHANGED  deps: opentelemetry-proto, protobuf, httpx (already transitive via anthropic)
```

## Key contracts

### Delivery bookkeeping

```sql
CREATE TABLE IF NOT EXISTS otlp_delivery (
  session_id VARCHAR, backend VARCHAR,      -- one row per (session, backend)
  fingerprint VARCHAR,                      -- the extract_state fingerprint that was shipped
  mapper_version VARCHAR,                   -- span-shaping version; stale ⇒ treated as undelivered
  spans_sent BIGINT,                        -- the local manifest: what a future --verify compares against
  delivered_at TIMESTAMP,
  PRIMARY KEY (session_id, backend))
```

Lives in `traces.duckdb`, created lazily like the enrichment tables (table existence, no schema-version bump), outside the replace transaction's `_TABLES` so re-extracts don't erase it. It therefore dies with the store: a `SCHEMA_VERSION` bump's "extract into a fresh store" remedy erases all delivery rows and the next run re-sends the full ~294K spans per backend as duplicates — safe in direction (duplication, never loss), but `docs/otlp-export.md` must say so, especially while backend duplicate-collapse behavior is an open question. DuckDB is single-writer, so `export-otlp` cannot run beside `extract` or `enrich`; `StoreSource` and `OtlpExporter` share one connection, and a concurrent open fails fast with DuckDB's lock error.

**Delivered** means: every batch of the session returned 2xx and its parsed `ExportTraceServiceResponse.partial_success` reported zero rejected spans. That is still not proof of persistence — issue #6 showed Honeycomb 200-ing while dropping ~40% server-side at 2,575 spans/s — so the rate limit below is the mitigation (300/s landed 177/177), and the only reliable check, manifest-vs-landed via a backend query API, is deferred behind `spans_sent` (a `--verify` must count *distinct* span ids, since failed attempts leave duplicate rows server-side).

A nonzero `partial_success` crashes the run naming the session and batch. For a *deterministic* rejection (a backend attribute cap, a timestamp it refuses) that session becomes a poison pill: every run crashes there, and sessions after it in iteration order stop shipping until the mapper changes. That is the intended fail-fast shape — a deterministic rejection is a mapper bug we need to see, and the fix (a mapper change, hence a `mapper_version` bump) re-sends everything correctly — but the operator move belongs in `docs/otlp-export.md`, which must say explicitly that **no override exists** — no skip flag, no quarantine; a mapper fix plus a `mapper_version` bump is the only path — so an operator mid-incident doesn't hunt for one. The corpus is stuck behind the crash, not silently partial.

**The promise is at-least-once with stable ids, nothing more.** A failed or interrupted export writes no row and the whole session re-sends next run; a changed fingerprint re-sends the whole session; both duplicate already-landed spans on a backend that ignores span identity. No prefix-diff, no ledger file, no settle window — the machinery behind issues #2 and #7. The safe unit of correction on a polluted backend is delete the dataset (or trace, where supported) and re-export; the store makes that a cheap `DELETE FROM otlp_delivery WHERE backend = ?` plus one run.

### Identity

`trace_id = sha256(session_id).digest()[:16]`; `span_id = sha256(f"{session_id}/{kind}/{source}/{natural_id}").digest()[:8]` — the store's own composite keys, hashed. **The `/` delimiter is an invariant: no component may contain one, and the id function crashes if one does.** Every shipped table is slash-free today (0 across the canonical store), but the namespace is Claude Code's, and `raw_records` already carries 358 `wf_<id>/journal` sources one table away — those reach only `raw_records` (journal files feed nothing else in `extract()`), which the mapper never ships, so the crash is a tripwire for drift, not a live hazard. Digest **bytes**, not hex characters: hex `[:8]` would yield 32-bit span ids with ~10% birthday collision inside the 29K-span session; digest bytes give the spec's 128/64 bits. The pipeline design's rejection of `{sid}:turn:{i}` (:176) rejected *index-based* ids, and that reasoning transfers here in the same direction: an index moves when a parser change refilters or reorders, giving the same record a fresh span id on re-send — the one thing stable ids exist to prevent. Natural ids hold still across extractor versions.

Every `invoke_agent` span — matched or orphan — derives its id from the **agent_run** key (`kind = "agent_run"`, empty source slot, id = agentId), never from the tool call it replaces. Two reasons: children in the run's transcript know only their `source` (the agentId), so they can compute their parent id without a join through `agent_runs.tool_use_id`; and a run that flips between matched and orphan across extracts keeps the same span id. The tool_call key ids only plain `execute_tool` spans.

### Span shaping

One trace per session. Replayed rows never become spans; shipping them double-counts in any backend aggregation. Turns, api calls and tool calls carry the extractor's `replayed` flag. Compactions carry none, so the mapper derives it from the copied-prefix shape: a fork copies a contiguous prefix of its parent's transcript, and `AgentRun.started_at` is by contract the first record no earlier transcript already held (`model.py`), so **a compaction in a fork-run source timestamped at or before its run's `started_at` (or in a fork run whose `started_at` is NULL — it copied everything) is a replay and emits no span**. A tie is a replay, not a live row: a fork cannot compact at the instant of its own first record, and when the copied prefix ends at the compaction, the first own record shares its millisecond (session `c7c4cae9-78a8-49ab-a06a-afe92c09808a`, compaction `83f550ae-b02a-471e-8c9e-018630ab8e59`, source `a09d7c5f4475f2fbf` — the recorded case). `main` is first in the extractor's ordering and can hold no copies; a compaction before a *non*-fork run's start is schema drift ⇒ crash (0 today). The rule is data-verified corpus-wide (probed 2026-08-08): all 16 fork-source compactions separate cleanly — 12 strictly after `started_at`, all non-duplicated (genuine own compactions); 3 before and 1 at, all copies of a duplicated `(session_id, id)` — and each of the 2 duplicated groups then has exactly one live copy. A copy whose original lives in *another session* ships in both traces, exactly as the resume caveat under out-of-scope already accepts. Note `live_compactions` does **not** filter these copies (`_COUNTED` marks the table replay-free, which the probe disproved), so the mapper is stricter than the rollup views here and any census must count the compaction term with the mapper's rule, not the view. Resource: `service.name` = project directory name (routes Honeycomb datasets per project; `--service-name` overrides), `aiobserve.exporter.version`, `aiobserve.telemetry.source = "store-export"`.

| Entity | Span | Parent | Honesty notes |
| --- | --- | --- | --- |
| Session | `claude_code.session`, INTERNAL | root | end = max(recorded `ended_at`, latest child end) — 2/575 sessions have subagents running past it; attrs keep the recorded value. A timeless session cannot reach the mapper (the NULL-project filter excludes all 4, and crashes on a contentful one); one that does anyway is schema drift ⇒ crash |
| Turn | `claude_code.turn` | root (`source = "main"`) or its run's span | |
| ApiCall | `chat {model}`, CLIENT | its turn's span; turn **replayed** ⇒ the source run's span (217 calls in 17 fork sources sit under turns the fork replayed, whose first-seen copy hashes a different `source` — without this arm they dangle); `turn_id IS NULL` ⇒ the source run's span (fork continuation), root if main | tokens, cost, `stop_reason`, `effort`, `gen_ai.*` semconv as prior art; `<synthetic>` calls (204) ship with `aiobserve.synthetic = true` |
| ToolCall with matched AgentRun | `invoke_agent {agent_type}` | the chat span | timed to the run's own `started_at..ended_at` (the launch-ack is ~0ms); the run's turns/calls nest under it; NULL run times (model permits, 0 in corpus) ⇒ crash — a shape we need to see |
| ToolCall otherwise | `execute_tool {name}` | the chat span | `ended_at IS NULL` (6) ⇒ end = start + `aiobserve.incomplete = true`; `duration_synthetic` (57K) and `server_side` (45) ship as attributes, never as invented times |
| AgentRun, orphan (46) | `invoke_agent {agent_type}` | root | `aiobserve.orphan = true`; same id key as matched runs |
| Compaction | `claude_code.compaction`, `duration_ms` long | root or its source run's span | replay derived by the copied-prefix rule above — a fork's copy of its parent's compaction emits nothing |
| PrLink | span event on root | | |
| OffloadFile, RawRecord | never shipped | | the archive stays local |

Zero and negative durations floor to 1ms. GenAI semconv attributes (`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.conversation.id`, token counts) plus `claude_code.*` mirrors, and `logfire.msg` row labels — the attribute vocabulary prior art verified against both backends.

### Privacy: metadata-only by default

Transcript-derived text is untrusted and may carry secrets; POSTing it to a third party publishes it. Default ships structure only: tool and model names, ids, counts, tokens, costs, durations, stop reasons, agent types, `command_name`, and bare PR numbers. Excluded by default, field by field: `Turn.prompt`, `ApiCall.text`/`thinking`, `ToolCall.input`/`result`, `AgentRun.description`, `Session.title` (`ai-title` is model-written from conversation content) and `agent_name`, and `PrLink.pr_url`/`pr_repository` (private repo names). Root and turn `logfire.msg` labels carry ids, never the title or prompt. `--include-text` opts the excluded fields in, truncated to `--max-chars` (default 500) — truncated because attributes are a context/ingest cost, opt-in because truncation is not redaction: a credential fits in 200 chars.

### Transport

Hand-built span dicts encoded to `TracesData` protobuf via `opentelemetry-proto`, gzipped, POSTed with httpx; response protobuf parsed for `partial_success`. No `opentelemetry-sdk`: issue #1's 82.9% loss was the SDK's `BatchSpanProcessor` queue silently evicting spans, and the SDK's exporter discards `partial_success` (#6). Here there is no queue to overflow — `export()` builds each batch itself, sends it, and reads the result, so #1's failure shape is unconstructible. Batches are sequential, sized by an `OtlpExporter(batch_spans=…)` parameter, default 2,000 (biggest session ⇒ ~15 POSTs) — a parameter so tests bind it down and cross real batch boundaries on recorded sessions (no fixture session nears 2,000 spans; the viewer's `$page_*` precedent), with the default pinned by value in a test. A token bucket caps spans/s (default 300, `--rate` overrides — the number #6 proved). **Time is a seam:** `OtlpExporter` takes `monotonic: Callable[[], float]` and `sleep: Callable[[float], None]` (production `time.monotonic`/`time.sleep`), and both the bucket's pacing and retry backoff wait through them — tests assert the delays *requested*, never wall-clock elapsed. 429/5xx retry with backoff honoring `Retry-After`, then crash without writing the delivery row. Full corpus at defaults: 294,362 spans, ~16.4 minutes — fine for a backfill run.

`Backend` registry mirrors prior art (verified endpoints/headers at claude-otel `:114`): honeycomb → `https://api.honeycomb.io/v1/traces`, key `HONEYCOMB_API_KEY`, header `x-honeycomb-team`; logfire → `https://logfire-us.pydantic.dev/v1/traces`, key `LOGFIRE_API_KEY`, header `authorization` (no `Bearer` prefix); generic → `OTLP_ENDPOINT` + optional `OTLP_HEADERS`. Keys load from `.env`, are validated before anything is read, and are never printed.

## Chosen test seam

Same seams as the pipeline: `SessionTrace` in, HTTP out. `test_otlp.py` runs an in-process OTLP receiver (stdlib `http.server` on an ephemeral port) that decodes `TracesData`, records every request, and can be scripted to return `partial_success`, 429, or 500. Shaping tests build `SessionTrace` values directly and assert on decoded spans; the end-to-end test runs `refresh()` over a fixture-populated tmp store against the receiver — the exact CLI path. `test_store.py` proves `StoreSource` by round-trip: `DuckDbExporter.export(trace)` then `StoreSource.extract()` returns an equal trace. Unproven until a key exists: real Honeycomb/Logfire auth and dataset routing, server-side throttle behavior at our volume, and whether either UI collapses re-sent identical span ids.

## Slices

1. **Seam + spine.** `StoreSource` (round-trip test), `OtlpExporter` emitting root + turn + chat spans metadata-only in a single checked POST, `otlp_delivery` with `mapper_version` staleness, CLI `export-otlp --backend generic` with env validation. Verified end to end against the receiver: spans decode with the derived ids; a second `refresh()` skips; a 500 or a nonzero `partial_success` crashes and leaves no delivery row.
2. **Full shaping + privacy.** `execute_tool`/`invoke_agent` replacement and nesting, orphans, compactions with the copied-prefix replay rule, PR events, replay exclusion with the replayed-turn parent arm, nullable-time handling, GenAI attributes, `--include-text`/`--max-chars`. Verified by decoded-capture tests on the existing fork and parallel-tool fixtures, including a pin that every emitted span's parent id exists in the trace — the invariant B-class bugs break.
3. **Delivery hardening + named backends.** Multi-batch sessions, token bucket, gzip, retry/backoff, the honeycomb/logfire registry entries, `spans_sent` bookkeeping. Verified by receiver tests (429-then-success; batch counts; inter-batch pacing) and a corpus dry-run count checked against the mapping-true formula (live turns + api calls + tool calls + copied-prefix-live compactions + contentful roots + orphan runs — 294,362 today before the compaction copies leave; the compaction term is computed by the mapper's rule, not `live_compactions`, and the dry run also asserts every within-session duplicated compaction `id` keeps exactly one live copy — true 2/2 today, guarded because forks keep landing).

## Decisions

- **Source is the canonical store, not disk** — pruned sessions live only there, the backend mirrors what analyses cite, and no re-parse. Rejected: `ClaudeCodeExtractor` directly (misses the archive; can ship what the store never saw).
- **A separate `export-otlp` command** — extract stays fast and offline. Rejected: an exporter flag on `extract` (couples ingestion to remote availability and rate limits).
- **Delivery state in `traces.duckdb`, per backend, lazily created** — beside the fingerprints it compares against, surviving replace like enrichment does. Rejected: a ledger file (the prior pipeline's worst failure class, #2); asking the backend (unqueryable without extra APIs and keys, and #6 shows it lies).
- **Delivered = all batches 2xx + zero partial_success rejections, recorded once at session end** — Rejected: recording "attempted" (#2: 375 sessions never retried); per-batch checkpoints with diff resend (#7: the grown-guard and prefix-diff traps).
- **At-least-once with stable ids; whole-session re-send on any change or failure** — honest about append-only remotes. Rejected: exactly-once machinery (impossible without backend dedupe); prefix-diff (the largest prior bug source).
- **Span ids from natural keys, not indices** — indices move under parser changes; the `:176` rejection transfers in support, not against.
- **Replayed rows excluded** — backend aggregates would double-count fork copies. Rejected: shipping them flagged (no backend UI filters by our flag by default).
- **Replayed-turn parent arm: a live api call under a replayed turn parents to its run's span** — the replayed turn emits no span, and its first-seen copy hashes a different `source`, so without this arm 217 chat spans plus their tool children dangle. Rejected: emitting replayed turn spans (re-opens the double-count); reparenting to the first-seen copy's span (nests a fork's own work under another transcript's turn, and the cross-source id is exactly what dangles).
- **`invoke_agent` ids from the agent_run key, matched or orphan** — children compute their parent from their own `source`, and matched↔orphan flips across extracts keep the id. Rejected: the replaced tool call's key (every subagent child needs a `tool_use_id` join to find its parent, and a flip re-ids the span).
- **A deterministic backend rejection crashes the run, poisoning the corpus until the mapper changes** — fail-fast: it is a mapper bug we need to see, and the fixing `mapper_version` bump re-sends everything. Rejected: skip-and-record quarantine (ships a silently partial corpus and hides the bug; revisit if a real rejection ever appears).
- **NULL-`project_dir` sessions excluded at the source filter, with a crash if one has content** — the 4 in the corpus are childless shells; the crash bounds the absence. Rejected: a mapper-level "emit nothing" rule (dead code behind the filter); silent exclusion (the unbounded-absence failure class the project bar forbids).
- **Metadata-only by default, `--include-text` to widen** — publishing transcript text is a deliberate act. Rejected: truncated-by-default (prior art's choice; truncation is not redaction).
- **`opentelemetry-proto` + httpx, no SDK** — deterministic batches with checked results; #1 unconstructible. Rejected: the SDK (queue + discarded partial_success caused #1/#6); hand-rolled OTLP/JSON (Logfire's JSON acceptance unverifiable without a key; binary protobuf is the encoding every server must take).
- **Rate limit in scope now, 300/s default** — #6 measured 40% silent loss without it and 0% with it; at our volume it costs 17 minutes. Rejected: deferring it (the one protection whose absence is invisible until data is gone).
- **Root end stretched to cover children** — a waterfall whose root ends before its children renders broken; attributes keep the recorded truth. Rejected: recorded end as span end (2/575 break); synthesizing nothing (unusable traces).
- **Compaction replay derived at export by the copied-prefix rule, tie ⇒ replay** — `compactions` has no `replayed` column, and the rule (timestamp at or before the fork run's own-work start) reads the same prefix shape the extractor's flags read, from columns already stored. Rejected: tie ⇒ live (refuted by the corpus — session `c7c4cae9…`'s compaction `83f550ae…` has a fork copy timestamped exactly at its run's `started_at`, which tie-⇒-live would ship as a second live span); shipping the copies with a scope note (double-counts the exact event the replay-exclusion decision exists to prevent, 2 sessions today and unbounded forward); a `replayed` flag on `Compaction` (the right fix eventually — it would also repair `live_compactions` — but a new column forces a `SCHEMA_VERSION` bump, and the fresh-store remedy evicts pruned sessions from the archive this exporter exists to ship).
- **Injected `monotonic`/`sleep` on `OtlpExporter`** — pacing and backoff share one deterministic seam. Rejected: wall-clock timing assertions (CI flakes) and monkeypatching `time` (hides the dependency the contract has).
- **`batch_spans` as a constructor parameter, default 2,000 pinned by a test** — recorded sessions can cross real batch boundaries when tests bind it down. Rejected: a module constant (multi-batch reachable only through a planted synthetic session).
- **Id-key components must not contain `/`; the id function crashes on one** — fail fast on an ambiguous key rather than hash two tuples to one span id silently. Rejected: an escaping scheme or exotic delimiter (machinery for a shape no shipped table has ever held, and a slash-bearing agentId is schema drift we want surfaced, not absorbed).
- **`StoreSource` returns `extract_state`'s `extractor`/`extractor_version` verbatim** — provenance names the parser that produced the rows, and round-trip equality holds over the whole object. Rejected: stamping `StoreSource`'s own name (misattributes rows to a reader and turns every round-trip test into field-by-field with exceptions).

## Out of scope

- **`--verify` (manifest-vs-landed)** — the only reliable delivery check, but it needs a backend query API and a key; `spans_sent` is stored now so it can be built without re-shipping
- **Remote cleanup tooling** — deleting a polluted dataset stays a manual backend operation
- **Corpus-level resume dedup** — each session is its own trace, so history a resume copied appears in both traces, exactly as per-session rollups double-count it; backend-side corpus totals inherit that caveat
- **OTLP logs/metrics, enrichment content in spans, live tail during extract** — traces only, from the store as it stands

## Open questions

- Does either backend's UI collapse re-sent spans with identical `(trace_id, span_id)`, or display duplicates? Decides how loudly `docs/otlp-export.md` must warn about re-sends. Settled by one paid key and a deliberate double-send.
- Honeycomb's and Logfire's query APIs both exist — is either's free tier enough for `--verify`? Settled the same way.