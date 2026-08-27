# Testing plan: OTLP export

Obligations for `plans/otlp-export/design.md` (audit verdict CLEAR TO IMPLEMENT,
`handoffs/audit-otlp-export-design.md`), grouped by the design's three slices so each
implementer knows what is due with their code. Every leaf is an obligation; the *Evidence:*
clause names the artifact that discharges it, and an auditor traces each leaf to that
artifact.

Four rules shape everything below.

- Fixtures are **redacted excerpts of real sessions** under `tests/fixtures/`
  (`.claude/rules/testing.md`). **No new fixture session is required** — the recorded corpus
  already carries every shape slices 1 and 2 need, including the replayed-turn parent arm
- Redaction flattened every string to `[redacted]` or a `fixture-*` pseudonym, so **no privacy
  leaf can assert on recorded text**. The privacy tier plants sentinels onto real rows of a
  copied store, labeled invented at each call site — the viewer plan's policy
  (`plans/trace-viewer/testing_plan.md:40`)
- **No test reaches a real backend on the default path.** The world is an in-process OTLP
  receiver; one opt-in, env-gated live leaf exists and never runs green in CI
- Fixture-store facts below were measured 2026-08-08 by building a store from
  `corpus_transcripts()`; canonical-store facts by a read-only probe of `data/traces.duckdb`
  (schema 7, 575 sessions). Both are measurements to re-run at implementation, not assertions

## Levels

Five places tests run. Each leaf sits at the level closest to real behavior its seam allows.

- **unit (StoreSource)** — `tests/extract/test_store.py`. A real DuckDB on `tmp_path`, built by
  `DuckDbExporter` from recorded fixture traces the way `tests/conftest.py:build_store` does;
  never a mock. `SessionTrace` in, rows, `SessionTrace` out
- **unit (span shaping)** — `tests/export/test_otlp.py`. Recorded `SessionTrace` values through
  `fixture_trace`, no store and no HTTP; assertions on the span list the mapper builds. This is
  the only tier that can drive a session the source filter excludes
- **integration (receiver)** — `tests/export/test_otlp__delivery.py`. The design's seam: stdlib
  `http.server` on an ephemeral port, decoding `ExportTraceServiceRequest`, recording every request, scriptable
  to return `partial_success`, 429 or 500. Real httpx, real protobuf, real gzip, a real store
- **end-to-end (refresh + CLI)** — the same receiver, driven through `refresh()` and
  `cli.main("export-otlp", …)` over a fixture-populated store: the exact production path
- **opt-in live** — one env-gated leaf against a real backend, `@pytest.mark.slow`, skipped
  without a key

Every wait carries a deadline and every default-path test runs offline.

## What the fixture corpus already carries

Sixteen sessions export cleanly. Measured 2026-08-08:

| Shape the design needs | What carries it |
| --- | --- |
| **The replayed-turn parent arm** | `fork_origin/`'s `5a88789c…`: source `a61a059e3610e6fb4` holds **2 live api calls under the replayed turn `33438141-776f-4e1e-9bc5-e5d85df18d22`** — the recorded miniature of the corpus's 217 across 17 fork sources |
| **A contentful NULL-`project_dir` session** | `fork_byref/`'s `07a769d7…`: NULL `project_dir` with 2 api calls, 2 tool calls, 1 agent run, 10 raw records. The crash arm has recorded data — see finding B |
| The `turn_id IS NULL` arms | `fork_byref/`'s run `afa3946951a08a798` (2 NULL-turn calls under a run) and `resume_pair/`'s `0a76f771…` (5 NULL-turn calls on `main`) — both arms, both recorded |
| Replay exclusion | `fork_origin/`: 1 replayed turn of 20, 1 replayed api call of 36, 4 replayed tool calls of 42 |
| Matched run vs launch ack | `spine/`'s `ac461ef46b4bb8e32`: spawning call 11 ms, run 4 m 50 s — the timing rule discriminates on recorded data |
| A run under a run | `spine/`'s `af6473ae437c9608d` under `ac461ef46b4bb8e32` |
| An orphan run | `teammate/`'s `aarchitect-5144001ac50718bc`, `tool_use_id` NULL |
| Nullable tool times and flags | 7 tool calls with `ended_at IS NULL`, 17 `duration_synthetic`, 3 `server_side`, 2 `is_error` |
| A `<synthetic>` call | `spine/`'s `03b918cc…` |
| **Root end before a child's end** | three sessions — `5a88789c…` by 34 minutes, plus `8d930c77…` and `10d0349d…` |
| Compactions | 6, all `source = 'main'` — the arm that can never be a copy. Every fork-run arm of the copied-prefix rule is planted |
| PR links | `spine/`'s two records sharing `pr_number` 656 at lines 33 and 34 |
| Three distinct `project_dir` values | `/Users/nob/repos/mycelia` (13 sessions), `/invented/project`, `/repo` — the source filter discriminates without a plant |
| Live-row totals for the span formula | turns 19, api calls 35, tool calls 38, compactions 6, contentful roots 15, orphan runs 1 |
| A fork run to hang planted compactions on | `fork_origin/`'s `a61a059e3610e6fb4` (`is_fork`, `started_at` 18:05:03.221) and `fork_byref/`'s `afa3946951a08a798`; `spine/`'s `ac461ef46b4bb8e32` is the non-fork run the crash arm needs |

