# 2026-08-07 — mycelia: where agent sessions lose money and time

First full analysis iteration over the mycelia corpus. The question: **where do Claude Code sessions on mycelia waste spend, context, or wall time, and which of those losses would a change to mycelia's guidance, agent definitions, or config remove?**

## Corpus and scope

- Store stamp: 575 sessions, `max(started_at)` 2026-08-07 07:47:41-04:00, schema_version 7, extractor_version 7 (`data/analysis/2026_08_07/stamp.txt`)
- Analysis window: trailing 28 days — 264 sessions, 2,754 turns, 123,291 tool calls, 1,780 agent runs, 890 compactions, $11,865.27
- Every count below comes from `src/hyphae/analyze/queries/<name>.sql` run with `project=/Users/nob/repos/mycelia, since=NULL, as_of=2026-08-07, window_days=28`; CSVs and full citation lines are in `data/analysis/2026_08_07/counts/`
- Read sample: 31 sessions drawn by `select_sessions` (cost_quota=8, error_quota=5, compaction_quota=4, discovery_quota=8, skill_threshold=5, seed=hyphae) plus 18 runs via `select_runs` (runs_per_stratum=1, min_runs=5) and reader flags. Reader reports live in gitignored `data/analysis/2026_08_07/{sessions,runs}/`

**Scope.** This corpus is one person's Claude Code sessions on one codebase. Every finding is evidence about mycelia's guidance and configuration, not about Claude Code or coding agents in general, and every recommendation targets mycelia.

Findings are labeled by the evidence ladder in `docs/analysis.md`: **counted** (a corpus query backs it), **recurring** (three or more independent session reports), **anecdote** (one or two sightings; a hypothesis). Citations are `(source, first_line–last_line)` per the quoting contract; `source` is `main` or an agent id within the named session.

## Amendment, 2026-08-08

Three findings moved up the ladder. Iteration 1 published them as recurring because no query could count them; the counting queries — `error_signatures`, `agent_compactions`, `error_records`, and a `view_runs` carrying cost, errors and compactions — were built after publication, from the process fixes listed at the end of this report. They ran against the same store with the same bindings as everything above, so the corpus stamp still holds.

R1 became C3, R3 became C4, and the first of R4's three false-positive patterns became C5. The rest of R4 stays recurring: a bare `Exit code 1` carries nothing that separates a benign grep miss from a real failure. Each promoted finding keeps its reader evidence as the mechanism — the query supplies only the magnitude.

One count corrects a reading. R3 said orchestrator threads stay lean, and they did in the four sessions read; corpus-wide the main thread compacts more per thread than every agent definition except `implementer`. C4 carries the correction.

## Counted findings

### C1. Fifty delegation-heavy sessions carry 94% of window spend; a third of sessions do no work

`session_shapes.sql`: of 264 window sessions, 50 delegation-heavy sessions cost $11,103.43 — 93.6% of the window's $11,865.27. Another 82 sessions (31%) are no-work: zero tool calls, zero agent runs, $0 — mostly `/model` and `/effort` config touches (`slash_commands.sql`: 64 `/model` turns across 56 sessions). `cost_distribution.sql`: mean $44.94 against a p50 of $0.17; the top decile holds 83% of spend.

Everything else in this report should be read against this shape: mycelia's costs live almost entirely inside orchestrated multi-agent sessions, so guidance that improves subagent briefs and agent definitions has far more leverage than guidance aimed at interactive use. Confidence: high.

### C2. The Honeycomb MCP is the highest-error active MCP surface

`tool_failures.sql` (window): `mcp__honeycomb__run_query` 20 errors / 292 calls (6.9%), against 2.1% for Bash and 0.9% for Read; `agent_types.sql` shows the `honeycomb` agent type at 35 errors over 430 calls in 17 runs. The mechanism, in the one failing run read, is auth: every MCP call in run `acccfdd07b854efa3` (session `0ba68577`) returned "The requested resource is invalid, missing, unknown, or malformed. The requested 'resource' was not included in the original grant." (`acccfdd07b854efa3`, 19–24) — an OAuth grant missing a resource, burning the whole dispatch. The other honeycomb run read (`aa6b3f5d`, session `24bfe69f`) succeeded, so the failure is not universal.

