# Design: the mycelia analysis process

A repeatable process that turns the trace store into findings about how AI coding agents perform on mycelia, and concrete repo tweaks — per-session and per-run reports rolled up into a committed top-level report, then a shortcoming review, then a better next run.

Designed against the canonical store `data/traces.duckdb` (probed 2026-08-07, schema 7): 575 sessions 2026-06-24 → 2026-08-07 (568 at `/Users/nob/repos/mycelia`, 3 in its `.claude/worktrees/`, 4 with NULL `project_dir` and no timestamps), $15,069 total priced cost, per-session cost p50 $0.19 / p90 $40 / p99 $641. Every number here names its query; re-run before trusting one — the store grows daily.

## Problem

The store answers questions but nobody has a standing way to ask them. Three constraints decide the shape:

- **A claim carries its query** (CLAUDE.md). Ad-hoc SQL typed into a chat produces findings nobody can re-run; the queries must be versioned artifacts a report cites
- **575 sessions cannot all be read**, and reading one must not load a transcript into context — the store's `raw_records` rows for one session run to megabytes, and transcripts are private
- **No API key.** Enrichment (`plans/enrichment/design.md`) would accelerate selection and triage, but the process must produce a full report without it. Careful reading is done by Claude Code subagents the manager dispatches, not by the enrichment pipeline

## The process, one iteration

Current state: nothing — `reports/` holds only its README. Proposed flow, run by the manager:

1. **Refresh and stamp.** `aiobserve extract` brings the store current. The iteration records its **corpus stamp**: session count, `max(started_at)`, `meta.schema_version`, distinct `extract_state.extractor_version`s. Every artifact of the iteration cites the stamp; that is what "the data you looked at" means when the store grows daily
2. **Broad counts and clusters.** Run the query library (below); outputs land in `data/analysis/<YYYY_MM_DD>/counts/` as CSVs
3. **Select sessions and runs** for careful reading with the selection query (below) — deterministic, citable, ~30 sessions and ~20 extra runs per iteration
4. **Careful reading.** One reader subagent per selected session writes a structured per-session report (and per-run reports for its flagged runs) into `data/analysis/<YYYY_MM_DD>/sessions/` and `runs/`, using only the bounded digest queries — never a whole transcript
5. **Synthesis.** A high-effort pass loads the per-session reports (they are small by contract), the count tables, and the cluster output; promotes candidates to findings under the evidence ladder (below); writes the committed report `reports/YYYY_MM_DD_mycelia_<topic>.md` per `reports/README.md`
6. **Process review.** The report's final section reviews the process itself; fixes to queries, templates, and `docs/analysis.md` land in the same PR as the report

## File-tree diff

```
src/aiobserve/analyze/
  __init__.py
  queries/*.sql              the query library, one statement per file
  templates/session.md       per-session report template (front matter + capped body)
  templates/run.md           per-run report template
src/aiobserve/cli.py         + `aiobserve query` subcommand
docs/analysis.md             the process guide readers and the manager follow; linked from the CLAUDE.md Layout tree
reports/YYYY_MM_DD_mycelia_<topic>.md   one per iteration (committed, human-reviewed)
data/analysis/<YYYY_MM_DD>/  gitignored working papers: counts/, sessions/, runs/, stamp.txt
```

## Key contracts