**What no fixture can carry**, because redaction flattened every string and no recorded session
holds the shape: transcript text to leak, NULL agent-run times (0 in the whole canonical
corpus), a timeless contentful session, a compaction in a fork-run source (all 6 fixture
compactions are `main`; the canonical store's 16 are not redactable into a fixture on their
own), a `/`-bearing id component (0 across every shipped table), and a matched↔orphan flip.
Each is planted below and labeled at its call site.

### Planted values

All planted onto **real rows of a copied store or a recorded trace**, labeled invented where
used:

- **Privacy sentinels** — one distinct sentinel string per excluded field: `sessions.title`,
  `sessions.agent_name`, `turns.prompt`, `api_calls.text`, `api_calls.thinking`,
  `tool_calls.input`, `tool_calls.result`, `agent_runs.description`, `pr_links.pr_url`,
  `pr_links.pr_repository`. Distinct so a failure names the field that leaked
- **A key sentinel** — an `OTLP_HEADERS` / backend key value asserted absent from every stream
- **A matched↔orphan flip** — `spine/`'s run `ac461ef46b4bb8e32` with `tool_use_id` set to None:
  a recorded run minus one field
- **NULL agent-run times** and **a timeless contentful session** — two single-field edits to
  recorded rows, each standing for a shape the model permits and the corpus lacks
- **Compactions re-sourced onto a run, at four timestamps** — one recorded compaction copied onto
  `fork_origin/`'s fork run `a61a059e3610e6fb4` at a timestamp before, at, and after its
  `started_at` of 18:05:03.221, plus one onto `spine/`'s non-fork run `ac461ef46b4bb8e32`. Only
  the timestamp and source are invented; the `mycelia-analysis` plan re-sources a compaction the
  same way. A fifth arm nulls the fork run's `started_at`
- **A `/`-bearing id component** — a recorded agentId with a slash inserted, for the id
  function's crash
- **A childless NULL-`project_dir` session** — `fork_byref/`'s main transcript built *without*
  its subagent file. A trim of recorded data, not an invention. As built, "childless" means
  free of *work* rows: every recorded transcript leaves `raw_records`, and the trimmed main
  transcript leaves 3, so `ARCHIVE_TABLES` in `src/hyphae/extract/store.py` carves the
  archive tables out and only a work-table row trips the crash

---

## Slice 1 — seam and spine

### unit (StoreSource)

- **A recorded trace round-trips through the store unchanged.** *Evidence:* export `spine/`'s
  trace with `DuckDbExporter`, read it back with `StoreSource.extract()`, and assert one
  `trace == expected` over the whole object — every entity list, `None` staying `None`
  (`ApiCall.fallback_from`, `cache_5m_tokens`, `agent_runs.description` on `8d930c77…`'s
  workflow run, `Session.title`). Bolded: everything the mapper ships is only as true as this
  rebuild, and a column silently dropped here ships a corpus missing a field nobody notices.
- The round trip holds for every fixture session, not one. *Evidence:* parametrize over
  `corpus_transcripts()`; assert equality per session. Discovery-phrased, so a fixture added
  later is covered without an edit.
- `SessionTrace.extractor` and `extractor_version` come back as `extract_state` recorded them,
  never as `StoreSource`'s own name (design `:19`, decision `:125`). *Evidence:* assert both
  against the `extract_state` row — the contract the whole-object leaves above rest on, and the
  one field a reader is tempted to stamp itself.
