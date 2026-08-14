# 2026-08-14 — mycelia: agent friction, iteration 4 (measurement and verification)

Successor to [the 2026-08-13 report](2026_08_13_mycelia_iteration_3.md), asking the same question: where do Claude Code sessions on mycelia waste spend, context, or wall time, and which losses would a change to mycelia's guidance, agent definitions, or config remove?

The corpus is **data-identical** to iteration 3: zero sessions have started since its stamp (`session_counts --since 2026-08-13`: 0 rows), and the store still holds 571 sessions capped at 2026-08-07 11:47 UTC. So nothing below is new behavior. What this iteration contributes is measurement and verification: three new queries (`path_failures`, `missing_file_recovery`, `idle_gaps`), normalized error signatures, a fully re-enriched and QC-cleared enrichment layer, and two iteration-3 findings put through independent verification — one of which (F6) largely failed it.

## Corpus and scope

- Store stamp: 571 query-visible sessions, corpus cost $16,108.17 — identical to the cent to iterations 2 and 3 (`session_counts`); `max(started_at)` 2026-08-07 11:47 UTC (`cb76d8e4`)
- Every count comes from `uv run aiobserve query <name>` with `project=/Users/nob/repos/mycelia, since=NULL, as_of=2026-08-14, window_days=28` unless stated; non-default `--param` bindings and pinned `--as-of` dates are inline with each count. All queries exclude 4 sessions with no `project_dir`
- **The trailing window shrank mechanically, not behaviorally.** At `as_of=2026-08-14` it holds 168 sessions / $8,653.24, down from iteration 3's 201 / $9,982.80 at `as_of=2026-08-13`, because a day rolled off the back of a corpus that stopped growing on 2026-08-07. No window delta in this report is a trend, and every iteration-3 window number is stale by construction. To reproduce an iteration-3 window figure, pin `--as-of 2026-08-13`. If no sessions arrive, the window empties entirely after 2026-09-04
- **Error-signature counts are not comparable across iterations.** `error_signatures` now stands absolute paths as `<path>` in the signature line, changing the group keys (it merged the isolation-guardrail message from 28 per-worktree groups into 3 rows). Wherever a count sits near an iteration-3 number below, treat them as two separate measurements of the same underlying failures, not a before/after pair
- Read sample: the same 30 sessions `select_sessions` draws deterministically (`seed=iteration4`; realized composition cost 8, tool-errors 5, compactions 4, skill 5, discovery 8 — discovery now under the new `min_discovery_api_calls=10` floor). 23 were read in prior iterations and got delta notes against the new lenses; 7 were fresh reads (six discovery draws plus `8c152204`); `0a527620` was skipped on an unchanged extract fingerprint. Four readers; notes in gitignored `data/analysis/iteration_4/`

**Scope.** This corpus is one person's Claude Code sessions on one codebase. Every finding is evidence about mycelia's guidance and configuration, not about Claude Code or coding agents in general. And because the corpus is paused, this data can no longer answer "did a fix help" — only measurement questions — until mycelia sessions resume. Citations are `(session_id, source, first_line–last_line)`.

## Findings

Numbered as in iteration 3; this iteration revises rather than discovers.

### F2, promoted and generalized: spawned runs 404 on gitignored directories their worktree doesn't contain (Counted, was Recurring)

The new `path_failures` query (`min_occurrences=5, tail_segments=1`) gives iteration 3's four-session mechanism a corpus count, and shows it is not just `handoffs/`:

- `handoffs`: 45 errors / 16 sessions / 36 threads corpus-wide, 40 of 45 (89%) in spawned runs — the corpus's #8 directory by errors
- `adrs`: 71 errors / 24 sessions — #3, 70 of 71 in spawned runs
- The rest of the table's top ranks above `handoffs` too: `issues` 62/20, `plans` 54/17, `docs` 48/20, `authoring` 46/9. (The #1 and #2 rows, `scratchpad` 188 and `implementer` 101, are agent scratch and worktree names, not mycelia source directories — the tail-segment grouping surfaces them separately.) Only `handoffs` and `adrs` failures were opened at record grain this iteration; whether `docs` and `authoring` carry the same worktree-invisibility mechanism is unchecked