**Query runner.** `aiobserve query <name> --project <path> [--since DATE] [--param k=v] [--csv]` — opens the store read-only, loads `analyze/queries/<name>.sql`, binds DuckDB named parameters, prints a table or CSV. The runner's per-query manifest gives each parameter either a production default or marks it **required with no default** — the house rule for a choice the caller must actively make: `records_slice`'s session, source, and line range have none, because a defaulted line range would quietly hand back the raw-text window the cap exists to stop. A bare `aiobserve query select_sessions` runs the design's values; a bare `records_slice` refuses to run, naming its unbound parameters. The manifest also carries a `scope: corpus | keyed` field, adopted from the trace-viewer design (`plans/trace-viewer/design.md`), which shares this library: `corpus` queries take the required `--project` and corpus predicate as above; `keyed` queries — fetches by `session_id`/`source`, such as `records_slice` and the digests — are exempt from both, since a corpus predicate on `WHERE session_id = $session_id` is noise. Build the manifest with the field from the start. With each result the runner emits the **citation** — query file name plus the resolved bindings — as a header of table output and on stderr under `--csv`; that line is what a report copies to meet the claim-carries-its-query bar. No query file reads the clock (`current_date`/`now()`): anything time-relative rides a bound `$as_of`, because a clock-reading query goes green on a frozen fixture store today and returns nothing next month. `--project` is required, resolved and stripped of any trailing slash before matching: the corpus predicate is `project_dir = $project OR starts_with(project_dir, $project || '/')` (the `/`-suffix picks up the 3 worktree sessions). The runner prints how many sessions the predicate excluded — to **stderr**, so piped `--csv` stays clean. Today that is the 4 NULL-`project_dir` rows: zero-cost bookkeeping stubs of 1–2 records (`bridge-session`, `ai-title`, `agent-name`), so their exclusion distorts nothing. `--since` omitted means full corpus. DB path and flag conventions follow the existing `cli.py` commands — verify at implementation, don't trust this line.

**Query files.** One `.sql` per question, commented with what it answers and which view family it reads. All cross-session sums read `corpus_*` views; per-session facts read `session_rollups`/`live_*` (the docstring of `export/duckdb.py` owns this rule). Cost queries always select `unpriced_api_calls` beside `cost_usd`. The initial library, by analysis style:

- *Broad counts*: tool use and failure rate per tool name (`live_tool_calls.is_error`; today Bash 1,779/86,130 errors, Edit 895/23,215); skill activity (`api_calls.attribution_skill` — manager alone spans 47 sessions/6,289 calls — plus `tool_calls.name='Skill'` inputs for invocation counts); slash commands (`turns.command_name`; `/compact` 93, `/manager` 68); agent-type usage (`agent_runs.agent_type`; general-purpose 1,012, implementer 425, auditor 227); token/cost distributions from `corpus_rollups`; weekly trend variants of each (ISO-week `date_trunc`)
- *Clusters*: pairwise co-occurrence of tools, skills, and agent types within a session (self-join on session-level incidence); a rule-based **session-shape classifier** — a `CASE` over rollup columns naming shapes like manager-orchestrated, delegation-heavy, solo-editing, read-only-analysis — so a shape is a citable predicate, not a vibe
- *Reading support*: `session_digest` (turn list with prompts truncated to 300 chars, per-turn call/error/cost aggregates, plus one **unattributed** row for api calls with NULL `turn_id` — 1,229 rows carrying $249 corpus-wide, which would otherwise make the digest's cost silently disagree with the front matter), `run_digest` (same at `source = agent_id`), `records_slice` (raw records for one `(session_id, source)` bounded by a **mandatory** line range and `substr(raw, 1, 2000)`)
- *Selection*: `select_sessions`, `select_runs` (below)

**As built,** every broad count carries a `period` column and returns its corpus row and its trailing-window row from one pass, off a runner-built `session_period` view — rather than shipping a weekly variant of each. `weekly_trend` remains the one ISO-week query; a per-count weekly variant is a query to add when an iteration wants a trend of that count, not five files to keep in step from the start. Two shapes of the same count are two chances for the window to drift from the total it restricts, which is the same reason the window lives in the runner and not in each file.

**As built,** the classifier names `skill-orchestrated` where this section says `manager-orchestrated`: any one skill carrying at least `$skill_share_pct` of a session's api calls. `manager` is one project's skill, and `CLAUDE.md` forbids assuming mycelia's conventions. Every cut point is a bound parameter — at the defaults on the 2026-08-07 store, `conversational` takes 338 of 571 sessions, so the first process review should expect to move them.

**As built,** `skill_activity` reports invocations and attributed calls as separate columns of one row, and they disagree sharply: `manager` shows 6,289 attributed calls and zero invocations, because a skill reached through a slash command invokes no `Skill` tool call. Reading either column as "how much this skill was used" would be wrong on its own. `docs/schema.md` records the `Skill` input shape the invocation half depends on; no fixture holds one, so that half is exercised only against the real store.

**As built,** the runner owns `--as-of` and `--since` rather than the manifest: they apply to
every corpus query, and the citation reports them beside the query's own bindings. It
materializes the corpus predicate and the window into a TEMP TABLE `project_sessions
(session_id, started_at, in_window)` and every corpus query joins it, so the `/`-suffix trap
is written once. The excluded count it prints is the store's NULL-`project_dir` sessions.

**As built,** `session_digest` covers the main thread and totals it — a digest that lists one
scope and advertises another's total is a number no reader can reconcile — so an agent run's
cost reaches a reader through `run_digest`, which is the same query at a bound `$source`. The
unattributed row carries the turn id `(unattributed)`. `records_slice`'s cap is a parameter,
`$max_chars`, defaulting to 2,000: the number is then in the citation, which is the honest
form of a bound that was always convention.

**Recency rule.** Fixed dual window, not decay weights: every count is reported for the full corpus and for the trailing **28 days** measured back from the bound `$as_of` (263 sessions and $11,689 at as-of 2026-08-07, so the window carries plenty of signal), and trend queries bucket by ISO week. The runner defaults `$as_of` to today and the corpus stamp records it, so a report's window is a pair of dates anyone can rebind. When a finding tests a specific mycelia guidance change, split at that commit's date and name the commit — and name what else moved in the window, per the correlation rule.

**As built,** `session_counts` returns both windows as two rows of one result, off one pass
over `project_sessions`, so the window cannot drift from the total it restricts.
`weekly_trend` buckets the same rows by ISO week and sends a session with no `started_at` to
a bucket named `undated`: the weeks have to sum to the corpus total, and a NULL bucket
swallows sessions where nobody looks.

**Selection.** `select_sessions.sql` is deterministic and stratified over the 28-day window, ranking on `corpus_rollups` (a resume duplicate is valued at its deduped work) and excluding sessions with zero turns and zero runs — 82 of the window's 263 sessions today, a 31% cut of the pool, fine for choosing what to read but one more reason no absence claim may rest on this set (see the evidence ladder). Strata fill **in order**, each walking down its own ranking (`ORDER BY metric DESC, session_id` — the id tiebreaker is what makes "deterministic" true) and taking sessions *not already selected* until its quota is met. Quotas and the skill threshold are **bound parameters with these production defaults** — so a 16-session fixture store can exercise the mechanism at small values, and iteration 1's budget reset is a parameter change, not a query edit: 8 by `cost_usd`, 5 by tool-error count, 4 by `compactions`, one per **major skill** — an `attribution_skill` used in ≥5 in-window sessions (6 of the window's 19 today), iterated in skill-name order, taking each skill's most recent unselected user — then a seeded 8 from the remainder (`ORDER BY hash(session_id || $seed)`) for **discovery**: surfacing friction the ranked strata would never pick, and bounding nothing. The backfill walk is load-bearing: in this window the top-8-cost, top-5-error and top-4-compaction sessions collapse to 8 distinct sessions, so without it the read set would silently shrink to the same few monsters. Two run-out rules keep the tags honest: a ranked stratum takes only sessions with a **nonzero** metric — a `tool-errors` tag on an error-free session would lie — and stops short when the metric runs out; a major skill whose every pool user an earlier stratum already took contributes nothing. Unused ranked slots pass to the discovery quota, which draws from the whole remaining pool. The realized set is therefore **min(quota sum, pool size) — 31 sessions at defaults while the window's pool holds that many; what varies is the composition, not the count** — and each session carries the tag of the stratum that took it, so the report states the realized composition, not the target. `select_runs.sql` adds runs beyond the selected sessions: highest error counts and highest cost per `agent_type`, same tiebreaker, so every commonly used agent definition gets read each iteration.

**As built,** "commonly used" is a bound floor, `$min_runs`, defaulting to 5 in-window runs. Without one the draw returned 75 runs across 59 `agent_type`s at as-of 2026-08-07: the set is open (`docs/schema.md`), and a session that names its own subagents — `audit-pr275`, `impl-cards` — wins a reading slot per invented name. At the default the draw is 28 runs across 14 definitions, which is the budget this paragraph sized.

**Per-session report.** Markdown, YAML front matter + body capped at 60 lines. Front matter: `session_id`, `iteration`, `stratum`, `extract_fingerprint`, `template_version`, and the digest's headline numbers (cost, turns, tool calls, errors, compactions, skills, commands). Body sections: **Narrative** (≤5 bullets), **Friction** (each item: one line, a category tag, and an evidence ref `(source, line_no–line_no)`), **Improvement candidates** (tagged from the closed vocabulary below), **Context waste** (what was loaded that the work didn't need), **Not examined**. The category vocabulary is the dispatch's eight plus one, as slugs in the template: `confusing-tool`, `failing-tool`, `doc-read-unneeded`, `doc-missed`, `workflow-mismatch`, `layout-confusion`, `lintable-mistake`, `bloated-tool-output`, `unneeded-context`, with `other` as the escape valve. Per-run reports are the same shape capped at 30 lines, keyed `(session_id, agent_id)`. The `extract_fingerprint` stamp lets a later iteration skip re-reading a session whose extraction and template are unchanged.

**Reader protocol.** A reader's brief is bounded: session id, stratum, the template path, and a pointer to `docs/analysis.md` — no content pasted in. Readers query through `aiobserve query` digests only; `records_slice` is the sole route to raw text, and its caps are the context and privacy control. The caps are **convention, not mechanism** — a reader has Bash and could open the store directly — so the mitigations are the bounded brief and the process-review checklist, which asks per reader whether it stayed inside the digests and what context it spent. Per-session reports live in gitignored `data/` because a reader summarizing private transcript text will sometimes carry some of it; only the synthesized top-level report is committed, under the quoting contract below.

**Evidence ladder.** Synthesis promotes a candidate to a finding at one of three stated confidence levels: **counted** — a corpus query corroborates it (the query and window go in the report); **recurring** — ≥3 independent session reports show it and no query can count it; **anecdote** — reported only as a hypothesis with its one session named. **An absence is only ever counted.** "No session did X" must come from a corpus-wide query whose filter demonstrably could have matched X — never from the read sample: zero sightings in ~30 read sessions bounds prevalence only at roughly one in three (rule of three at n=8 for the random stratum), over a pool that already excluded 31% of the window. A reader who notices an absence files it as a hypothesis for synthesis to count or drop. The mirror claims ride the same rule: "every read session did Y", or an absence recurring across ≥3 reports, is still a statement about the biased 31-session sample — it reaches a finding only restated as a corpus-wide count, or explicitly labeled sample-only. Every recommendation ties to a finding and is scoped to this corpus: one person's sessions on one codebase.

**Committed-report quoting.** Any transcript quote in a committed report carries its citation `(session_id, source, line_no–line_no)` and passes a rule-based redaction: strip file paths outside the analyzed repo, personal names, and anything secret-shaped (tokens, env values, PEM headers). Synthesis checks that every quote has its citation; Nathaniel's PR review checks each quoted line against the redaction rule — the citation is what makes that check mechanical rather than a vibe.

**Iteration and versioning.** A new iteration is a new dated report; old reports stay as the version history (the README's naming already dates them). The process-review section answers a fixed checklist: which strata produced findings and which produced none; which template fields went unfilled or were always `other`; which candidates failed corroboration and why; roughly what context each reader spent; which queries misled. Fixes edit `docs/analysis.md`, the templates (bump `template_version`), and the query library in the same PR as the report.

**When enrichment arrives.** Running `aiobserve enrich` creates `session_enrichments` / `agent_run_enrichments` / `turn_enrichments` (`enrich/store.py`). Enrichment-aware queries are separate `*_enriched.sql` variants — selection gains strata over `outcome`/`friction`/`category`, digests gain descriptions so readers orient in one row — and the base variants keep working on a store without those tables. The process's shape does not change; selection and triage get sharper.

## Chosen test seam

The `aiobserve query` CLI against a store built by the real extract pipeline from checked-in fixtures into a temp DuckDB file. One smoke test executes every `queries/*.sql` through the runner's per-query manifest, overriding with fixture-sized bindings where needed (`records_slice` a line range, selection small quotas; a query missing from the manifest fails the test) — catching a query broken by a schema bump loudly. Behavioral tests cover `--since` and `$as_of` binding, the corpus predicate including a worktree path and reporting the excluded count on stderr, the selection strata, backfill, and run-out rules at fixture-sized quotas, and a pin on the production default values the committed reports cite. Fixtures redact all strings, so tests assert on counts and tags, never on text sizes (`.claude/rules/testing.md`).

## Slices

1. Query runner + three broad-count queries (tool failure rates, skill activity, cost distribution) + the smoke and `--since` tests — proves the seam and one representative count
2. The rest of the library: trends, co-occurrence, session shapes, digests, `records_slice`, selection — verified by the smoke test plus the selection/strata tests
3. Templates + `docs/analysis.md` + CLAUDE.md Layout line — verified by `mise run check` and by a dry-run digest of one real session
4. Iteration 1 itself, run by the manager per `docs/analysis.md`, ending in the first committed report — the process's own acceptance test; its review section is the input to iteration 2

## Decisions

- Queries as versioned `.sql` files run by a CLI, not Python-embedded strings or ad-hoc SQL — diffable, citable by name, reusable; the report still inlines the SQL it ran so its evidence is self-contained. (The duckdb CLI binary is not installed here; the runner rides the existing Python dependency)
- Fixed dual window + weekly trends, not exponential recency decay — a decayed count is not a query a reader can re-run and argue with; a window is a filter
- Per-session/run reports in gitignored `data/analysis/`, not `reports/` — they sit too close to transcript text to commit; the committed artifact is the synthesized, human-reviewed report
- Stratified deterministic selection with ordered backfill, not read-the-most-recent-N or read-all — strata aim at the improvement categories, backfill stops them collapsing into the same few monster sessions, and a selection you can re-run is a selection you can criticize
- Absences counted, never read-sampled — a ~30-session sample cannot bound prevalence below roughly one in three, and its pool excludes 31% of the window; the corpus query has no such limits. The random stratum buys discovery only
- Quotes in committed reports require a record citation plus rule-based redaction, not reviewer judgment alone — an instruction is not a control
- Rule-based session-shape classifier in SQL, not embedding clustering — citable and key-free; revisit if enrichment categories prove richer
- Evidence ladder with three named confidence levels, not free-form confidence prose — makes the reading→counting promotion mandatory rather than aspirational
- Process guide in `docs/analysis.md`, not in this plan — the process is durable and iterated; a plan documents one change
- New dated report per iteration, not versioned edits of one report — matches `reports/README.md` naming and keeps the shortcoming history readable

## Out of scope

- Acting on recommendations in mycelia — the report proposes repo tweaks; filing or applying them is a separate task in the other repo
- Measuring whether a recommended tweak worked — that is a later iteration's finding, needing before/after windows and the correlation caveat
- The enrichment pipeline itself and any API-key wiring (`plans/enrichment/design.md` owns it); a viewer/dashboard over the store
- Re-parsing pruned sessions from `raw_records`; any store mutation — the analysis layer is read-only by construction
- Automating steps 4–6 into code — the manager and subagents run the process; the tooling here is queries, templates, and one CLI subcommand

## Open questions

- Reading budget: 31 sessions + ~20 runs at the default quotas is a guess sized to synthesis context; iteration 1's review resets it empirically — a parameter change, against the realized composition the stratum tags report