- `sessions()` returns `SessionSource(id, files=(), fingerprint)` with the fingerprint
  `extract_state` holds. *Evidence:* assert the tuple equals the `extract_state` row and that
  `files` is empty — `refresh()` never reads it (`pipeline.py:68-76`).
- The filter is at-or-under `project`, by path component. *Evidence:* the three recorded
  `project_dir` values discriminate the "at or under" half; the boundary half plants a sibling
  `project_dir` of `/Users/nob/repos/mycelia-other` onto a recorded session (the `worktree_db`
  precedent in `tests/conftest.py`) and asserts it is absent under `--project
  /Users/nob/repos/mycelia`. A string-prefix filter passes the first half and fails here.
- **A NULL-`project_dir` session is excluded when childless and crashes when it holds content.**
  *Evidence:* both arms on recorded rows — the exclusion from a store built on `fork_byref/`'s
  main transcript alone (a childless NULL-project session — childless of work rows; it keeps 3
  `raw_records`), the crash from the same session with its subagent file, which holds 2 api
  calls, 2 tool calls, 1 agent run and 10 raw records;
  assert the message names the session and the row counts, and that it carries no transcript
  text. Bolded: this is the whole bounded-absence claim, and it is the one design rule the
  shared fixture corpus trips on today (finding B).

### unit (span shaping)

- Root, turn and chat spans are emitted for a recorded session with the design's names and
  kinds. *Evidence:* `spine/`; compare the whole `(name, kind, parent)` list against an expected
  list spelled out in the test.
- **A span id is digest bytes, not hex characters.** *Evidence:* for every emitted span, assert
  `span_id == hashlib.sha256(f"{sid}/{kind}/{source}/{natural_id}".encode()).digest()[:8]`
  recomputed in the test, and the same for the 16-byte `trace_id`. Bolded: the killed mutant is
  `.hexdigest()[:16]`, which is also 16 bytes and passes any length-only assertion, and which
  gives 32-bit span ids that collide with ~10% probability inside the 29K-span session.
- **Ids are stable across re-export.** *Evidence:* shape the same recorded trace twice, and
  again from a store rebuilt from scratch; assert the three span-id sets compare equal. Bolded:
  at-least-once with stable ids is the entire delivery promise, and an id that moves turns a
  re-send into an unrelated second trace.
- No two spans in a trace share an id, over every fixture session. *Evidence:* assert
  `len(set(ids)) == len(spans)` per session; extended to every kind in slice 2.
- **The id function crashes on a `/`-bearing key component.** *Evidence:* two arms — the crash
  from a planted slash in a recorded agentId (labeled: 0 shipped rows hold one), asserting the
  message names the offending component; and a sweep asserting no `source` or natural id in any
  shipped table of the fixture store contains `/`, so the tripwire and the corpus it guards are
  both pinned. Bolded: without the crash a slash makes two different tuples hash to one span id
  silently, and `raw_records` already carries 358 `wf_<id>/journal` sources one table away.

### integration (receiver)

- **The receiver seam decodes what the exporter encoded.** *Evidence:* export one recorded
  session; assert the receiver's decoded span list equals the mapper's own, field by field,
  including resource attributes and both id fields. Bolded: every decoded-capture leaf in this
  plan rests on this one, and a receiver that silently drops a field makes the rest vacuous.
- Resource attributes carry `service.name` = the project directory name, the exporter version,
  and `hyphae.telemetry.source = "store-export"`; `--service-name` overrides the first.
  *Evidence:* `spine/` under `/Users/nob/repos/mycelia` gives `service.name == "mycelia"`; a
  second run with the flag gives the override.
- A confirmed session writes exactly one `otlp_delivery` row carrying the shipped fingerprint,
  the current `mapper_version`, `delivered_at`, and `spans_sent`. *Evidence:* SQL on
  `otlp_delivery` after a clean export.
- **`spans_sent` counts what the receiver decoded, not what the mapper built.** *Evidence:*
  compare the column against the receiver's own span tally across the whole run. Bolded: it is
  the local manifest a future `--verify` compares against, and a number taken from the sender's
  intent proves nothing about delivery.