Raw-grain confirmations this iteration: `26dfe608` (two `records_slice`-verified reads of a worktree-relative `handoffs/` path), `8c152204` (a spawned run 404s on `docs/adrs/` inside its worktree, then guesses again rather than listing), and `f1a1eb9a` — never read for this before — which alone holds 11 of the 45 `handoffs` errors across ~9 spawned threads. One `f1a1eb9a` thread names the cause in its own output: `.gitignore:34:/handoffs/` followed by `ls: handoffs: No such file or directory` (`f1a1eb9a`, `a2fec7662c1f3a755`, `error_records` line_no 162).

Caveat on the count: `path_failures` is directory-keyed, not mechanism-keyed, so the 45 mixes worktree-invisibility with plain filename guesses against the primary checkout (one seen in `cdedfb8f`). Don't cite 45 as a pure count of this mechanism without spot-checking the mix.

Recommendation (revised): fix at the worktree-creation mechanism, not a per-directory allowlist — make the gitignored directories spawn prompts reference (the ones `path_failures` names) reachable from spawned worktrees, or inline their content in the spawn prompt.

### F6, rewritten: the dispatch mechanism is real; the causal story was wrong

An independent verification pass (`handoffs/verify-f6-teammate-dispatch.md`) re-ran F6 claim by claim. Counts require `--as-of 2026-08-13` — both teammate sessions started 2026-07-16, exactly 28 days before that date, so they have already left the current window.

**What held.** `agent_compactions` reproduces exactly (`impl-rung1` 9 compactions, `impl-cards` 7, `implementer` baseline 1.1/thread), and the dispatch mechanism is now confirmed at raw grain — which iteration 3's reader never opened. The re-tasking really is a `<teammate-message>` user record injected into the live run thread, e.g. the rung-2 dispatch telling the run "Rung 2 is yours — you have the 5b context warm" (`10d0349d`, `aimpl-rung1-07b89bf51437c28f`, 725–726).

**What was refuted.** Two of F6's three load-bearing claims:

- *"Sequential unrelated items"* — the chains are dependent. Rungs 1→2→3 are consecutive rungs of one plan; PR3→PR5→PR6b are one stacked PR train, and the dispatch text says the reuse is deliberate, for warm context and in-tree predecessor code. Iteration 3 read a chosen trade-off as a mistake
- *"The mechanism, not task size, is the delta"* — corpus-wide, single-dispatch `implementer` threads run 4–7 compactions with no re-tasking at all: eight such threads in `4c0c9e8e` and `cb76d8e4`, at 385–779 tool calls each (endpoints reverified via `run_digest`, compaction counts via `view_runs`). In the 464–510-call band that matches the teammate threads' own volume, the single-dispatch threads sit at 6–7 against `impl-cards`' 7 and `impl-rung1`'s 9. And `impl-unbox` took *more* dispatches than `impl-cards` (13 vs 12) with fewer compactions (3 vs 7). Compaction tracks thread token volume; re-tasking is just one way a thread gets long. A residual re-tasking cost of perhaps +2 compactions at matched volume is visible but not separable at n=2

**Denominator caveat.** `agent_type` is a free-text name, so an ad-hoc single-use definition gives `agent_compactions` a denominator of 1 — the 9.0 "rate" is one thread's count, and a 413-thread mean is the wrong comparator. The right comparator is the top of the `implementer` distribution, which sits at 4–7.

Consequences: iteration 3's C4 scoping line — "the compaction tail is re-tasked teammate threads" — is rewritten: **the compaction tail is large single-dispatch implementer threads**, and the two teammate threads are members of it, made visible only by their ad-hoc names. The recommendation "dispatch a fresh run per work item" is withdrawn: it was never priced against the re-read cost of splitting a dependent chain, and no session ran that counterfactual. The "third reload mechanism" claim is downgraded — its "0 compaction-linked reloads" evidence proves nothing (compaction-linked reloads are a main-thread phenomenon corpus-wide, 34 of 40), though the genuinely low idle share (25–40%, against a corpus rate of 67.6% — 627/928 — and 78% for `implementer`, 166/214) stands as a description of busy-thread churn, pending the control that was not run (idle share on single-dispatch hot threads).

