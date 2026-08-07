# Design: AI enrichment

Add model-written descriptions and categories to the trace store, so the phase-3 analysis agents can find interesting work without reading raw transcripts and the phase-4 viewer can label a session at a glance.

Designed against the canonical store `data/traces.duckdb` (2026-08-07, schema 7, every session at one extractor version): 575 sessions, 1,409 live main turns, 2,535 subagent turns, 2,458 agent runs, 154K tool calls. Every count and size below comes from a query stated inline; re-run them before trusting a number, the store grows daily.

## Problem

The store holds full text but no summaries: answering "what was this session doing?" means reading megabytes. Three constraints decide the shape:

- **Context is a cost, squared.** Each enrichment prompt must carry only what its level needs — tool results alone are 390 MB corpus-wide (`select sum(len(result)) from corpus_tool_calls`); feeding them raw would dominate cost and drown the signal
- Enrichment rows must survive re-extraction: they attach by the pipeline's natural keys and live in tables the per-session replace never touches (`plans/trace-pipeline/design.md`)
- Descriptions are derived from private transcripts. They go to the Anthropic API and into the gitignored DB. The prompt instructs the model to describe, never quote — but an instruction is not a control, so validation also runs a secret-shape screen over every output (`sk-`, `AKIA`, PEM headers, and kin) that fails the item, descriptions stay under the same read-before-pasting rule as transcript text, and the failure summary prints item keys only, never model output

## Levels and what each prompt is built from

The corpus decides the levels. An agent run averages 1.031 turns (41 runs have 0, 2,360 have 1, 57 have more, max 16 — `select count(tu.id) t from agent_runs a left join live_turns tu on tu.session_id=a.session_id and tu.source=a.id group by a.session_id, a.id`), so the **run**, not the subagent turn, is the unit for delegated work. Three levels, enriched bottom-up so each higher prompt carries child descriptions instead of child text:

| Level | Rows | Prompt content | Budget |
| --- | --- | --- | --- |
| agent run | 2,458 | each of its turns' prompts in sequence, ≤4K chars each (turn 1 is the task; later turns are incoming teammate instructions — `<teammate-message>` XML unwrapped like slash-command tags); per api call: `text` ≤1.5K chars, one line per tool call (name, input head ≤120 chars, input and result sizes, error flag, error result tail ≤300); spawned child runs render as their enriched description. A zero-turn run (41: fork continuations with no local prompt) renders its api calls alone, labeled as a continuation | 30K chars |
| main turn | 1,409 | `Turn.prompt` ≤4K (slash turns render `command_name` + `command_args`, not the raw tag XML — the `prompt` column retains tags); same per-call render; `Agent`/`Workflow` tool lines embed the spawned run's description | 30K chars |
| session | 473 (see skip rule) | title, wall/active time, token and cost totals from `session_rollups`, joined to `sessions` for `git_branch` (the rollup view carries no branch column); the chronological list of child enrichments (description, category, outcome). Children are main turns plus **rootless runs** — `tool_use_id IS NULL` (46: 43 recorded teammate agents at `spawn_depth = 0`, 3 orphans), which no turn's tool line embeds. Runs spawned from a main turn reach the session through that turn's description; runs spawned by another agent reach it through their parent run | 24K chars |

Over budget, elide the middle of the call sequence and keep head and tail with an elision marker (209 of 2,458 runs hit the cap). Sessions average 2.5 children, max 92 (`select avg(c), max(c) from (select s.id, main_turns + rootless_runs c ...)`) — same head-and-tail rule. `thinking` is excluded everywhere. A session with no main turns and no runs at all — 102 of 575 — is skipped, not enriched: there is nothing to describe. Coverage is therefore 473 sessions.

Each item's output, via a forced tool call with a JSON schema: `description` (1–2 sentences), `category` (taxonomy member), `outcome` (`completed | partial | failed | abandoned | unclear`), `friction` (one line naming visible struggle — retries, errors, backtracking — or null). Validated client-side; an out-of-vocabulary value fails the item, never widens the vocabulary.

## Taxonomy

A closed `StrEnum` in `enrich/taxonomy.py`, one shared vocabulary across all three levels, each member with a one-line definition comment: `design`, `implement`, `fix_bug`, `refactor`, `test`, `debug`, `review`, `analyze`, `document`, `configure`, `vcs_ops`, `explore`, `chat`, `other`. `other` is the escape valve; a growing `other` share is the signal to revise. `TAXONOMY_VERSION` (int) lives beside it. Rows record the version they were written under, so a bump makes old rows stale without invalidating them mid-migration — the viewer can render version-N rows while version-N+1 backfills.