- **A 500 crashes, writes no row, and the next run re-sends the whole session.** *Evidence:*
  scripted 500 (retries exhausted); assert the raise, `count(*) FROM otlp_delivery = 0`, then
  flip the receiver to 200 and re-run `refresh()` — the receiver's second-run span set equals
  the session's whole span set. Bolded: this walk is at-least-once, and the failure it guards is
  prior-art issue #2's 375 never-retried sessions.
- **A nonzero `partial_success` crashes naming the session and the batch, writes no row, and
  poisons the corpus.** *Evidence:* receiver returns 200 with a rejecting `partial_success`
  body; assert the message names the session id and the batch index and contains no transcript
  text; assert no row; assert a second `refresh()` crashes at the same session and that a
  session later in iteration order never shipped. Bolded: the poison shape is the design's
  accepted cost, and the leaf is what makes it a documented behavior rather than a surprise —
  it is also the executable half of the "no override" statement `docs/otlp-export.md` owes an
  operator (design `:56`), since a skip flag added later fails here.
- **A second `refresh()` with nothing changed sends nothing.** *Evidence:* assert the receiver
  saw zero requests on the second pass and `delivered_at` is unchanged. Bolded: this is what
  lets the command run on a schedule instead of duplicating the corpus each time.
- A changed fingerprint re-sends the whole session. *Evidence:* rewrite `extract_state`'s
  fingerprint, refresh; assert the receiver saw the full span set and the delivery row's
  fingerprint moved.
- **A stale `mapper_version` re-sends everything.** *Evidence:* bump the mapper version constant,
  refresh; assert `fingerprints()` omitted the session, that the receiver saw the full span set,
  and that the row now carries the new version. Bolded: this is the only recovery path from a
  mapper bug, it rides `fingerprints()` rather than a new protocol method, and nothing else
  exercises the trick.
- Delivery state is per backend. *Evidence:* export the same store under two backend names
  against the same receiver; assert two rows and two full sends, and that delivering to one
  leaves the other's row untouched.
- **`otlp_delivery` survives a re-extract.** *Evidence:* deliver a session, then run
  `DuckDbExporter.export()` over it (the replace transaction); assert the delivery row is still
  there. Bolded: a table added to `_TABLES` by reflex would erase the ledger on the next
  extract, and every later run would silently duplicate the corpus.
- The table is created lazily without a schema bump. *Evidence:* open a store with no
  `otlp_delivery`, run an export, assert the table exists and `meta.schema_version` is
  unchanged — and that `tests/view` and `tests/enrich`'s schema checks still pass against it.

### end-to-end (refresh + CLI)

- `cli.main("export-otlp", project, "--db", …, "--backend", "generic")` against the receiver
  produces the spans and the rows `refresh()` produces. *Evidence:* invoke in-process; compare
  decoded spans and `otlp_delivery` rows against a direct `refresh()` over a copy of the store.
- Missing configuration refuses at startup, before anything is read. *Evidence:* unset
  `OTLP_ENDPOINT`; assert the exit names the variable, that the receiver got no request, and
  that `otlp_delivery` was not created.
- **A key is never printed.** *Evidence:* run with a sentinel key value through a failing
  export; assert the sentinel appears in neither stdout, stderr, the exception text, nor any
  warning. Bolded: `CLAUDE.md` makes this a hard rule, and the crash paths are exactly where a
  key gets interpolated into a message by accident.
- A store held by another writer fails fast with DuckDB's lock error. *Evidence:* a subprocess
  holding a write connection (the viewer plan's `test_lifecycle.py` precedent, and it must be a
  subprocess — an in-process second connect raises a different error); assert the run raises
  naming the lock rather than hanging or half-delivering.

### integration (privacy)

- **No attribute in the default set carries transcript text.** *Evidence:* plant one distinct
  sentinel per excluded field onto real rows of a copied store, export, and assert **no
  sentinel appears anywhere in the un-gzipped request bodies** — the raw bytes, not the parsed
  attribute dict, so a stray field or a `logfire.msg` label is caught. Assert the converse in
  the same test: the metadata that must ship (tool name, model, token counts, cost, stop reason,
  agent type, `command_name`, bare `pr_number`) is present, so a mapper that ships nothing
  cannot pass. Bolded: publishing a transcript to a third party is irreversible, and the
  whole-payload sweep is the only form of this leaf that covers an attribute added next month.

---

## Slice 2 — full shaping and privacy

### unit (span shaping)