### F3, rewritten: keep-warm loses on economics for the waits it targeted; a narrower version stays unpriced

Iteration 3 left F3 with two options: (a) accept idle-wait reload cost, or (b) cost out a keep-warm heartbeat. The pricing pass (`handoffs/pricing-f3-keepwarm.md`) and the new `idle_gaps` query close (b) for the multi-hour case it was proposed for.

The base numbers reproduce: `context_reloads` corpus 928 reloads / 627 idle / 40 compaction-linked, $949.63 reload cost against $8,485.61 on affected threads; at pinned `--as-of 2026-08-13` the window row matches iteration 3's cited 605 / 449 / $560.23 / $5,503.28 exactly.

**The pricing.** From `pricing.py`'s multipliers, one heartbeat firing costs ≈8% of the reload it defers (5-minute TTL; 5% at a 1-hour TTL) — an upper bound, since measured reload cost includes response generation a heartbeat wouldn't pay. Break-even is ~12.5 firings, i.e. an idle gap of ~56 minutes at 5-minute TTL: heartbeat wins under it, the reload is cheaper above it. F3's named mechanism — coordinator waits of 1–4.75 hours — sits past break-even on every sampled instance; on its own worked example, heartbeating would have cost more than the $22.52 the thread actually paid in reloads.

**The population shape cuts the other way from the finding's example.** `idle_gaps` (`min_idle_seconds=300`): 2,404 corpus gaps ≥5 min, 627 reloaded — and 477 of those 627 (76.1%) are under the 56-minute break-even (window: 331 of 383, 86.4%). The session iteration 3 chose to illustrate F3, `4c0c9e8e`, is a population outlier on exactly this dimension: 9 of its 12 reloaded main-thread gaps (75%) sit *past* break-even. A deep-read session chosen for narrative clarity is not guaranteed to be distributionally typical; there is now a cheap query to check.

Three more facts shape what a heartbeat could buy:

- Reload cost concentrates in **sub-agent resumes** (700–2,500 s gaps rebuilding 30k–140k tokens each) and the **first post-overnight main-thread call**, not in the main thread's routine short gaps — seen identically in `5a88789c` and `17e0f606`
- **A live cache entry is used almost always.** The pricing pass read the corpus's 21 warm-but-rebuilt reloads as doubt about the mechanism, but that count conditions on the outcome. Asked of the right population — every gap that returned inside the hour after a call wrote a 1-hour cache entry — 1,501 of 1,522 (98.6%) did *not* rebuild; 21 (1.4%) did (`idle_gaps`, gaps with `cached_1h` true and `idle_seconds` < 3600). The data supports the warm-entry mechanism. One caveat, cutting both ways: `cached_1h` says the prior call wrote *some* 1-hour entry, not that it covered the next call's whole prefix
- **Compactions and idle reloads are decoupled**, at least in the two sessions checked: `6eea741c` (12 compactions, 1 of 17 idle gaps reloaded) and `74dbe7ed` (4 compactions, 0 of 1). A heartbeat would not touch compaction cost

Disposition: the mechanism holds, and with the corrected warm-entry figure the case against keep-warm is economic, not mechanical. For the multi-hour coordinator waits F3 named, option (b) stays closed: every sampled gap sits past the ~56-minute break-even, and on the finding's own worked example heartbeating would have cost more than the $22.52 the thread paid in reloads. For sub-hour gaps — the majority of reload events by count, and where sub-agent resume cost concentrates — a scoped keep-warm is now plausible: the warm-entry mechanism works, and those gaps sit under break-even. But it stays a hypothesis, not a recommendation, because its dollar upside is unquantified — no query splits reload cost by gap length, and short reloads rebuild fewer tokens on average, so the 76–86% count share overstates the dollar share by an unknown amount. Until that split exists, what stands is (a): idle-wait reloads are the measured price of mycelia's multi-hour async-wait pattern, ≈$416 of the pinned window's $560.23 reload cost by proportional estimate.