## Staleness and idempotency

A row is current when `(input_hash, prompt_version, taxonomy_version, model)` all match the values the enricher would use now; anything else is stale and gets re-enriched, insert-or-replace by primary key.

- `input_hash` = sha256 of the rendered prompt content (not the instruction template). Re-extraction that changes no text re-buys nothing; a child's new description changes the parent's rendered input, so cascade re-enrichment falls out of the hash — **provided the stale set is recomputed after each round's upserts**. Before a child's round runs, its parent renders from the old description and hashes current; an implementation that plans all rounds up front silently breaks the cascade. `enrich()` therefore re-renders and re-hashes each level only when its round starts
- Because cascade staleness is unknowable up front, `--dry-run` counts hash-stale items **plus all their ancestors** and reports the total as an upper bound — a child re-enriched to identical text stops the cascade and costs less than quoted
- `prompt_version` is a per-level int in `enrich/prompts.py` — covers what the hash cannot see: system-prompt/instruction changes and changes to the output tool's JSON schema
- `model` in the staleness tuple means `--model` switch re-enriches automatically
- Each run starts by sweeping zombie rows — enrichments whose base row vanished (an extractor bump can redraw turn boundaries): `DELETE FROM turn_enrichments WHERE NOT EXISTS (matching live turn)`, likewise per level. Without the sweep the LEFT-join views hide the drift