- **Every emitted span's parent id exists in the trace, and each trace has exactly one root.**
  *Evidence:* over every fixture session, assert
  `{s.parent for s in spans if s.parent} <= {s.id for s in spans}` and one parentless span;
  the sweep **must include `fork_origin/`'s `5a88789c…`**, whose replayed-turn calls are the
  recorded case. Bolded: this is the B-class invariant the audit's blocker broke, and the sweep
  is what makes a future span kind land inside the tree or fail here.
- **A live api call under a replayed turn parents to its run's `invoke_agent` span.**
  *Evidence:* `fork_origin/`'s 2 live calls under replayed turn `33438141…` in source
  `a61a059e3610e6fb4`; assert their parent is `sha256(f"{sid}/agent_run//a61a059e3610e6fb4")`'s
  span id, computed from the child's own `source` with no join. Bolded: without this arm 217
  chat spans and their tool children dangle in the real corpus, and the sweep above would pass
  if the arm were implemented as "parent to root" — this leaf names the parent it must be.
- A live api call with `turn_id IS NULL` parents to its run's span outside `main`, and to the
  root on `main`. *Evidence:* two recorded arms — `fork_byref/`'s run `afa3946951a08a798` (2
  calls) and `resume_pair/`'s `0a76f771…` (5 calls on `main`). `fork_byref/`'s session is the
  one the source filter excludes, so this leaf drives the mapper directly rather than through
  `refresh()` (finding B).
- **Replayed rows never become spans.** *Evidence:* `fork_origin/` — assert no span carries the
  natural id of its 1 replayed turn, 1 replayed api call or 4 replayed tool calls, and that the
  session's span count equals the live-row formula. Bolded: a fork's copies double-count in
  every backend aggregation, which is the decision's whole reason.
- A matched `ToolCall` becomes one `invoke_agent {agent_type}` span, not an `execute_tool`
  launch ack, timed to the run's own clock. *Evidence:* `spine/`'s `toolu_015dP3…` /
  `ac461ef46b4bb8e32` — assert one span named `invoke_agent claude`, no `execute_tool Agent`
  span for that id, and a duration of 4 m 50 s rather than the call's recorded 11 ms.
- The run's turns, calls and compactions nest under its `invoke_agent` span. *Evidence:*
  `spine/`'s nested pair `af6473ae437c9608d` under `ac461ef46b4bb8e32`; assert every span whose
  `source` is a run reaches that run's span, and that the two runs nest rather than sitting flat.
- **The `invoke_agent` span id comes from the agent_run key, so a matched↔orphan flip does not
  move it.** *Evidence:* `spine/`'s `ac461ef46b4bb8e32` shaped twice — once as recorded, once
  with `tool_use_id` set to None (a planted single-field edit, labeled: no recorded run flips);
  assert the span id is byte-identical and equals the agent_run key's digest, and that no
  `tool_use_id` value enters the hash. Bolded: the alternative key re-ids the span on every flip
  and forces every subagent child through a join to find its parent.
- An orphan run is an `invoke_agent` span under the root with `hyphae.orphan = true`.
  *Evidence:* `teammate/`'s `aarchitect-5144001ac50718bc`, a recorded orphan.
- A plain tool call is `execute_tool {name}` under its chat span. *Evidence:* `spine/`; assert
  the name and parent on a recorded non-Agent call.
- Nullable and flagged tool times: `ended_at IS NULL` ends at start with
  `hyphae.incomplete = true`; `duration_synthetic` and `server_side` ship as attributes over
  real times, never as invented ones. *Evidence:* the corpus's 7 incomplete, 17 synthetic and 3
  server-side tool calls, `server_tools/` for the last.
- Zero and negative durations floor to 1 ms. *Evidence:* read the fixture store for a row whose
  `started_at == ended_at` and assert the floor on it; plant one only if the query finds none,
  labeled — phrased as a lookup so it does not rot.
- **NULL agent-run times crash.** *Evidence:* planted (labeled — 0 of the canonical corpus's
  2,487 runs has one); assert the raise names the run and the session. Bolded for the same
  reason as the poison-session leaf: it is a fail-fast the design chose knowing it blocks a
  corpus, so the message is the whole product.
- A timeless session reaching the mapper crashes as schema drift. *Evidence:* planted (labeled)
  — the source filter excludes every real one, which is exactly why the mapper's own rule needs
  a test.
