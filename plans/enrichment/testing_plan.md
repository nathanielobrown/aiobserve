# Testing plan: AI enrichment

Obligations for `plans/enrichment/design.md` (audit verdict CLEAR TO IMPLEMENT), grouped by the design's slices so each implementer knows what is due with their code. Every leaf is an obligation; the *Evidence:* clause names the artifact that discharges it. An auditor traces each leaf to that artifact.

Three rules shape everything below.

- Fixtures are **redacted excerpts of real sessions** under `tests/fixtures/`, trimmed to the records the test needs, with the Claude Code version in a README (`.claude/rules/testing.md`). Leaves whose data is invented say so and say why
- **No test calls the Anthropic API on the default path.** `BatchClient` is the seam; every automated test drives a fake. One opt-in, env-gated, two-item live check exists, mirroring the census pattern the pipeline plan recommends
- **No obligation depends on live pricing.** The cost table is a code constant; the estimate leaves assert arithmetic over it, never a published number

Store facts below were re-run 2026-08-07 on **`data/traces_v6.duckdb`** — the stable snapshot, because `data/traces.duckdb` was write-locked by a parallel implementer. Re-run them on the live store at implementation, per the design's own rule.

## Levels

Six places tests run. Each leaf sits at the level closest to real behavior its seam allows.