Rendering the whole corpus to compute hashes is a local DB read, cheap by construction. (This scheme supersedes the pipeline design's line "`extract_state.extracted_at` tells the enricher what is stale" — the hash is strictly better, and timestamps are not consulted.)

## Model and API mechanics

`claude-haiku-4-5-20251001` by default, `--model` to override. Production calls go through the **Message Batches API** (50% discount; latency is fine for a backfill-and-nightly tool — most batches finish under an hour, worst case 24h per round). One invocation runs sequential rounds: agent runs in topological order over `parent_agent_id` (leaves first, so parents can embed child descriptions; the 46 rootless runs are roots — the store has zero `parent_agent_id` values naming a missing run, and a new one would crash), then main turns, then sessions — bounded by max spawn depth 5, so ≤8 rounds. For prompt iteration and `--limit`-sized dev runs, `--no-batch` selects `SyncClient`, a synchronous Messages-API implementation of the same `BatchClient` protocol — full price, minutes not hours.

Failure handling, fail-fast without losing the batch: every succeeded item is upserted as results stream in; a failed item (API error, schema-invalid or secret-bearing output, or a request the Batches API **expired unbilled** at its 24h limit) writes nothing, and its parents are skipped that round. At the end the run **crashes** with a summary classifying failures by kind and naming item keys — never model output. Because failures wrote no rows, they are still stale — rerunning `aiobserve enrich` is the retry, with no separate resume state.

`ANTHROPIC_API_KEY` is loaded from `.env`/environment, validated non-empty at command start, never printed.

## Cost

Truncation-aware render sizes, from the store (queries in `enrich/prompts.py` mirror these; per tool call the line costs `30 + least(len(input),120) + 300×is_error` chars):

- Runs: `sum(least(Σ_turns least(len(prompt),4000) + Σ_calls least(len(text),1500) + Σ tool_line, 30000))` over `agent_runs` → **33.5M chars** (avg 13.6K)
- Main turns: same shape with one prompt per turn, over `live_turns` where `source='main'` → **5.8M chars** (avg 4.1K)
- Sessions: 473 × (metrics + avg 2.5 children × ~300 chars) → **~0.7M chars**

Items: 2,458 + 1,409 + 473 = 4,340. At 3.3–4 chars/token (4 is a floor for code-heavy text, not a ceiling): 10–12.1M content tokens + ~3.0M instruction overhead (~700 tokens × item) ≈ 13–15.1M input, ~0.9M output (200/item). Haiku 4.5 batch rates ($0.50 in / $2.50 out per MTok): **≈ $9–12 per full-corpus pass** (~$18–24 unbatched). Not an upper bound except with respect to caching, which the estimate counts at zero. A taxonomy or prompt bump re-buys its level(s); the hash keeps everything else free. `--dry-run` prints stale counts and this estimate before spending.

## Schema

```sql
CREATE TABLE turn_enrichments (
  session_id VARCHAR NOT NULL, source VARCHAR NOT NULL, turn_id VARCHAR NOT NULL,
  description VARCHAR NOT NULL, category VARCHAR NOT NULL, outcome VARCHAR NOT NULL,
  friction VARCHAR,                     -- one line of visible struggle, NULL when none
  input_hash VARCHAR NOT NULL, prompt_version INTEGER NOT NULL,
  taxonomy_version INTEGER NOT NULL, model VARCHAR NOT NULL,
  enriched_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (session_id, source, turn_id));
-- agent_run_enrichments: same columns, PRIMARY KEY (session_id, agent_run_id)
-- session_enrichments:   same columns, PRIMARY KEY (session_id)
```

Consumers query three views: `enriched_turns` (`live_turns` ⋈ `turn_enrichments`), `enriched_agent_runs` (`agent_runs` ⋈ `agent_run_enrichments`), `enriched_sessions` (`session_rollups` ⋈ `session_enrichments`) — LEFT joins, so un-enriched rows appear with NULL enrichment and the UI can show coverage honestly.

## File-tree diff

```
src/aiobserve/enrich/
  __init__.py      NEW
  taxonomy.py      NEW  Category/Outcome StrEnums, TAXONOMY_VERSION
  prompts.py       NEW  per-level render + input_hash, PROMPT_VERSION per level; budgets are render parameters, defaults defined here
  store.py         NEW  enrichment DDL + views, staleness query, upsert (pipeline never touches these tables)
  batches.py       NEW  BatchClient protocol + AnthropicBatchClient (submit/poll/collect) + SyncClient (dev)
  enricher.py      NEW  enrich(): rounds, per-round staleness, zombie sweep, skip-parents-of-failures, crash summary
src/aiobserve/cli.py  CHANGED  `enrich [--db] [--project] [--model] [--dry-run] [--limit] [--no-batch]`
docs/enrichment.md    NEW  taxonomy meanings + staleness model (schema.md stays telemetry-only)
CLAUDE.md             CHANGED  Layout entry for docs/enrichment.md (doc-sync will enforce)
pyproject.toml        CHANGED  deps: anthropic, python-dotenv
tests/enrich/         NEW  test_prompts.py, test_store.py, test_enricher.py
```

## Chosen test seam

`BatchClient` is the seam: `submit(requests) -> results`, one protocol method the fake implements. `test_enricher.py` builds a real DB by running the existing pipeline over the extractor fixtures, then drives `enrich()` end to end with a fake client — asserting rounds order, upserts, cascade staleness, and the failure-summary crash via SQL. Fixture rows are fully redacted, so cap and exclusion behavior is invisible on them at real sizes: render budgets are parameters of the render functions (defaults in `prompts.py`), and `test_prompts.py` proves elision with a small injected budget and proves exclusions (thinking, tool results) by planting a labeled sentinel in one field of a copied real row; an env-gated slow test runs the default budget over the live store. The real `AnthropicBatchClient` is tested against mocked SDK responses; one manual 2-item live check per release of the client, not in CI.

## Slices

1. **Seam + spine, turn level.** Taxonomy, store DDL + `enriched_turns`, turn render + hash + staleness + zombie sweep, `enrich()` over the fake client, secret-shape screen, CLI with `--dry-run`. Verified by `test_enricher.py` enriching fixture-built turns end to end, plus a second-run-is-a-no-op test.
2. **Real clients.** `AnthropicBatchClient` submit/poll/collect with all four result types (succeeded, errored, canceled, expired), `SyncClient`, key validation, per-item failure → classified crash summary → rerun resumes. Verified by mocked-SDK tests; manual live check noted in the PR.
3. **Agent runs.** Topological rounds with per-round staleness recompute, child-description embedding into run and turn prompts, rootless runs as roots, multi-turn renders (teammate-tag unwrap) and zero-turn continuations, cap elision. Verified on a subagent fixture session including a multi-turn run.
4. **Sessions + docs.** Session render from rollups + spawn-linkage children, skip rule, remaining views, `docs/enrichment.md` + the CLAUDE.md Layout line, full `--dry-run` cost report with ancestor counting. Verified by view SQL tests.

## Decisions

- **Run, not subagent turn, as the delegated-work unit** — runs average 1.031 turns; per-turn rows inside runs would nearly duplicate run rows, and the run render carries every turn's prompt so multi-turn teammate exchanges lose nothing. Rejected: enriching all 3,944 turns (2,535 add almost no information over their run).
- **Session children partitioned by spawn linkage, not spawn depth** — children are main turns plus rootless runs (`tool_use_id IS NULL`); everything else reaches the session through its embedding parent. Rejected: depth-1 runs as children (drops all 43 recorded teammate agents and their subtrees from every session summary, and double-embeds the 10 depth-1 runs that have a `parent_agent_id`).
- **Bottom-up hierarchy: parents read child descriptions, never child text** — the context-is-a-cost constraint applied to our own prompts. Rejected: flat per-level prompts over raw text (session prompts would carry megabytes or lossy samples).
- **Closed taxonomy in code, versioned** — `GROUP BY category` must work; code is PR-reviewed truth. Rejected: open vocabulary (fragments into synonyms); DB-stored taxonomy (drifts from the code that validates against it).
- **Staleness by input hash, not `extracted_at` comparison** — a re-extract that changes no text costs nothing, and cascade invalidation falls out free. Rejected: timestamp comparison (re-buys the corpus on every extractor version bump).
- **Batches for production, `SyncClient` for dev — both behind `BatchClient`** — half price where volume lives, and prompt iteration is not stalled by up-to-24h rounds (a run-level prompt bump cascades through up to 8 serial rounds). Rejected: batches-only (phase-3 prompt iteration at batch latency); a production streaming path (pays double forever for latency nothing needs).
- **Upsert successes, crash at the end naming failures** — house fail-fast without losing 3,000 sibling items; staleness is the resume mechanism. Rejected: fail on first error (loses the batch); silent partial success (a quietly under-enriched corpus).
- **Exclude `thinking` from prompts** — 30.5 MB corpus-wide vs 22.3 MB of text, and the visible actions carry the story; friction shows in retries and errors. Rejected: including it (≈2x turn/run cost for marginal description quality). Revisit if phase 3 finds friction under-detected.
- **Exclude tool result content except error tails, but carry both input and result sizes** — results are 390 MB, yet result length is the one-number signal behind context-bloat findings; sizes cost ~10 chars a line. Rejected: including result heads (budget-dominating, mostly file dumps); omitting result size (descriptions could never say "a 2 MB dump flooded the window").
- **Include a ≤120-char tool input head** — it names the file read, the command run, the URL fetched; without it, "docs read unneeded", "confusing commands", and "layout confusion" findings lean entirely on assistant narration and error tails. Costs roughly a dollar a pass. Rejected: name-and-size-only lines (the model describes *that* a Read happened but not *of what*).
- **`friction` as a fourth output field** — one nullable column that lets phase-3 agents filter to struggling turns without reading text. Rejected: leaving friction analysis wholly to phase 3 (it would re-read raw text at Sonnet prices to find candidates).
- **`enrich` as a separate command, no `extract --enrich`** — extract stays key-free and offline; an enrichment failure cannot taint extraction. Rejected: auto-chaining.
- **Enrichment schema documented in `docs/enrichment.md`, not `schema.md`** — schema.md documents fields Claude Code owns and can break; enrichment fields are ours. Small call, flagged for the audit.

## Out of scope

- **Tool-call-level enrichment** (154K rows) — phase 3 queries `is_error`, names, and durations directly; descriptions there would cost more than they save
- **Prompt caching in batches** — the shared instruction prefix may cache (batch + cache discounts stack) but hit rates are not guaranteed; the estimate counts zero cache benefit, so it is an upper bound
- **Embedding/semantic search over descriptions** — phase 3 may want it; nothing here blocks adding a vector column later
- **Automatic re-enrichment scheduling** — `enrich` is run by hand or by whatever runs `extract`; no daemon
- **Enriching `raw_records`-only content** (journals, queue operations) — archived, not summarized

## Open questions

- Description length/style for the viewer (one line vs two sentences) is guessed at 1–2 sentences; phase 4 mockups should settle it before a corpus-wide re-buy (a deliberate `prompt_version` bump, ~$10)
- Whether `effort` (carried opaque from phase 1) should feed the run prompt as a hint — deferred until its semantics are established in `docs/schema.md`
- The secret-shape pattern list is a heuristic, not a guarantee — is the read-before-pasting rule on descriptions enough for phase 3's committed reports, or should phase 3 add its own screen at report time?