- A `<synthetic>` call ships as `chat <synthetic>` with `hyphae.synthetic = true`.
  *Evidence:* `spine/`'s `03b918cc…`.
- A compaction is a `duration_ms`-long span under the root, or under its source run's span.
  *Evidence:* `compaction/`'s two recorded main compactions for the root arm; the run arm plants
  a recorded compaction onto a run source (labeled — all 6 fixture compactions are `main`),
  following `plans/mycelia-analysis/testing_plan.md:106`.
- **The copied-prefix rule decides a compaction's replay, and a tie is a replay.** *Evidence:*
  one recorded compaction planted onto `fork_origin/`'s fork run `a61a059e3610e6fb4` at three
  timestamps around its `started_at` of 18:05:03.221 — before ⇒ no span, **exactly at ⇒ no
  span**, after ⇒ a span — plus a fourth arm on a fork run whose `started_at` is NULL ⇒ no span,
  and a fifth asserting a `main` compaction always ships. Bolded, and the tie arm is the leaf's
  teeth: the corpus refutes tie ⇒ live at session `c7c4cae9-78a8-49ab-a06a-afe92c09808a`,
  compaction `83f550ae-b02a-471e-8c9e-018630ab8e59`, source `a09d7c5f4475f2fbf`, whose copy sits
  at its run's `started_at` to the millisecond (re-verified 2026-08-08), so `<` instead of `<=`
  ships a second live span for one recorded event and every other arm still passes.
- **A compaction before a non-fork run's `started_at` crashes.** *Evidence:* planted onto
  `spine/`'s `ac461ef46b4bb8e32` (labeled — 0 of the canonical store's 847 non-fork-run
  compactions is one); assert the raise names the session, the source and both timestamps.
  Bolded: the rule's whole safety is that only a fork can hold a copy, and this is the arm that
  fails loudly rather than silently dropping a live compaction when that stops being true.
- A `PrLink` is a span event on the root carrying the bare number and no URL. *Evidence:*
  `spine/`'s two records sharing `pr_number` 656 at lines 33 and 34; assert two distinct events
  and that neither carries `pr_url` or `pr_repository`.
- **The root's span end covers its children while its attributes keep the recorded value.**
  *Evidence:* `5a88789c…`, whose root ends 34 minutes before its last api call, plus `8d930c77…`
  and `10d0349d…`; assert `span.end == max(child ends)` and that the recorded `ended_at` survives
  as an attribute. Bolded: three recorded sessions render as broken waterfalls without it, and
  the attribute is what keeps the stretch from becoming a lie.
- GenAI semconv attributes ride every chat span. *Evidence:* `spine/`; one whole-dict comparison
  on a recorded call covering `gen_ai.operation.name`, `gen_ai.request.model`,
  `gen_ai.conversation.id`, the token counts, cost, `stop_reason` and `effort`.

### integration (privacy)

- **`--include-text` widens exactly the excluded set, and `--max-chars` truncates it.**
  *Evidence:* the same planted store as slice 1's sweep, exported with
  `--include-text --max-chars 20`; assert every excluded field now has an attribute of exactly
  20 characters, that character 21 of each sentinel is absent, and that the default run still
  ships none of them. Bolded: the two leaves are one obligation — a default that excludes
  nothing and a flag that includes everything both pass in isolation.
- The widened set is a superset of nothing else. *Evidence:* diff the attribute key sets of the
  default and widened runs; assert the difference is exactly the design's named field list, so a
  field added to the include path without a decision fails here.

---

## Slice 3 — delivery hardening and named backends

### integration (receiver)

- **A multi-batch session partitions its spans across POSTs with no span sent twice.**
  *Evidence:* construct `OtlpExporter(batch_spans=…)` bound down against a recorded session, so
  the boundary is a real overflow of recorded spans rather than a planted one; assert the POST
  count is `ceil(n / size)`, that the union of decoded spans equals the session's span set, and
  that no span id appears in two batches. Bolded: prior-art issue #1 was a batching bug that
  lost 82.9% of spans while reporting success.
- The production `batch_spans` and rate defaults are pinned by value. *Evidence:* assert 2,000
  and 300/s against the constructor defaults with a comment naming the design paragraph. Every
  other leaf in this tier binds test-sized values, so without this pin the tier passes at any
  defaults — including ones that reproduce issue #6.