Recommendation: verify the Honeycomb MCP grant covers the resources the `honeycomb` agent queries, and have the agent definition fail fast on the first auth error instead of retrying tool by tool. Error rate: high confidence; auth as the dominant mechanism: low (one run).

### C3. Edit/Write-before-Read is two thirds of every Edit and Write failure (published as R1)

`error_signatures.sql project=/Users/nob/repos/mycelia since=NULL as_of=2026-08-07 window_days=28 signature=NULL min_occurrences=5 signature_chars=120`: over the window, "File has not been read yet. Read it first before writing to it." is 460 of Edit's 661 errors (70%) across 27 sessions and 190 threads, and 123 of Write's 216 (57%) across 33 sessions and 103 threads. Together 583 of the 877 Edit/Write failures — 66%. The session counts overlap and do not add.

The mechanism comes from the read sample, where the signature landed in ≥6 sessions, concentrated in implementer and auditor runs: `0164a230` (main, 522–527, 3× after a `gt checkout` branch switch), `26dfe608` (runs `afa965bcc1b7a691f` and `ab9c838384bb33718`, 12× against `/tmp` copies of the worktree), `8c2ea996` (19× across ≥4 runs, clustered right after `[Request interrupted by user]` turn boundaries — `ae2c93066ea2c8864`, 75–210), `0db9e51a` (5×), `d835351c` (run `a1f560beb5849a692`, 7×), `4b613b5a` (1×). Three sub-mechanisms: a new turn or interruption resets the read state; the guard is path-scoped, so a file read at its repo path still blocks edits to a `/tmp` copy; Write to a file the run never read.

Recommendation: one line in mycelia's implementer and auditor definitions — Read a file at its current path, in the current turn, before Edit/Write, even if you read it earlier or at another path. Confidence: high. The count settles iteration 1's open question of how much of the 877 this signature explains, and replaces its floor of six read sessions with the corpus figures above.

### C4. Half of implementer threads compact; implementers carry 46% of the window's compactions (published as R3)

`agent_compactions.sql project=/Users/nob/repos/mycelia since=NULL as_of=2026-08-07 window_days=28`: of the window's 890 compactions over 2,044 threads, `implementer` holds 412 over 426 threads — 0.97 per thread, with 216 of those threads (51%) compacting at least once, across 41 sessions. The definitions it is measured against: `general-purpose` 0.34 (177 over 522 threads), `claude` and `auditor` 0.41, `honeycomb` 0.12, `doc-writer` and `writer` 0.05, `Explore` 0.02 (3 over 136 threads), `pr-submitter` 0.02, and `stack-merger`, `workflow-subagent` and `claude-code-guide` at zero. Ad-hoc one-run definitions top the per-thread ranking with the same shape at n=1 — `impl-rung1` 9 compactions in a single thread, `impl-cards` 7.

Where the count corrects the reading: the main thread is not lean. 49 of 264 main threads compacted, 136 compactions, 0.52 per thread — second only to `implementer` among definitions with ten or more threads, and 15% of the window's total. Read as: orchestrators compact about half as often per thread as implementers, not negligibly.