### F9, reframed: after a genuine 404, listing is the minority recovery

`missing_file_recovery --param missing="does not exist"` — the population a listing could plausibly have prevented — corpus: 235 failures, of which 43 (18.3%) recovered by listing the failed directory, 65 (27.7%) listed elsewhere, and 127 (54.0%) listed nothing (window: 118 = 23 / 37 / 58). Unbound, the query returns 1,429 corpus failures at 88.4% no-listing, but that population is dominated by guardrail and permission noise the bound predicate removes.

Iteration 3's F9 read as "agents recover by listing" because its citations were recovery stories. Re-checked against the split, they divide: `4b613b5a` and `08483117` listed (the 18% minority), `2f3e6be5` did not (moderate confidence — tool names weren't fully resolved at the character cap used), `4208c1bd` has one listing recovery plus a previously unexamined third 404 — the session's last action, unrecovered. And `0a527620` shows both dispositions inside one session, one thread guessing again immediately (`a8dc890b7fc92d39b`, 226–228), another running a scoped `find` (`a0e02c85a90dd91fe`, 95–96). The disposition varies call to call, not by session or agent type.

The reframe strengthens the recommendation rather than weakening it: guessing again — or giving up — is the modal behavior. The one-liner for mycelia's agent guidance ("list the directory before Reading a file whose exact name you haven't seen") and exact filenames in dispatch prompts both stand.

### Observation, correlation only: an auditor Write denial, and a next-day allowlist edit

`c47db58d` (started 2026-07-21) ran a `/manager`-orchestrated extraction whose audit run hit `Error: No such tool available: Write. Write exists but is not enabled in this context.` (`c47db58d`, `a6ab4e59fcd1c3ddb`, 136). `c23f52ab` (started 2026-07-22) edits `.claude/agents/auditor.md` from a `disallowedTools` denylist to an explicit `tools` allowlist that includes Write.

That is the whole claim: two facts one day apart. Neither transcript states the link, and what else changed in mycelia's config that day was not examined. The absence after the edit is bounded, though: all 10 corpus Write denials across 6 sessions (`error_signatures --param signature="No such tool available" --param min_occurrences=1`) sit in sessions started before the edit — the same query with `--since 2026-07-22` or `--since 2026-07-23` returns zero rows — and the 177 auditor runs across 16 sessions started since 2026-07-23 accumulated 228 tool errors with no denial among them (`agent_types --since 2026-07-23`). The edit is followed by a bounded absence of the failure. The cause is still correlation, not mechanism; this stays an observation.

## What analysis may consume from enrichment

The store was fully re-enriched at `prompt_version=4, taxonomy_version=2` (`enrichment_coverage`, all rows, enriched 2026-08-14). **All three levels are now cleared**, session level for the first time: QC draw 3 (`select_enrichments --param level=session --param per_category=1 --param seed=qc3`, n=14 covering all fourteen categories) passed its gate — zero command-result errors, one refuted description in fourteen, and the one error is a fabricated *truncation*, not a fabricated completion. The zero-api-call fabrication mechanism iteration 3 flagged is fixed and re-verified. Scorecard: `data/analysis/enrichment_qc_2026_08_13/round2_qc3_session_reader.md`.

Cleared means usable as a **map, not evidence**: enrichment points at where to look; every citable fact still gets verified against the trace. Four standing corrections apply to any enrichment-derived number or quote:

1. Numbers inside description text are pointers — re-check before citing
2. A truncation or "cut off" claim must be checked against the turn's `stop_reason` (`end_turn` refutes it)
3. Filter audit-shaped runs from any `debug`-category count (measured contamination 3 of 6)
4. Session `friction=null` is structurally blind — absence is not evidence of no friction