- **A failure on batch N leaves no delivery row and the next run re-sends batch 1 too.**
  *Evidence:* receiver scripted to 500 on the second batch; assert the raise, zero rows, then a
  clean run whose decoded span set equals the whole session — and assert the receiver's total
  across both runs holds batch 1 twice, which is the honest duplicate cost of at-least-once.
  Bolded: this is the mid-session crash walk the audit verified on paper.
- **Nothing marks delivered but 2xx with zero rejections.** *Evidence:* parametrize the receiver
  over 400, 429-until-exhausted, 500, and 200-with-`partial_success`; assert `otlp_delivery` is
  empty after every case. Bolded: `delivered` is the only word this system says about a remote
  it cannot query, and this leaf is its whole meaning.
- A 429 carrying `Retry-After` is retried after the named delay and then succeeds, writing one
  row. *Evidence:* scripted receiver with a recording `sleep=`; assert the requested delay equals
  the header, the request count, and the single row.
- **The token bucket paces the sends through the injected seam.** *Evidence:* pass `monotonic=`
  and `sleep=` fakes to `OtlpExporter`, run a recorded session at a bound rate and batch size,
  and assert the sequence of delays the bucket *requested* — never wall-clock elapsed time.
  Bolded: issue #6 measured 40% silent server-side loss without a limiter and 0% with one, its
  absence is invisible until the data is gone, and a wall-clock version of this leaf is both
  slow and a flake.
- Both waiters share the seam: no test-visible wait happens through `time.sleep`. *Evidence:*
  run the pacing and the 429-backoff leaves with a `sleep=` that raises, and assert each failure
  comes from the injected callable — a backoff that reaches for the module clock directly would
  pass every assertion above while sleeping for real in CI.
- Requests are gzipped and the endpoint receives valid protobuf. *Evidence:* the receiver
  asserts `Content-Encoding: gzip`, inflates, and parses `ExportTraceServiceRequest` — a receiver that accepted
  plain bytes would hide a missing encode step.
- The backend registry resolves endpoint, key variable and header name per backend. *Evidence:*
  drive honeycomb and logfire with their endpoints overridden to the receiver; assert the
  outbound header is `x-honeycomb-team` for one and `authorization` **with no `Bearer` prefix**
  for the other — the prior-art detail (`claude-otel:114`) a reflex typo breaks silently.

### end-to-end

- **The emitted span total equals the mapping-true formula.** *Evidence:* over the fixture
  store, compute `live turns + live api calls + live tool calls + copied-prefix-live compactions
  + contentful roots + orphan runs` in SQL inside the test and assert the exported count equals
  it — the formula, not today's 114, so the leaf does not rot as fixtures land. Bolded: the
  audit's M1 showed the design's own headline numbers off by 2,441 from counting matched runs
  twice, and this is the check that would have caught it.