- **unit (render)** — `tests/enrich/test_prompts.py`. The richest testable unit: rows in, prompt text and `input_hash` out, no I/O and no client. Rows come from a real DuckDB built by running the existing pipeline over `tests/fixtures/` (the `fixture_trace` scaffolding in `tests/conftest.py` plus a `DuckDbExporter` on `tmp_path`); a session-scoped fixture builds it once
- **unit (validation)** — `tests/enrich/test_prompts.py` or a sibling, wherever the implementer lands the output validator. A model-output dict in, an accepted item or a classified failure out. Its data is necessarily invented: model output is not a recorded transcript
- **unit (taxonomy)** — the `StrEnum` and `TAXONOMY_VERSION`. Two leaves, no scaffolding
- **integration (store)** — `tests/enrich/test_store.py`. A real DuckDB file on `tmp_path`, never a mock; assertions are SQL. The base rows are the fixture-built pipeline output, so the natural keys under test are the ones the pipeline really writes
- **unit (batch clients)** — `tests/enrich/test_batches.py` (**new file, beyond the design's `tests/enrich/` list** — the design names three files and puts client tests among them; the client's mocked-SDK tests are a different world from the enricher's and deserve their own). The world is stood in for by the `anthropic` SDK's own result objects, constructed in the test
- **end-to-end (enricher + CLI)** — `tests/enrich/test_enricher.py`. Real DB from fixtures, real store, real renders, a `FakeBatchClient` that records the item keys of each `submit()` call and returns scripted results. `enrich()` and `cli.main("enrich", ...)` drive it; assertions are SQL over the enrichment tables plus the fake's call log

Every wait is bounded and every default-path test runs offline.

## Fixture corpus

The enrichment renders need no new recorded sessions. Nine existing fixture directories already carry every shape the design's renders distinguish; one needs extending. Sources and versions are each fixture's README.

| Fixture | CC version | What it carries **for enrichment** |
| --- | --- | --- |
| `spine/` | 2.1.221 | The session-level workhorse: 4 main turns (two slash, in both tag orderings; two plain), a main-turn `Agent` call `toolu_015dP3…` spawning run `ac461ef46b4bb8e32`, which spawns `af6473ae437c9608d` — a four-round cascade (leaf run → parent run → main turn → session) in one session. Also an incomplete tool call, a `<synthetic>` api call, and an `Agent` call `toolu_01Qn8A…` whose spawned run is **not** in the store |
| `teammate/` | 2.1.211 | A rootless run (`tool_use_id IS NULL`, `spawn_depth = 0`) whose turn prompt is a `<teammate-message …>` — the tag-unwrap case and the session's second child kind. **Extend it:** the recorded run `aarchitect-5144001ac50718bc` has four teammate turns (uuids `d1fb01b4…`, `11b6b551…`, `c94604a0…`, `6e1beb33…`, 2,582–3,597 chars each); the fixture keeps only the first. Add at least the second, so a multi-turn run render has real turns to sequence. Same session also holds the corpus's longest run, `aimpl-rung1-07b89bf51437c28f` at 16 turns |
| `fork_byref/` | 2.1.202 | A zero-turn fork continuation, spawned by a tool call: run `afa3946951a08a798`, `is_fork`, two api calls, no turn at all. Verified one of the store's 41 zero-turn runs, **all 41 of which are forks** |
| `fork_origin/` | 2.1.215 | A run whose parent is another run: fork `a61a059e3610e6fb4` (depth 2) under auditor `acbc29008a04b9702` (depth 1) — topological ordering over `parent_agent_id`. The fork's only turn is `replayed`, so it is a zero-turn continuation in `live_turns` while the auditor keeps the turn |
| `server_tools/` | 2.1.201 | The only fixture with `is_error = True` on a tool call carrying a result — the error-tail render. Also an incomplete call and a zero-turn non-fork run |
| `workflow/` | 2.1.207 | A main turn whose `Workflow` tool line must embed the spawned run's description, the second embedding shape beside `Agent` |
| `compaction/`, `dup_uuid/` | 2.1.198, 2.1.211 | Sessions with no main turns and no runs — the skip rule. 102 of 575 real sessions are in this state |
| `offload/`, `legacy_title/`, `legacy_entrypoint/`, `registry_zoo/`, `resume_pair/` | per README | Not needed by enrichment; listed so their absence reads as a decision |

Invented or planted data, each labeled where it is used, because no recorded session can contain it:

- **Model outputs** — schema-invalid JSON, an out-of-vocabulary `category`, an out-of-vocabulary `outcome`, and a description carrying a secret shape (an obviously fake `sk-ant-api03-…`, `AKIA…`, and a PEM header). Model output is not transcript data; there is nothing real to record, and a real secret could never be committed
- **SDK batch results** of all four types. A real expired-unbilled result takes 24 hours to produce. Build them from the `anthropic` SDK's own result classes, not hand-rolled dicts, so an SDK shape change breaks the test
- **A dangling `parent_agent_id`** — planted by deleting a parent run row from the fixture-built DB. The store holds zero such rows corpus-wide, which is why the design's crash path is currently unexercised
- **Sentinel content planted into real rows.** Every fixture string outside the structural keep-list is redacted to the same `[redacted]` token, so no fixture can discriminate "the render excluded `thinking`" from "the render included it" — both produce the same characters. The exclusion leaves therefore write a unique sentinel into `api_calls.thinking` and into a non-error `tool_calls.result` of a real fixture row and assert its absence. Real row, one field replaced; labeled at the call site

---

## Slice 1 — seam and spine, turn level

### unit (taxonomy)

- Every `Category` and `Outcome` member is a `StrEnum` value the validator accepts, and `TAXONOMY_VERSION` is an `int`. *Evidence:* round-trip each member through the validator; assert the accepted set equals the enum's members exactly, so a member added without a definition comment still cannot widen silently.
- An out-of-vocabulary `category` or `outcome` fails the item rather than widening the vocabulary. *Evidence:* invented model output (labeled) with `category="refactoring"`; assert the item is classified failed and that no row is written for it.

### unit (render)

- A plain main turn renders its prompt, then one line per api call carrying `text` capped at 1.5K, then one line per tool call carrying name, input head, input size, result size, error flag. *Evidence:* `spine/`'s turn `818588ad…`; assert the whole rendered string against a spelled-out expected, so every field's presence, order, and label are visible to the test's reader.
- **A slash-command turn renders `command_name` and `command_args`, never the raw tag XML.** *Evidence:* `spine/`'s two slash turns, one leading with `<command-name>` and one with `<command-message>` (both orderings occur); assert `/model` and `/night-run` appear and that `<command-name>` does not. Bolded: the `prompt` column keeps the tags, so a render that forwards `prompt` verbatim spends budget on markup and reads as content.
- **`thinking` reaches no prompt.** *Evidence:* a unique sentinel planted into `api_calls.thinking` on a `spine/` row (labeled — redaction makes every real string identical); assert the sentinel is absent from the turn render, the run render, and the session render. Bolded: 30.5 MB corpus-wide, and the cost estimate assumes it is gone.
- **Tool result content reaches no prompt except an error tail.** *Evidence:* the same sentinel technique on a non-error `spine/` tool call's `result` (absent), plus `server_tools/`'s real `is_error=True` call (its result tail present, capped at 300 chars). Bolded: results are 390 MB; including them would dominate every prompt.
- A tool line carries both the input size and the result size, as the recorded lengths of those columns. *Evidence:* `spine/` and `server_tools/`; assert the rendered numbers equal `len(input)` and `len(result)` read from the same rows.
- The tool input head is capped at 120 characters and is the head, not a hash or a name. *Evidence:* `workflow/`'s `Workflow` call, whose input keeps a readable `"name": "deep-research"`; assert the rendered head starts with the input's first characters.
  - *As built:* that call's input is 47 characters, so it can show the head is the head but not that the cap bites. The cap is discharged on the same real row at an injected `input_head=20`, as the elision leaf below does for `total`.
- An incomplete tool call renders without crashing and is marked as unanswered. *Evidence:* `spine/`'s `Bash` call with no result; assert the line exists and does not claim a zero-length result.
- **`input_hash` is a function of the rendered content and nothing else.** *Evidence:* render one `spine/` turn twice and assert equal hashes; then change one rendered-in field (a tool call's `name`) and assert the hash changes; then change a field the render does not read (`api_calls.request_id`) and assert it does not. Bolded: every staleness and cascade claim in this plan rests on this being true in both directions.
- Over budget, a turn render keeps the head and the tail of its call sequence and marks the elision, and never exceeds the budget. *Evidence:* `spine/`'s longest turn rendered at an injected small budget (see *Obligations the seams can't reach* — the budget must be a render parameter, since redaction leaves no fixture near 30K chars); assert `len(rendered) <= budget`, that the first and last call lines survive, and that the marker appears.
  - *As built:* the render's unit of elision is a line, and the response header and text lines count as lines. `spine/`'s longest turn renders to six of them, so at any budget that elides anything the surviving head is the response header rather than the first tool line. The test asserts the whole rendered string at an injected 200-char budget: the prompt, the head of the sequence, a counted gap, and the last tool call.

### unit (validation)

- **A description matching a secret shape fails the item, and the item's failure carries the key but not the description.** *Evidence:* invented model outputs (labeled) carrying a fake `sk-ant-api03-…`, an `AKIA…`, and a PEM header; assert each is classified failed, that no row is written, and that the offending string is absent from the failure record. Bolded: this is the one control between a credential in a transcript and a committed phase-3 report.
- A well-formed output with an in-vocabulary category, a valid outcome, and a null `friction` validates and writes a row with `friction IS NULL`. *Evidence:* invented output (labeled); assert the stored row, `NULL` staying `NULL` rather than becoming an empty string.

### integration (store)

- The three enrichment tables and the `enriched_turns` view are created on open and survive a pipeline re-export of the same session. *Evidence:* build the DB over `spine/`, enrich, re-run `refresh()` over the same fixture; assert the `turn_enrichments` rows are byte-identical afterwards. This is the "pipeline replace never touches these tables" contract.
- A second upsert of the same key replaces the row rather than duplicating it. *Evidence:* upsert twice with different descriptions; assert one row, holding the second.
- `enriched_turns` LEFT-joins, so an un-enriched turn appears with NULL enrichment columns. *Evidence:* enrich two of `spine/`'s four main turns; assert the view returns four rows, two with `description IS NULL`.
- The staleness query returns exactly the rows whose `(input_hash, prompt_version, taxonomy_version, model)` differ from what the enricher would use now, and nothing else. *Evidence:* enrich `spine/`, then mutate each of the four fields in turn on one row; assert that row and only that row comes back stale each time.
- **A zombie enrichment — a row whose base turn no longer exists — is swept.** *Evidence:* enrich `spine/`, delete one turn from `turns` (standing for an extractor bump that redraws turn boundaries), run the sweep; assert the enrichment row is gone and the others survive. Bolded: the LEFT-join views hide this drift completely, so nothing else in the system would ever report it.

### end-to-end (enricher + CLI)

- `enrich()` over a fixture-built DB writes one row per enrichable turn, with the fake client's descriptions and the model, versions, and hash the enricher used. *Evidence:* `spine/`; assert the `turn_enrichments` rows whole, and that the fake received one request per turn.
- **A second `enrich()` with nothing changed submits nothing and writes nothing.** *Evidence:* the fake's call log is empty on the second pass and `enriched_at` is unchanged on every row. Bolded: this is what makes `enrich` safe to run beside `extract` on a schedule, and it is the cheapest possible regression net for the whole staleness scheme.
- A `prompt_version` bump re-enriches that level; a `taxonomy_version` bump re-enriches every level; a `--model` switch re-enriches everything. *Evidence:* three runs over the enriched `spine/` DB, each bumping one value (monkeypatched or passed); assert the fake's item keys per run and the changed column in the DB.
- `ANTHROPIC_API_KEY` missing or empty refuses at command start, before any render or any DB write. *Evidence:* `cli.main("enrich", …)` with the variable unset; assert the raise and that the enrichment tables hold no rows.
- The key never appears in output. *Evidence:* set a sentinel key value, force a failing run, and assert the sentinel is absent from stdout, stderr, and the exception text.
- `--dry-run` writes nothing and submits nothing. *Evidence:* the fake's call log is empty and every enrichment table is empty afterwards.
- `--limit N` submits at most N items. *Evidence:* `spine/` with `--limit 2`; assert the fake received two.

## Slice 2 — real clients

### unit (batch clients)

- **`AnthropicBatchClient` maps all four batch result types: `succeeded` upserts, and `errored`, `canceled`, and `expired` each become a classified failure that writes nothing.** *Evidence:* mocked SDK responses (invented — labeled; an expired-unbilled result takes 24h to produce for real), one of each type in a single batch; assert one upsert, three failures, and that each failure's class names its own type. Bolded: `expired` is unbilled and silently normal at scale, and treating it as an error or as a success both corrupt the corpus's coverage story.
- `submit()` builds one request per item with a `custom_id` that maps back to the item's primary key, and `collect()` restores that mapping. *Evidence:* mocked SDK; assert the round trip over a set of keys including a rootless run id and a slash-turn uuid.
  - *As built:* a `custom_id` may hold `[a-zA-Z0-9_-]` up to 64 characters, and an item key carries pipes and a pair of uuids — so the id is `item_N` and the key travels only in the mapping `submit()` holds. The test asserts the ids against that charset as well as the round trip.
- **Polling is bounded and names what it waited for.** *Evidence:* a mocked SDK that never reports the batch ended; assert the client raises with a deadline message inside the test's own timeout, rather than hanging. Bolded: an unbounded poll against a 24h batch is a job that burns its whole budget and prints no failure line.
- `SyncClient` satisfies the same protocol over the Messages API and returns results in the same shape. *Evidence:* mocked SDK; run the same enricher assertions through both clients and assert the resulting DB rows are equal.
  - *As built:* the equality test lives in `test_batches.py` beside the fake SDK, and compares every enrichment column but `enriched_at`, which is a clock reading. A refusal the SDK raises per request (`APIStatusError`) becomes `api_error` for that item alone, but an `AuthenticationError` or `PermissionDeniedError` propagates: it fails every item identically, and a crash summary listing the whole corpus would bury the cause.

### end-to-end (failure handling)

- **A per-item failure crashes the run at the end with a summary that classifies by kind, names item keys, and contains no model output.** *Evidence:* a fake returning, in one round, an API error, a schema-invalid output, and a secret-bearing output — each carrying a unique sentinel string; assert the raised summary contains the three keys and the three kind labels, and assert every sentinel is absent from it. Bolded: this is the "keys only, never prose" privacy obligation, and a summary built by formatting the failed response is the natural implementation that violates it.
  - *As built:* only the two output-bearing failures carry a sentinel. The API-error item has nowhere to put one — `Failed` holds a key and a kind, which is the mechanism the obligation is asking about.
- A failure writes no row, so the item is still stale, and a rerun with a healthy fake fills it in. *Evidence:* the failing run leaves the key absent from `turn_enrichments`; the rerun submits exactly that key and writes it. This is the whole resume mechanism — assert there is no resume state on disk.
- **A failed child's parents are skipped for that round and are not written.** *Evidence:* `spine/`, with the fake failing leaf run `af6473ae437c9608d`; assert no enrichment row exists for `ac461ef46b4bb8e32`, for the main turn that spawned it, or for the session, while `spine/`'s three other main turns are enriched normally. Bolded: writing a parent whose child failed bakes a hole into a description that the hash will then call current forever.
  - *As built:* **not discharged in slice 2, and not discharged anywhere yet.** A run enriches one level today, so no item has a parent to skip and the mechanism has nothing to be written against. It belongs with slice 3's rounds — implement it there, with this leaf.
  - *Discharged in slice 3* as `test_a_failed_childs_parents_are_skipped`: the failing leaf blocks its parent run and the main turn that spawned it, both of which write nothing, while the session's three other main turns are enriched normally. The session half waits for slice 4.
- Siblings of a failure are still upserted. *Evidence:* the same run; assert the succeeded items' rows exist before the crash.

### opt-in live

- One two-item live check reaches the real API through `SyncClient` and returns schema-conformant output. *Evidence:* `@pytest.mark.slow`, skipped unless an env var opts in **and** `ANTHROPIC_API_KEY` is set; two smallest-render items from the fixture DB; assert both results validate and that `mise run test` with the variable unset makes no network call. Mirrors the pipeline plan's opt-in census pattern. The batch client's own live round trip stays manual — see *Not covered*.
  - *As built:* `AIOBSERVE_LIVE_API` is the opt-in, beside `test_prompts.py`'s `AIOBSERVE_LIVE_STORE`. Written and skipped, never run: the machine that built it held no `ANTHROPIC_API_KEY`, so the leaf is due a first green run — as is the manual batch check — before the clients are trusted.

## Slice 3 — agent runs

### unit (render)

- **A multi-turn run renders each turn's prompt in sequence, with the `<teammate-message>` wrapper unwrapped.** *Evidence:* the extended `teammate/` fixture (run `aarchitect-5144001ac50718bc`, at least turns `d1fb01b4…` and `11b6b551…`); assert both prompts appear in index order and that `<teammate-message` and `teammate_id=` do not. Bolded: this is audit finding B3 — before the fix the model saw the agent's replies but not what it was asked, for the 57 multi-turn runs.
- Each turn prompt is capped at 4K independently, not the sequence as a whole. *Evidence:* the extended `teammate/` fixture at an injected 200-char per-prompt cap; assert both prompts are present and each is truncated.
  - *As built:* the injected cap is 4 characters, not 200 — redaction leaves every recorded prompt at ten characters, so 200 cannot bite. The assertion counts two truncation markers, one per instruction.
- **A zero-turn run renders its api calls alone, labeled as a continuation.** *Evidence:* `fork_byref/`'s run `afa3946951a08a798` — a real member of the store's 41 zero-turn runs; assert the render carries both api calls and the continuation label, and that it does not fabricate an empty task section. Bolded: all 41 are forks whose task lives in another transcript, and a render that assumes a task prompt exists crashes or lies on every one of them.
  - *As built:* the fixture corpus holds a second zero-turn run that is not a fork — `server_tools/`'s `a3b37063695183556`, whose records the fixture trim left without a turn. The corpus's 41 are all forks (re-verified), but the render may not assume it.
- **A replayed turn is not the run's task.** *Evidence:* `fork_origin/`'s fork `a61a059e3610e6fb4`, whose only turn is `replayed` and therefore absent from `live_turns` while auditor `acbc29008a04b9702` keeps the same turn id; assert the fork renders as a continuation and the auditor renders with the prompt. Bolded: renders read the `live_*` views, and a render over the base tables would attribute the parent's task to the copy.
- A run's spawned child runs render as their enriched description, not as their text. *Evidence:* `spine/` with `af6473ae437c9608d` enriched; render `ac461ef46b4bb8e32` and assert the child's description string appears on the `Agent` tool line for `toolu_01SpzL…`.
- A spawning tool line whose run is absent from the store renders as a plain tool line, without crashing. *Evidence:* `spine/`'s `Agent` call `toolu_01Qn8A…`, which really has no matching run row; assert the line exists and carries no description slot.
- A run render over budget elides the middle of its call sequence and keeps head and tail. *Evidence:* `spine/`'s `ac461ef46b4bb8e32` at an injected small budget; same assertions as the turn-level elision leaf. 209 of 2,458 real runs hit the cap.

### end-to-end

- Rounds run in topological order over `parent_agent_id`, leaves first. *Evidence:* `spine/` and `fork_origin/` in one DB; assert from the fake's call log that `af6473ae437c9608d` precedes `ac461ef46b4bb8e32`, and `a61a059e3610e6fb4` precedes nothing but follows `acbc29008a04b9702`, and that all main turns follow all runs.
  - *As built:* the leaf's ordering claim is inverted — `a61a059e3610e6fb4` is `acbc29008a04b9702`'s child, so children-first sends it *before* the auditor, and it is the auditor that follows. Asserted as the set of keys in each batch rather than a sequence: within a round the order is the store's and means nothing. Ordering is over the union parent rule, not `parent_agent_id` alone — see the design's as-built note.
- Rootless runs are roots and are enriched in the first round. *Evidence:* `teammate/`'s `aarchitect-…` (`tool_use_id IS NULL`, `parent_agent_id IS NULL`); assert it appears in round one. The store holds 46 rootless runs and zero of them carry a `parent_agent_id`.
  - *As built:* 55 runs are roots, not 46: the other 9 were spawned by a main-transcript call belonging to no turn.
- A run naming a `parent_agent_id` absent from `agent_runs` crashes, naming the run. *Evidence:* planted (labeled — the store has zero such rows, verified) by deleting `ac461ef46b4bb8e32` from the fixture DB; assert the raise names the orphaned child.
  - *As built:* the crash covers a parent named either way the union rule reads one, so the planted case is a missing parent run rather than a missing `parent_agent_id` specifically. The raise names the orphaned child and the parent it named.
- **The stale set is recomputed after each round's upserts, so a child's new description makes its ancestors stale within the same invocation.** *Evidence:* enrich `spine/` fully; bump the run level's `prompt_version` only, and script the fake to return a *different* description for `af6473ae437c9608d`; run again and assert the fake received `ac461ef46b4bb8e32`, the spawning main turn, and the session in later rounds, and that each of their stored `input_hash` values changed. Bolded: this is audit finding B2, and an implementation that computes the stale set once up front passes every other leaf in this plan while silently never cascading.
  - *As built:* the stated setup cannot show the cascade. A run-level `prompt_version` bump makes *both* spine runs stale up front, so the parent's re-send proves nothing, and a fake that derives its answer from the item key re-describes the leaf identically, so nothing would cascade anyway. Replaced: rename one tool call inside the leaf's own transcript — the only hash-stale item, verified — and script the fake to answer both runs with new text. Asserts the leaf, its parent run and the spawning main turn were sent in that order and that all three stored `input_hash` values moved. The session half waits for slice 4.
- A child re-enriched to identical text stops the cascade. *Evidence:* the same setup with the fake returning the *same* description; assert no ancestor is submitted and no ancestor's `enriched_at` moves. This is the other half of the hash contract and the reason `--dry-run` is an upper bound.
  - *As built:* the same mutation with the fake's default key-derived answers; asserts the leaf is the only key sent, that no turn row changed, and that the parent run's row — `enriched_at` included — was not rewritten.

## Slice 4 — sessions, views, and the cost report

### unit (render)

- A session renders its title, branch, wall and active time, and token and cost totals, then its children in chronological order. *Evidence:* `spine/`; assert the whole rendered string. Note that branch comes from `sessions.git_branch` — `session_rollups` has no such column (verified on `data/traces_v6.duckdb`), so the render joins.
- **A session's children are its main turns plus its rootless runs, each once.** *Evidence:* `teammate/`, which has exactly one of each — main turn `97d6f3d4…` and rootless run `aarchitect-…`; assert both descriptions appear, and separately assert on `spine/` that `ac461ef46b4bb8e32` (spawned by a main turn) does **not** appear directly, reaching the session through its turn instead. Bolded: audit finding B1 — the depth-1 reading dropped all 43 recorded teammate agents from every session summary and double-embedded 10 others.
- A session with more children than the budget allows elides the middle. *Evidence:* `spine/`'s four children at an injected budget of two; assert head, tail, and marker. Real sessions average 2.5 children and reach 92.
- A `Workflow` tool line embeds its spawned run's description, like `Agent`. *Evidence:* `workflow/`; assert the description appears on the `toolu_0171Lz…` line.

### integration (store)

- `enriched_agent_runs` and `enriched_sessions` LEFT-join like `enriched_turns`, so coverage reads honestly. *Evidence:* enrich one run of two; assert both rows return, one with NULL enrichment.
- A session with no main turns and no runs is never enriched. *Evidence:* `compaction/` and `dup_uuid/` in the DB; assert `session_enrichments` holds no row for either, and that the enricher reports them as skipped rather than failed. 102 of 575 real sessions are in this state, so coverage is 473.
- Zombie sweeps run at all three levels. *Evidence:* delete a base row of each kind; assert all three enrichment rows go.

### end-to-end

- **`--dry-run` counts hash-stale items plus all their ancestors, and says the number is an upper bound.** *Evidence:* enrich `spine/` fully, make only leaf run `af6473ae437c9608d` hash-stale, and assert `--dry-run` reports four items (the leaf, its parent run, the spawning main turn, the session) while the subsequent real run may write fewer. Bolded: a dry run that reports one item quotes a price for a cascade it cannot see.
- The cost estimate is arithmetic over rendered character counts and the code-resident price table, with no network and no live lookup. *Evidence:* a fixture DB whose rendered sizes the test computes itself; assert the printed estimate equals the hand-computed product of those sizes, the chars-per-token constant, and the table's rates, and assert the run makes no API call.
- `cli.main("enrich", …)` drives the same path as `enrich()` and produces the same DB. *Evidence:* invoke in-process (no subprocess, no timeout to bound); compare every enrichment table against a direct `enrich()` run.
- `--no-batch` selects `SyncClient` and `--dry-run` works under both. *Evidence:* assert the client class the CLI constructed, through the same protocol seam.

## Not covered, and why

- **A live `AnthropicBatchClient` round trip.** A batch is retrievable only when the whole batch ends, worst case 24 hours. No deadline a test suite can carry covers that, and `.claude/rules/testing.md` forbids an unbounded wait. It stays a two-item manual check per client release, recorded in the PR — the design says so, and the opt-in live leaf above covers the synchronous path instead
- **Whether the cost table matches Anthropic's published prices.** No seam reaches it; the table is a constant, and a test asserting a constant against itself proves nothing. It needs the same dated out-of-band check the pipeline plan's pricing table needs
- **Description quality.** Whether a description is a good description is a judgment, not an assertion. The taxonomy's health is a corpus query (`GROUP BY category`, watching the `other` share), which belongs in a phase-3 report, not in the suite
- **The secret-shape pattern list's completeness.** The leaves prove the screen fires on the shapes it knows. That the list covers what a real transcript might hold is a heuristic the design already books as an open question
- **Prompt caching behavior.** The design counts cache benefit at zero and no code depends on a hit
- **Concurrent `enrich` runs, and `enrich` racing `extract`.** No design contract says what they do, so there is nothing to hold an implementation to
- **Batch rate limits and retry-after handling.** Not in the design's failure model; if the client grows one, it brings its own leaves

## Obligations the seams can't reach

Each is a real obligation the design creates, reported rather than dropped or demoted.

1. **Truncation and elision are not provable at the design's stated budgets.** Every fixture string outside the structural keep-list is redacted to `[redacted]`, so no fixture row comes within two orders of magnitude of the 30K cap, and the design's stated check — "`test_prompts.py` asserts budgets are never exceeded on real fixture rows" — is vacuous as written. Two changes discharge it: make each render take its budget as a **parameter** defaulting to the constant in `prompts.py`, so the elision leaves run on real rows at a small budget; and add an opt-in, env-gated slow test that renders the live store and asserts no prompt exceeds its real budget. The second reads private data, so it is gated like the pipeline plan's census. This is a small seam change, not a redesign — but the leaves above assume it.

2. **Content-exclusion claims need a sentinel, not a fixture.** For the same redaction reason, "the render excluded `thinking`" and "the render included `thinking`" produce identical characters on every recorded fixture. The leaves plant a sentinel into one field of a real row. That proves the exclusion for the field planted; it cannot prove that no *other* field leaks. A render whose output is asserted whole (the first leaf of slice 1) is the partial answer, and the reason that leaf spells out the expected string rather than checking substrings.

3. **The multi-turn fixture does not yet exist and the plan depends on it.** `teammate/` today holds one of the recorded run's four teammate turns. The B3 obligation — the audit's central render fix — is undischargeable until the fixture is extended. This is fixture work, not a design gap, but it blocks slice 3 and should be done first in that slice.
   - *As built:* done first in slice 3 — `teammate/` now carries both `d1fb01b4…` and `11b6b551…`, redacted from the same recorded session, and the B3 leaf asserts the whole rendered string.

4. **"No test calls the real API" is a convention, not a control.** Nothing in the design prevents an implementer from constructing `AnthropicBatchClient` inside a test. Recommended discharge: an autouse fixture in `tests/enrich/conftest.py` that makes the SDK's transport raise unless the live marker is present, so an accidental live call fails loudly instead of billing quietly. Without it, the guarantee rests on review.

5. **The per-round-recompute leaf is the only thing standing between the design and a silent cascade failure**, and it is a behavioral assertion over a fake's call log — not a structural one. An implementation could satisfy it by special-casing while still planning rounds up front in some other path. This is inherent: "recomputed each round" is a statement about *when* a query runs, and only its observable consequence is testable. Bolded in the plan, and worth an implementation comment naming the leaf.