The mechanism comes from four read sessions, all at a consistent ~166–172k-token auto-compaction ceiling: `2f3e6be5` (one run took 6 of the session's 27 compactions, ~6% of wall time), `8320539c` (22 of 25 sub-run compactions in implementers, one every 15–25 minutes), `cb76d8e4` (every implementer compacted ≥1×; designer, test-planner, and Explore runs zero — the implementers front-loaded 25–52KB whole-file reads in their first minutes), `ce02402d` (post-compaction full re-read loop: ~131K chars re-read, next compaction 4.6 minutes later). Corroborating: `8ee00a94` (run with 15 compactions), `d835351c`.

Recommendations: split multi-hour implementer briefs so each rung gets a fresh dispatch; prefer targeted reads (grep, offset/limit) over whole-file dumps in implementer guidance; add post-compaction discipline — trust the summary, re-read by range, don't re-read the tree. Confidence: high for the concentration; the compaction→defect link stays unestablished (see "What we could not tell").

### C5. A `===` separator in a zsh command line is a quarter of all Bash errors (published as R4's first pattern)

`error_signatures.sql project=/Users/nob/repos/mycelia since=NULL as_of=2026-08-07 window_days=28 signature="== not found" min_occurrences=1 signature_chars=120`: 381 window Bash errors carry the text `== not found`, across 42 sessions and 224 threads — 26% of the window's 1,487 Bash errors, and 34% of the 1,115 whose first line is `Exit code 1`.

The text is attributable because zsh produces it in one way: equals-expansion on a token beginning with `=`. A chained `echo ===` separator runs, prints its output, and zsh then reports `(eval):1: == not found`, which the harness reads as a failed call. `error_records.sql session_id=31c7f80b-… source=NULL max_chars=1500` shows the fragment sitting after a successful command's output in that session's main thread. Reader sightings gave the mechanism first: `31c7f80b` main, 50–51; `4b613b5a` run `aad961ae2ae09e010` 3×; `24bfe69f` run `ad466ce80911da904` 2×; `4208c1bd` run `a58506e9ffaa80800`, 29–30; also `aa6b3f5d`.

Recommendation: mycelia Bash guidance uses `printf` or a quoted string as a separator. Consequence for anyone reading these numbers: a quarter of the window's Bash "errors" are this, so `tool_failures` overstates Bash failure by at least that much. Confidence: high — the count raises the sighting floor from 4 sessions to 42.

## Recurring findings

### R2. Agents with persistent memory files write them before reading them

Three sessions: auditor run `a0e02c85a90dd91fe` (session `0a527620`) had its MEMORY.md Write rejected (`a0e02c85a90dd91fe`, 127–128); pr-submitter `a5d0ae93c86927be3` (session `1ae6e5f6`) hit the same on its own MEMORY.md; auditor `aa10eede9bdf109fd` (session `f087648e`) three times. Recommendation: any mycelia agent definition with a persistent MEMORY.md reads it at run start. Confidence: high; cost per incident is small but the fix is one line.

### R4. Two more benign patterns read as errors, and neither can be counted

The `===` separator moved to C5. These two stay recurring, because the error text does not distinguish them:

- grep/rg with no match exits 1 — 4 sessions (`0db9e51a` 3×, `24bfe69f`, `8d930c77` run `ac5abc2b2bf516436`, `0a527620`)
- `gh pr checks` exits nonzero while checks are pending — 2 sightings (`cdedfb8f` run `a5a9c890f2ec9e879`, `1ae6e5f6`)

Both land in a bare `Exit code N` group — the window holds 1,115 `Exit code 1` errors and 16 `Exit code 8` — and nothing in the result says which command produced it. Attributing a slice of `Exit code 1` to grep would be a guess with a number attached, so the share stays uncounted; counting it needs a signature query over the command line rather than the error text (process fix below).

Recommendation for mycelia: Bash guidance treats grep exit 1 and a pending `gh pr checks` as expected. Confidence: high for recurrence; share uncountable with today's queries.

### R5. The merge-stack flow has three documented-gap failures

Three distinct gate flaws in three sessions (the class recurs; each flaw is a single sighting):

- `17af721e`: an out-of-band commit landed mid-ladder and turned main red; the session then built a headRefOid snapshot guard (mycelia PRs #560/#561) — partially actioned already
- `cdedfb8f`: Chromatic's "UI Tests" check sits permanently PENDING, blocking the tip-CI gate (kin sighting in `1ae6e5f6`)
- `6562b226`: no documented way to wait for tip CI — the agent hand-rolled a foreground poll that blew the 10-minute Bash timeout, and after a `--no-verify-ci` merge the restack guard halted every remaining rung

Recommendations: document or allowlist the perpetually-pending Chromatic check; add a CI-wait recipe to `docs/merging-stacks.md`; document the per-rung halt loop and its recovery. Confidence: medium — each flaw seen once, but all three are gate-design gaps in the same flow.

### R6. Worktree isolation is convention, not mechanism, and agents fall out of it

Three-plus sessions: `0db9e51a` (4 of 21 errors were worktree-vs-main path mistakes), `c7c4cae9` (multiple runs edited the shared checkout; the same agent did it three times back-to-back), `e4003d83` (doc-writer run `a8920c4c35f380316` worked in the shared checkout — isolation is enforced only by the Edit tool, not Bash, and all 6 of its errors cascaded from one `cd`). Kin anecdote: `ec00d20d` — a `/tmp` worktree wiped by macOS made the session unresumable.

Recommendations: put the assigned worktree path at the top of every subagent brief; validate Bash cwd the way Edit validates paths (a mycelia hook can do this); give scheduled runs a persistent worktree location outside `/tmp`. Confidence: medium-high.

### R7. No house pattern for waiting on long-running work

Three to four sessions improvised waits and hit the same walls: `0164a230` (sleep-chain hard-blocked), `17af721e` (identical sandbox block in two runs — "a chained `sleep 60; tail ...` polling command was blocked by the sandbox, which told the agent to use Monitor with an until-loop instead" (`a24a0479bd686b88a`, 35); also `a92e13eb3ae2a6dfe`, 312), `6562b226` (foreground poll → 10-minute timeout), `cdedfb8f` run `a5a9c890f2ec9e879` (2-minute timeouts). The harness's own guidance (Monitor, run_in_background) surfaces only after the agent violates it.

Recommendation: one documented wait recipe in mycelia guidance — Monitor with an until-loop, or run_in_background — referenced from the merge and verification docs. Confidence: high.

### R8. The auto-mode permission classifier denies benign commands

Three sessions: `4c0c9e8e` (run `a223a319f1f29c055`: `sed -n` and `uv sync` denied three times in a row, 40–90s lost each), `0db9e51a` (5 main-thread denials, including spawning an auditor the human had approved), `e4003d83` (2 denials mid-recovery). Counted context (2026-08-08, `error_signatures.sql`, same bindings, `signature=NULL min_occurrences=5`): classifier denials in the window run 49 on Bash over 17 sessions and 27 threads, plus 9 on Edit, 7 more Bash denials reasoned "[Irreversible Local Destruction]" and 5 with no reason given. The query counts denials, not whether the command was benign — that judgment stays with the three read sessions, so the finding stays recurring.

Recommendation: allowlist read-only and idempotent commands (`sed -n`, `uv sync`, `ls`, `grep`) in mycelia's settings. Confidence: high for recurrence; the specific allowlist should come from the denial log, not this sample.

### R9. Orchestration gaps: the manager workflow loses state, contradicts guardrails, and under-specifies briefs

The class recurs across ≥5 delegation-heavy sessions; each sub-gap is a single sighting:

- **No research-tier routing.** `c068966d`: no routing row for research work, so the manager improvised a recursive opus tree (depth 3, up to 6 concurrent opus against a "two opus" cap) — roughly $45 of the session's $66.87 before design started
- **Spawn prompt contradicts the harness.** `c7c4cae9`: dispatch prompts order subagents to write scratch report files while the harness hard-blocks subagent report writes ("Subagents should return findings as text, not write report files") — ≥8 of 9 sampled multi-error runs wasted a call and a heredoc workaround each. Counted (2026-08-08, `error_signatures.sql`, same bindings, `signature=NULL min_occurrences=5`): that block fired 42 times in the window over 7 sessions and 38 threads
- **Manager state goes stale.** `c068966d`: manager-state.md written once, never updated across 10 dispatches (~106 min). `8ee00a94`: the human lost track of 39 subagents and paid for a from-scratch git/gh reconciliation although the state sat in the manager scratchpad
- **Brief omits the audit bar.** `4b613b5a`: four audit/implement rounds (~$42) because the brief never named the mutation-testing bar the auditor would apply
- **Long-lived agent reuse over fresh dispatch.** `5f4b59fb`: one doc-writer alive ~11h across 3 injected asks (one reversing a "no PR" instruction); two context-reload calls cost $2.96, ~21% of the run. Kin: `24bfe69f` (5 checkpoints fused into one honeycomb run, 2 forced compactions)
- **`gt sync` discards unpushed work.** `f1a1eb9a`: a sync-class reset threw away audit-accepted rung-14 rework — the third occurrence per the brief of run `ac79b8d8461dc7eb8` — ~$37 of recovery

Recommendations, one per gap: add a research tier to the routing table with model and concurrency caps; fix the spawn-prompt/guardrail contradiction; have the manager render scratchpad state on request; name the audit bar in every implementer brief; prefer fresh dispatch after an idle gap; push after every audit-accept, or guard `gt sync` against unpushed branches. Confidence: high that orchestration is where the money leaks (it is where 94% of spend sits — C1); each sub-gap medium-to-low individually.

### R10. Subagent model tier drifts upward when not pinned (weak recurring)

Three sessions, inference flagged: `c068966d` (opus inherited to depth-2/3 sub-researchers with no explicit override), `62a5c06e` (Explore on opus at 4.6× the corpus average Explore cost — `agent_types.sql`: Explore averages $1.03/run), `24bfe69f` (honeycomb on opus while sibling runs used fable). Recommendation: state the model in every dispatch. Confidence: low-medium — tier attribution comes from reader inference over run costs, not a model field.

## Anecdotes

Each is a hypothesis with its session named.

- **A1. grill-me assumes a live interviewer.** `4208c1bd`: three one-shot dispatches; the "Reading answers" phase ran against nobody, and "message me" had no listener. Fix: a non-interactive variant, or the dispatching prompt states how phase 2 resumes
- **A2. handoff skill saves to OS temp** while mycelia keeps handoffs in-repo (`08483117`). Fix: the skill checks for a repo `handoffs/` convention first
- **A3. The writing skill doesn't reach hands-on doc edits.** `f087648e`: prose edits to agent docs never loaded the style guide; the writer run also read the 21KB style guide twice. Fix: mycelia doc-edit guidance points at the skill
- **A4. pr-and-document fork friction — already fixed.** `17e0f606` hit it; mycelia commit `6e34c01e0` (2026-07-30, "one pr skill, docs land with the change") removed the skill, and its skills directory now carries only `pr`. Reported as validated
- **A5. Depth-2 fork nesting rejected, twice, unlearned.** `5a88789c`: "Fork is not available inside a forked worker. Complete your task directly using your tools." (`a61a059e3610e6fb4`, 47–48). Fix: drop Fork from forked workers' tool sets or brief the restriction
- **A6. ~10.5KB of identical skill/tool-listing preamble per dispatch.** `5b451fe6` (3 sampled runs of 72: 9,571 + 888 chars each; ~4KB noted in `17af721e`). Mechanically per-dispatch, so ~1,780 window dispatches would multiply it — but only 2 sessions sampled. Mycelia's lever: trim skill count and description length
- **A7. Interrupt didn't stop a background implementer.** `b53a27cb`: "Are you hung? I cannot seem to quit" — ~78s, then force-kill
- **A8. Playwright browser cache missing per worktree,** rediscovered by two independent subagents in `8c2ea996`. Fix: install step in the worktree setup script
- **A9. Fixed-port verification script collides across worktrees.** `4c0c9e8e`: stale "port 8497 already in use". Fix: derive the port from the worktree
- **A10. Read on a cached WebFetch result trips the 25K-token cap regardless of offset/limit.** `62a5c06e` (run `acaeaa268634ce683`, 2 of 3 errors). Harness behavior; noted for upstream

## What we could not tell

- ~~The corpus-wide share of Edit/Write errors matching the before-Read signature (R1)~~ — answered 2026-08-08 by `error_signatures`; see C3
- What share of the window's 1,115 Bash `Exit code 1` errors are benign grep no-matches (R4). The error text is identical for a real failure, so no signature query reaches it
- Whether compaction correlates with defects: `a6cc585d`'s audit found defects after a 5-compaction run, one unverified anecdote
- How prevalent idle-interrupted dispatches are (`cdedfb8f` showed 3 of 65)
- Whether the ~10.5KB dispatch preamble (A6) holds across all 1,780 window dispatches

## Process review

Per the checklist in `docs/analysis.md`.

**Strata.** Cost, tool-errors, compactions, and skill strata all produced findings. Discovery was diluted: 3 of 8 draws (`44cd85d9`, `de0b0560`, `c221842f`) were config-only sessions — `/model`/`/effort` turns, 0 tool calls, $0 — that the pool filter admitted because they have turns. The run draw (`select_runs`) was productive; it surfaced R2, R10, and both honeycomb runs.

**Template fields.** "Context waste" was often "not assessed". Run-only readers invented ~15 off-vocabulary tags (`false-positive-error`, `write-before-read`, `mcp-auth`, `worktree-isolation`, …) because the vocabulary lives only in session.md and run.md says "do not restate it here". `other`/invented tags clustered on false-positive errors — a real missing category.

**Failed corroboration.** Dropped: `ec00d20d`'s self-referential transcript search (not a mycelia-guidance issue); `ab2c08564`'s malformed JSON tool args (one-off platform glitch) and rung 3–4 scope expansion (cause untraced); `4c0c9e8e`'s "compactions discard context the manager must re-derive" (no evidence found). Demoted to "could not tell": compaction→defect, idle-interrupt prevalence. Moved here from findings: `d835351c`'s "87% of session cost invisible to the main-thread digest" — that is a hyphae tooling gap, not a mycelia one.

**Reader context.** Error-hunting dominated reader spend: several readers scanned 1,000+ records at 2,000-char caps to locate `is_error` results, and some never found them (`a296e39745f86e891` 5 errors, `ac549ca3bfec11e8c` 14, `afff07e2437c2e264` 43 — all untraced). Readers self-reported context spend inconsistently; the templates now ask for it.

**Queries that misled.** `tool_failures` counts include false positives — a quarter of its window Bash errors, per C5. `session_digest` covers main-thread only, hiding up to 87% of a delegation-heavy session's cost. `run_digest`'s single-turn framing hides multi-ask agent reuse (`5f4b59fb`). `select_sessions` admits config-only sessions to the pool. One reader initially missed `view_runs`; the brief was updated mid-iteration, as was a `mktemp -d` scratch-dir line after two concurrent readers collided in `/tmp`.

**Fixes applied in this commit** (docs and templates only):

- `docs/analysis.md`: reader protocol now tells readers to work in a `mktemp -d` scratch dir, enumerate a session's runs with `view_runs` before digging, and record context spent
- `templates/session.md` (template_version 2): added `false-positive-error` to the vocabulary; added a "Context spent" line
- `templates/run.md` (template_version 2): same two additions, and the vocabulary pointer now names the file and the rule instead of only forbidding restatement

**Fixes listed for the query-library implementer** (not implemented here — code lane is held elsewhere):

1. `select_sessions.sql` / `queries.py`: pool filter requires `api_calls > 0` (or `tool_calls + agent_runs > 0`) so config-only sessions can't take discovery slots
2. New `error_records.sql`: per `(session_id[, source])`, emit `line_no`, `tool_name`, and the first ~200 chars of each `is_error` tool_result — kills the reader error-hunt
3. New corpus error-signature query: normalize the first line of error text, group with session/run counts — makes R1, R4, and R8 countable next iteration
4. `view_runs.sql`: add `cost_usd`, `tool_errors`, `compactions` columns
5. New per-run compaction query by `agent_type`, counting runs that compact more than their session's main thread — makes R3 countable

**Status, 2026-08-08.** All five landed. Queries 3 and 5 produced C3, C4 and C5 above; query 2 (`error_records`) confirmed C5's mechanism without opening a raw slice. One fix remains open: counting R4's grep no-match needs a signature query over the command line, since the error text of a benign no-match and a real failure are the same string.
