# 2026-08-09 — mycelia: agent friction, iteration 2 (focused)

Successor to [the 2026-08-07 report](2026_08_07_mycelia_agent_friction.md), asking the same question: where do Claude Code sessions on mycelia waste spend, context, or wall time, and which of those losses would a change to mycelia's guidance, agent definitions, or config remove?

This iteration is deliberately narrow. The corpus grew by essentially one session since v1 — `cb76d8e4`, a 48-hour unattended `/manager` run that cost $1,145.27 — so instead of a full stratified re-selection, this iteration deep-read that session (one session reader plus 13 run readers) and re-ran every corpus query at the new date. The scope deviation and its rationale are in the process review.

## Corpus and scope

- Store stamp: 575 sessions, `max(sessions.started_at)` 2026-08-07 07:47 EDT, `max(api_calls.started_at)` 2026-08-09 10:53 EDT, schema_version 7, extractor_version 7 (`data/analysis/2026_08_09/stamp.txt`). The refresh extracted 4 sessions and left 571 unchanged; roughly 95% of the new activity is `cb76d8e4`
- Analysis window: trailing 28 days at `as_of=2026-08-09` — 256 sessions, 2,571 turns, 119,024 tool calls, 1,670 agent runs, 876 compactions, $11,109.87 (`session_counts.sql`)
- Every count comes from `src/hyphae/analyze/queries/<name>.sql` with `project=/Users/nob/repos/mycelia, since=NULL, as_of=2026-08-09, window_days=28`; CSVs and full citation lines are in `data/analysis/2026_08_09/counts/`. Non-default `--param` bindings are stated inline
- The window moved two days against v1: sessions from July 10–12 fell out while `cb76d8e4` and three small sessions came in. Every count delta below mixes both effects, so none is read as a trend
- Read sample: session `cb76d8e4` (stratum: cost — ranked #2 in the window by `select_sessions`, and the window's highest compaction count) plus 13 of its runs in three strata: 4 implementer cost outliers, 6 non-implementer coverage runs (including all 4 that `select_runs` drew from this session independently), and 3 runs nearest the session's median run cost. Reader reports live in gitignored `data/analysis/2026_08_09/{sessions,runs}/`

**Scope.** This corpus is one person's Claude Code sessions on one codebase. Every finding is evidence about mycelia's guidance and configuration, not about Claude Code or coding agents in general.

Findings carry their evidence-ladder label per `docs/analysis.md`. One warning specific to this iteration: 13 run reports from one session are 13 looks at one session, not 13 independent sightings. Nothing read this iteration can create a **recurring** finding by itself; a pattern seen in many of this session's runs is labeled *recurring within one session* and stays an anecdote unless a corpus query counts it. Citations are `(source, first_line–last_line)`; `source` is `main` or an agent id within `cb76d8e4` unless another session is named.

## The anchor session: what $1,145 unattended bought

A single `/manager` turn told the agent to keep working on its own for the length of a vacation, and it did: one main-thread turn, ~48 hours, 110 agent runs in ~27 numbered waves, each wave a fixed pipeline per tracked issue — Explore, designer, auditor, test-planner, implementer, auditor. Run mix: auditor 35, implementer 27, designer 18, test-planner 16, Explore 11, doc-writer 2, writer 1; median run $5.11.

Mostly, it worked. Every implementer run read closed cleanly with commits and green checks; the auditor, Explore, and test-planner runs read were error-free with cost that fits their role (`a77b43a33e194f626` spent its 109 calls on mutation-testing mechanics; `abdf85a4933eddcd8` ran 86 scoped greps). The friction findings below are about where the $1,145 leaked, not about the session failing.

Where the money and context went, per the session report:

- Implementers took $615 of the $981 run spend (63%), 74 of the 100 run-level compactions, and 128 of ~198 run-level tool errors — the same concentration C4 counts corpus-wide
- The main thread took $164.28 (14%) across 806 api calls and compacted 10 times, roughly every 4–5 hours at a ~167k-token ceiling, fed in part by chained `cat`/`head`/`ls` reads that dump whole issue and plan files into context (A14)
- Genuine tool failures were rare and small across all 13 runs read: a non-unique Edit `old_string`, a `Write` call given Bash's `description` parameter, an empty heredoc commit message, an inline Python `.group()` on a `None` regex match, and one harness-blocked `sleep 60` poll redirected to Monitor (`afba04527b62af557`, 165) — another sighting for v1's R7. Most of what the error counter shows is the false-positive mechanism in C5

## Counted findings

### C5, updated: the `===` separator now has a denominator — 83% of Bash calls that chain `echo ===` read as errors

Two counts, both at the 2026-08-09 window:

- `error_signatures.sql --param signature="== not found" --param min_occurrences=1`: 419 window Bash errors carry `== not found`, across 38 sessions and 220 threads (v1: 381 / 42 / 224) — 28% of the window's 1,471 Bash errors and 37% of its 1,128 bare `Exit code 1` errors
- New this iteration, `command_failures.sql --param mentions="echo ===" --param min_occurrences=1`: of the 522 window Bash calls whose command line contains `echo ===`, 435 (83%) returned as errors and 87 succeeded. The failing shapes are exactly the read chains the readers saw — `sed` 126, `rg` 64, `grep` 63, `cat` 58, `ls` 30 window errors by command head

The second count is what v1 lacked: a denominator. The idiom is not occasionally unlucky; it fails five times in six. The mechanism re-confirmed across six threads of `cb76d8e4` — main plus three implementers, a designer, and a test-planner — is unchanged from v1: zsh reports `(eval):1: == not found` after the chain's real output has already printed, e.g. an `ls && echo === && grep` probe aborted before the grep ran (`a399e4169f1d2b7a9`, 89–91). `echo ====` produces the same artifact (`ac93b375e07209517`, 72, 76).

Recommendation (upgraded from v1): don't soften this to a style preference — ban bare `===`/`====` echo separators in mycelia's Bash guidance and say what to use instead (`echo ---` or `printf`). Consequence for anyone reading error counts: at least 28% of the window's Bash "errors" are this one artifact, on top of the trailing no-match greps v1 could not count. Confidence: high.

### C6, new: the harness's report-file Write block fired in 38 threads, and agents comply after one rejection

`error_signatures.sql` (default bindings): the Write rejection "Subagents should return findings as text, not write report files" is a top-8 window signature — 42 errors across 7 sessions and 38 threads. The corpus count (43 / 8 / 39) nearly equals the window count, so the block first fired within the last 28 days: this is a new guardrail, not a long-standing one.

Two things settled this iteration:

- **Attribution.** mycelia's checked-in hooks (`frozen-doc-guard`, `mise-flag-order`, `post-compact-orient`, `ruff-fix` — none touching Write) cannot produce this message; it is harness behavior, as v1's R9 said. (Checked against mycelia's current hook set, not the window's; the set could have changed.)
- **Compliance, not thrash.** 42 errors over 38 threads means at most 4 threads saw a second rejection. Agents take the hint the first time; the cost per incident is one wasted Write plus re-sending the content as text

The v1 R9 contradiction stands: `c7c4cae9`'s spawn prompts ordered subagents to write scratch report files while the harness blocks exactly that. The count bounds the damage as small-per-incident but 38-threads-wide. Recommendation: remove "write your report to a file" instructions from mycelia's spawn prompts and manager guidance; the harness has decided this one. Confidence: high.

### C3, re-confirmed: Edit/Write-before-Read is still the top non-generic error signature

`error_signatures.sql` (default bindings): "File has not been read yet" is 335 of Edit's 510 window errors (66%, 22 sessions, 151 threads) and 99 of Write's 192 (52%, 30 sessions, 82 threads) — 434 of 702 combined, 62%. v1 counted 583 of 877 (66%); the absolute drop is the window shift (heavy July 10–12 sessions fell out), and the share held. v1's mechanism analysis and one-line recommendation stand unchanged.

### C4, re-confirmed: implementer compaction rate rose to 1.05 per thread

`agent_compactions.sql`: implementers hold 469 compactions over 447 window threads — 1.05 per thread, 234 threads (52%) compacting, 41 sessions; 54% of the window's 876 compactions (v1: 0.97 per thread, 46% of total). `cb76d8e4`'s 27 implementer runs, 74 compactions among them, pushed the rate up. The main thread runs 0.45 per thread (116 over 256), still second among definitions with ten or more threads. Two new single-thread extremes entered the window — `impl-rung1` (9 compactions, one thread) and `impl-cards` (7, one thread) — ad-hoc definitions at n=1, unread this iteration.

v1's mechanism and recommendations stand; `cb76d8e4` added a fifth read session at the same ~167k ceiling. Confidence: high.

## Anecdotes from the anchor session

Each is a hypothesis. All come from one session, however many of its runs show it.

### A11. Coordinator continuations to open runs force cache-miss reloads — 9–28% of run cost

New this iteration, and the read sample's largest quantified per-run waste. When the manager sent a follow-up message into an agent run that was still open, the continuation re-sent skill bodies the thread already held and forced a full cache-miss recompute:

- Implementer `ae14b1a78ebb8f180`: one continuation re-sent the `tdd` and `commit` skills, recomputing 95,572 tokens for $0.60 — 9% of the run (`ae14b1a78ebb8f180`, 155–158)
- Designer `a399e4169f1d2b7a9`: two continuations rebuilt 115.8K and 152.3K tokens for $1.69 together — 28% of the run (`a399e4169f1d2b7a9`, 138–142)
- The third typical run, auditor `adfe7445a1c17f331`, had no continuation and stayed single-shot — the control

Seen in 2 of 3 median-cost runs of one session; kin to v1 R9's long-lived-reuse sighting in `5f4b59fb` ($2.96, 21% of that run). Two independent sessions across two iterations — one short of recurring, and no query today reaches cache-miss tokens per continuation (see "What we could not tell"). If it generalizes, it taxes exactly the continuation pattern the manager workflow leans on. Hypothesis-grade recommendation: prefer fresh dispatch over continuing a finished run, and don't re-send skill text into a thread that already holds it.

### A12. A 240k-character agent-memory browse before any work

The session's priciest run, implementer `a09663f30413609c1` ($61.99, 7 compactions), opened by paging ~400 agent-memory filenames and batch-`cat`-ing ~16 memory files — over 240k raw characters before touching the plan or any repo file (`a09663f30413609c1`, 13–30). A sibling implementer in the same session (`aa9fb0923dd7cab57`) read only targeted lesson files, so the browse is a choice, not a requirement of the memory design. Hypothesis: mycelia's agent-memory guidance should say "grep by keyword, read what matches" rather than permitting a full-directory browse.

### A13. The manager's marker-assert scratchpad update is fragile

The manager tracked dispatch state in a scratchpad outside the repo, updated via a Python heredoc that asserts an exact marker string before rewriting; one update died on the `AssertionError` when the text had drifted (`main`, 2459–2462). Kin to v1 R9's "manager state goes stale." Hypothesis-grade fix: an idempotent update (append-only log or structured state) instead of assert-then-rewrite.

### A14. Main-thread doc reads by chained `cat`/`head`/`ls` bloat the orchestrator

The manager read issue and plan docs with chained Bash (`cat …; echo ===; head …; ls …`), dumping 1–6KB markdown files into main-thread context per wave; 6 of the main thread's 7 tool errors were a trailing `ls` on a missing-but-optional directory flipping a useful read to `is_error` (`main`, 645–649). This feeds both C5's false positives and the main thread's 10 compactions. The session reader did not count what share of the 644 main-thread tool calls follow this shape — that bound is future work.

## v1 findings carried forward

- **C1 (spend concentration): re-confirmed.** 45 delegation-heavy sessions carry $10,348.03 of the window's $11,109.87 — 93% (`session_shapes.sql`); 80 sessions (31%) are no-work; p50 session cost $0.17 against a mean of $43.40 (`cost_distribution.sql`). Same shape as v1
- **C2 (Honeycomb MCP errors): unchanged.** `tool_failures.sql` window: `mcp__honeycomb__run_query` still 20 errors / 292 calls — all of it inside this window, no new activity. Not re-read
- **C3, C4, C5: re-confirmed and updated above**
- **R5–R7 (merge-stack gaps, worktree isolation, wait patterns): not retested** — one new session cannot move a recurring finding. R7 gained one more sighting (the blocked `sleep 60` poll above)
- **R8 (classifier denials): count now entirely in-window.** 49 Bash denials / 17 sessions, corpus equals window (`error_signatures.sql`) — the denial behavior is recent, within 28 days. Benign-vs-justified still rests on v1's three read sessions
- **R9 (orchestration gaps): two sub-gaps sharpened.** The spawn-prompt/guardrail contradiction is now counted (C6), and the state-goes-stale gap gained the marker-assert mechanism (A13). A11 is kin to the long-lived-reuse gap
- **R10 and the v1 anecdotes: untouched**

## What we could not tell

- Whether A11's continuation cache-miss reloads generalize: no query reads cache-creation vs cache-read tokens around a mid-run continuation. This needs a query before iteration 3 can promote or drop it
- What share of the main thread's 644 tool calls follow A14's chained-read shape (the reader sampled only the error-adjacent slices)
- Whether A12's memory browse recurs in other sessions of agents with persistent memory — nothing counts "context spent before first repo touch"
- Still open from v1: the benign share of no-match grep exits (`command_failures` now counts grep-headed exit-1 at 102 window errors, but benign-vs-real is still the same string); the compaction→defect link; dispatch-preamble prevalence (A6)

## Process review

Per the checklist in `docs/analysis.md`.

**Scope deviation.** This iteration ran focused, not full: the extract refresh changed 4 sessions, ~95% of the delta being `cb76d8e4`, so a full stratified re-selection would have re-read a corpus v1 already covered. Selection evidence for the anchor: `select_sessions` (default quotas, `seed=hyphae`, now with `min_api_calls=1`) ranks it #2 by window cost; `select_runs` independently drew 4 of its runs (designer and test-planner, cost and errors strata). Full re-selection is deferred to iteration 3, by which time the window will have turned over materially.

**Strata.** All three read strata earned their slots, differently: the implementer-outlier stratum corroborated C5's mechanism (3 of 4 runs) and produced A12; non-implementer coverage spread C5 across agent definitions (designer, test-planner) and supplied the clean-run contrast that scopes the friction to implementers and the manager thread; the median-cost typicals — a stratum v1 didn't have — produced A11, this iteration's main discovery. The lesson: outliers corroborate, typicals discover.

**Template fields.** Run readers labeled the typical draws with an invented stratum name (`nearest-to-median-cost … not select_runs`) because the template's stratum field assumes a `select_runs` draw; honest, but the template should name a `synthesis-draw` option. "Context waste" was filled only in the session report. The `false-positive-error` tag added in template_version 2 was the iteration's most-used tag and carried C5's evidence — the v1 fix paid off.

**Failed corroboration / stayed anecdote.** A11 (no counting query exists), A12, A13, A14 (mechanisms seen once or only within the one session). Run `ab5edd08929b24939`'s missing-ADR Read was dropped entirely: one isolated miss, no retry pattern. Nothing this iteration was promoted on read evidence alone.

**Reader context.** All readers stayed inside the digests. Session reader: light digests plus 8 `records_slice` calls at ≤2,000 chars. Run readers: `run_digest`/`view_run_header`/`error_records` plus ~4–15 slices each at 50–4,200-char caps; two used `view_turn_calls` sorted by cache-creation tokens to find heavy calls (that lens found A11), and one ranked record sizes with a 1-char-preview `view_records` scan — cheap and effective. No reader opened a full thread.

**Queries.** None misled this iteration. `command_failures`' `mentions` binding, built for v1's grep question, produced C5's denominator on its first real use. One usage note: denominators need `--param min_occurrences=1`, since the default of 5 drops small success rows and silently inflates failure rates read off the table.

**For iteration 3.**

1. Full stratified re-selection over the turned-over window
2. A query for continuation cache-misses — cache-creation vs cache-read tokens at mid-run message boundaries, per agent thread — to promote or kill A11
3. Read the `impl-rung1` / `impl-cards` session(s): two single-thread definitions at 9 and 7 compactions, unexplained
4. Add a `synthesis-draw` stratum label to `templates/run.md` (bump template_version) when the templates are next touched
5. Re-check C6 after mycelia's spawn prompts are fixed: the window count should fall toward zero, and that fall is the cheap test that the fix landed