- **The census computes its compaction term by the mapper's rule, not `live_compactions`.**
  *Evidence:* assert the two disagree on a store carrying a planted fork-run copy — the view
  returns it, the mapper does not — and that the census matches the mapper. Bolded: the view's
  `_COUNTED` comment claims it is replay-free and the probe disproves it (4 of the canonical
  store's fork-source copies survive it, re-verified 2026-08-08), so the obvious simplification
  is wrong in a direction nothing else in the suite would catch.
- Every within-session duplicated compaction `id` keeps exactly one live copy. *Evidence:* the
  same census, asserting the invariant over the store it counts — true 2 of 2 in the canonical
  corpus today, and the guard that tells us the day a fork shape lands that the rule cannot
  separate.
- The formula and the invariant hold against the real corpus, without sending. *Evidence:* an
  env-gated `@pytest.mark.slow` dry-run leaf that opens `data/traces.duckdb` read-only, shapes
  every session, and asserts both — the design's slice-3 corpus dry run, offline and reading no
  content. Skipped unless the variable names a store, mirroring the pipeline plan's census
  pattern.

### opt-in live

- One env-gated live send reaches a real backend and is accepted. *Evidence:*
  `@pytest.mark.slow`, skipped unless an opt-in variable **and** a backend key are set; ship the
  two smallest fixture sessions, assert 2xx with zero `partial_success` rejections and a written
  delivery row, and assert that `mise run test` with the variable unset makes no network call.
  It never runs green in CI, and that is acknowledged rather than worked around — it is the only
  leaf that can touch auth and dataset routing at all.
- **An accidental live call fails loudly.** *Evidence:* an autouse fixture in
  `tests/export/conftest.py` that makes any request to a host other than the receiver's
  loopback address raise unless the live marker is present; assert it by attempting a call to a
  public host in a test. Bolded: the enrichment plan's finding 4 — without this the offline
  guarantee rests on review, and the failure bills money or publishes a transcript.

---

## Not covered, and why

- **Live auth, dataset routing, and whether spans land.** One key and one network away; the
  opt-in leaf above is the whole coverage, and it proves acceptance, not persistence
- **Whether Honeycomb or Logfire collapses re-sent identical `(trace_id, span_id)` pairs.** The
  design's open question. Our receiver counts duplicates faithfully, which tells us what we
  sent; what a backend UI does with them is settled by a paid key and a deliberate double-send,
  not by a test
- **Server-side silent drop at volume** (issue #6: HTTP 200 while dropping ~40%). No seam we own
  reaches it. The rate limit is the mitigation and the only real check is manifest-vs-landed
  through a backend query API, which `--verify` defers
- **`--verify` itself.** Out of scope in the design; `spans_sent` is stored so it can be built
  later, and the leaf above pins that number's truthfulness now
- **Real network failure modes** — TLS, DNS, a connection dropped mid-body, a proxy rewriting
  gzip. The scripted receiver covers status codes and bodies, not the transport beneath them
- **A process killed mid-export.** Nothing is written until every batch is confirmed, so the
  crash-and-resend walk is covered by the failure leaves; proving it under SIGKILL would test
  DuckDB's WAL
- **Attribute limits and timestamp rules of a specific backend.** Unknowable without a key —
  and the design's answer to hitting one is the poison-session crash, which is tested
- **Corpus-level resume dedup.** Out of scope in the design: a resumed session's copied history
  appears in both traces, exactly as per-session rollups double-count it

## Findings for the designer

The design's revision resolved five of the seven this plan opened. **A** is answered by the
copied-prefix rule (design `:68`, decision `:121`), which the leaves above discharge on planted
timestamps across all four arms — I re-probed the store 2026-08-08 and every number the rule
rests on holds: 12 after / 3 before / 1 tie among the 16 fork-source compactions, the tie at
`c7c4cae9…` / `83f550ae…` / `a09d7c5f4475f2fbf` to the millisecond, 0 non-fork-run cases of 847,
and exactly one live copy in each of the 2 duplicated groups. The designer's own find — that
`live_compactions` keeps 4 of those copies despite its `_COUNTED` comment — has its own census
leaf. **C**, **D**, **E** and **G** are stated contracts now (`monotonic`/`sleep`, `batch_spans`,
the crashing id function, verbatim provenance) and the leaves assert them rather than asking for
them. Two remain, neither blocking.

**B. The shared fixture corpus cannot be exported wholesale.** `fork_byref/`'s `07a769d7…` has
NULL `project_dir` and 2 api calls, 2 tool calls, 1 agent run and 10 raw records, so the
design's crash-on-contentful-NULL-project rule fires on `corpus_db`. This is good news — the
crash arm has recorded evidence rather than a plant — but the OTLP end-to-end tier needs its own
store that excludes that transcript, and the `turn_id IS NULL` shaping leaf must drive its run
through the mapper directly. Worth one line in the design so the implementer does not read the
crash as a bug.

**F. Nothing at any level proves a span persisted.** Issue #6's whole lesson. Every leaf here
proves what we sent and what a receiver we wrote decoded. The design says as much; naming it
here so no auditor reads the delivery tier as proof of delivery.

## Obligation count

| Slice | Area | Obligations |
| --- | --- | --- |
| 1 | unit (StoreSource) | 6 |
| 1 | unit (span shaping) | 5 |
| 1 | integration (receiver) | 12 |
| 1 | end-to-end (refresh + CLI) | 4 |
| 1 | integration (privacy) | 1 |
| **1** | **total** | **28** |
| 2 | unit (span shaping) | 20 |
| 2 | integration (privacy) | 2 |
| **2** | **total** | **22** |
| 3 | integration (receiver) | 9 |
| 3 | end-to-end | 4 |
| 3 | opt-in live | 2 |
| **3** | **total** | **15** |
| | **Total** | **65** |