Correction 4 recurred across reader batches this iteration: `cb76d8e4` (null, 205 tool errors), `f1a1eb9a` (friction text omits its 11-error handoffs pattern), `5a88789c`, `08483117`, and `c068966d` all pair null-or-incomplete friction with real, well-evidenced friction. Also from the readers: `c068966d`'s `outcome=completed` describes its design phase while the session's own state is a GATE-READY park — spot-check outcomes before leaning on them.

One predicate note, because session counts depend on it: `enrichment_coverage` under the mycelia project predicate reads 428 session items, and the predicate is a path prefix, so that count already spans the primary checkout and all three worktree checkouts (`view_projects`: 571 sessions = 568 primary + 3 worktree). `manager-state.md`'s bookkeeping figure of 429 has no verifiable seam through the CLI — no query counts enrichment outside a project predicate — and the only place a 429th could sit is among the 4 sessions with no `project_dir`, which drop out of every project-scoped query. Cite 428, with its predicate.

## Carried-over ledger

Every iteration-3 finding, with its iteration-4 disposition:

- **F1 (`echo ===` separators): holds; not re-examined.** No new sessions exist, so the did-the-fix-land regression test still cannot run. Under the normalized signatures the errors now group under the `Exit code 1` line — phrase-bound count `signature="== not found", min_occurrences=1`: corpus 539 errors / 49 sessions — **not comparable** to iteration 3's 393-window figure (grouping and window both changed; the data did not). The synthesis pass again reproduced the artifact live: a `===` echo separator between two store queries failed in zsh
- **F2: revised** — promoted to Counted and generalized beyond `handoffs/` (above)
- **F3: revised** — option (b) closed for multi-hour waits; a sub-hour variant stays an unpriced hypothesis; the example session shown atypical (above)
- **F4 (Edit/Write-before-Read): holds; not re-verified at mechanism grain.** The corpus error counts match iteration 3's (Edit 621, Write 176) — these signatures carry no path, so normalization could not move them; the session/thread figures beside them (36/253, 38/153) are new default-survey numbers with no iteration-3 counterpart. New instances from readers: all 12 of `f087648e`'s Edit errors are the post-compaction-reset shape; two more in `74dbe7ed`, one in `08483117`. A cousin mechanism is worth separating: Edit "String to replace not found" (corpus 168/49/144) is a guessed-`old_string` miss, not a read-tracking miss — three traced in `c47db58d`
- **F5 (worktree-isolation guardrail): holds, sharpened by normalization.** The 29 hand-aggregated per-worktree rows are now three signature rows: Edit 23/8/19, Write 17/7/13, Bash "too complex to verify" 12/2/7 corpus (not comparable to iteration 3's phrase-bind numbers). New: the blocks concentrate — `8320539c` alone holds 5 of the 12 Bash blocks, and `8c152204` has 8 of its 35 errors isolation-shaped; both are manager-orchestrated multi-agent sessions. Recommendation stands
- **F6: revised** — mechanism confirmed at raw grain, causal story refuted, recommendation withdrawn (above)
- **F7 (fork dispatch, dead-end retrieval paths): holds; not re-examined.** `5a88789c`'s session-level delta adds nothing against it
- **F8 (fanout cap): holds; not re-examined**
- **F9: revised** — reframed on the corpus recovery split (above)
- **F10 (anecdotes): not re-examined.** `0a527620` is fingerprint-identical to its iteration-3 read; `cdedfb8f`'s enrichment corroborates "six audit rounds" without adding mechanism
- **F11 (skills stratum): holds, with small additions.** Three minor previously unflagged errors — two in `74dbe7ed`, one in `08483117`; the `c068966d` outcome caveat above. One clean counter-example worth having on file: `d07af752` waits on CI with a background `ScheduleWakeup` instead of foreground polling
- **C4: rewritten** per the F6 verification — the compaction tail is large single-dispatch implementer threads
- **C6 (report-file Write block): still blocked on data** — the regression test needs post-fix sessions that don't exist
- **A13 (manager scratchpad staleness): holds at 2 sessions** — `cb76d8e4`'s delta read added nothing
- **R8 (classifier denials): holds; counts re-measured, not re-examined.** The normalized signatures split the rows by reason text: corpus Bash 49/17/27 plus 13/8/12 (destruction-reason) and a small tail, Edit 9/4/4 (`error_signatures --param signature="auto mode classifier" --param min_occurrences=1`) — not comparable to iteration 3's window-70 figure. Benign-vs-justified still rests on read sessions
- **A12, A14, C1, C2, R5–R7, R10: not re-examined** — no new sessions exist to move them

## Recommendations, and their status

Nothing in this report is applied; mycelia edits remain blocked on dispatch, as they were in iteration 3 — the accepted iteration-2 F1 edit has still not landed, and with the corpus paused, no fix can register in data until mycelia sessions resume. Standing after this iteration: F1 (ban bare `===` separators), F2 broadened (worktree-reachable gitignored directories, scoped by what `path_failures` names), F4 (Read-before-Edit guidance for Bash-inspected files), F5 (recovery rule in worktree-isolated spawn prompts), F9 strengthened (list before Reading a guessed name; exact filenames in dispatch prompts), C6 (spawn-prompt fix). Dropped this iteration: F6's fresh-run-per-item, and F3's keep-warm heartbeat for the multi-hour waits it targeted (a scoped sub-hour variant stays an unpriced hypothesis, not a recommendation).

## Process review

Per the checklist in `docs/analysis.md`.

**The iteration's shape.** Delta reads over a static corpus, plus two dedicated verification passes. Verification earned its cost: it refuted two of F6's three load-bearing claims and reversed F3's open recommendation. The F6 failure traces to iteration 3's own flagged caveat — a reader who worked at query grain and opened no raw records — which this iteration confirms as a real hazard, not a formality.

**Strata.** Cost deltas produced F2's promotion (`f1a1eb9a`, unread for this in three iterations); tool-errors deltas confirmed F2 and F5 at raw grain; the one fresh compactions read (`8c152204`) generalized F2 to `docs/adrs`; the skill batch mostly fed the enrichment-trust section. Discovery, under the new `min_discovery_api_calls=10` floor, drew 8 substantive sessions instead of iteration 3's half-degenerate set (7 read by one reader, `17e0f606` by another) — 5 of 8 ran error-free, and the stratum still produced the allowlist observation. The floor did its job.

**Queries.** `idle_gaps` passed a full internal consistency check on arrival (reloaded counts match `context_reloads` at both grains, 627/627 and 383/383; the 21-gap `cached_1h` figure matches the header note). The signature normalization is a one-time comparability break — flagged wherever a count sits near an iteration-3 number. Two traps surfaced: `agent_compactions` at `agent_type` grain measures the naming convention as much as the behavior (a single-use name yields a denominator of 1), and `path_failures` is directory-keyed, so its buckets mix mechanisms. One gap: no library query lists threads by compaction count with volume — the F6 verification needed a three-step manual join; a `thread_compactions` query would have caught the confound in iteration 3.

**Reader context.** Four readers, all inside the digests: light per-session digests plus 2 / 4 / 2 / ~9 `records_slice` calls at 180–2,000-char caps. The delta protocol kept re-reads cheap; the one fingerprint-matched skip (`0a527620`) cost nothing and lost nothing.

**For iteration 5.**

1. Extract when mycelia sessions resume, then run the F1 and C6 regression tests already defined — nothing can move until then
2. Add a `thread_compactions` query (per-thread compaction counts beside volume), per the F6 verification
3. Run the missing control — idle share on single-dispatch hot threads — before re-claiming a third reload mechanism
4. Split reload cost by gap duration (an `idle_gaps` companion or column) — that is the number the narrowed sub-hour keep-warm hypothesis waits on
5. If F2's 45/71-error counts are used as clean mechanism counts, spot-check the bucket mix first (`path_failures` is directory-keyed)
6. Consider widening `_command_results`' carrier match so bare-prompt `/compact` turns get a command block (QC draw 3's one non-blocking follow-up)
