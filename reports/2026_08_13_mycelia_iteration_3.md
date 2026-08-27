# 2026-08-13 — mycelia: agent friction, iteration 3 (full re-selection)

Successor to [the 2026-08-09 report](2026_08_09_mycelia_agent_friction.md), asking the same question: where do Claude Code sessions on mycelia waste spend, context, or wall time, and which losses would a change to mycelia's guidance, agent definitions, or config remove?

This is the full stratified re-selection iteration 2 deferred. One premise correction up front: the raw corpus did **not** grow since 2026-08-09 — the refresh extracted the same 4 still-open sessions and left 571 unchanged, and zero sessions start on or after 2026-08-08. What changed is the trailing window (early-July sessions aged out) and a corpus-wide enrichment pass completing on 2026-08-13. The enrichment completion and the deferred re-selection are the triggers, not new data.

## Corpus and scope

- Store stamp: 571 query-visible sessions, corpus cost $16,108.17 (identical to the cent to 2026-08-09), `max(sessions.started_at)` 2026-08-07 11:47 UTC (`data/analysis/2026_08_13/stamp.txt`)
- Analysis window: trailing 28 days at `as_of=2026-08-13` — 201 sessions, $9,982.80 (`session_counts.sql`). Down from 256 / $11,109.87 at 2026-08-09 purely because the window rolled forward over nothing; no count delta below is a trend
- Every count comes from `src/hyphae/analyze/queries/<name>.sql` with `project=/Users/nob/repos/mycelia, since=NULL, as_of=2026-08-13, window_days=28`; non-default `--param` bindings stated inline with each count
- Read sample: 30 sessions from `select_sessions` (realized composition: cost 7, tool-errors 5, compactions 4, skill 6, discovery 8 — `cb76d8e4`, rank 2 by cost, was excluded as deep-read in iteration 2 and unchanged since) plus 26 `select_runs` picks spanning 6 run-only sessions, read by 11 readers. Reports in gitignored `data/analysis/2026_08_13/{sessions,runs}/`; selection and headlines in `roster.md`

**Scope.** This corpus is one person's Claude Code sessions on one codebase. Every finding is evidence about mycelia's guidance and configuration, not about Claude Code or coding agents in general. Citations are `(session_id, source, first_line–last_line)`.

## Findings, ranked by actionability for mycelia

### F1. `echo ===` separators still fail five times in six — the iteration-2 fix never landed (Counted; closes C5)

`command_failures --param mentions="echo ===" --param min_occurrences=1`: of 487 window Bash calls whose command line contains `echo ===`, 408 (84%) returned as errors — statistically unchanged from iteration 2's 435/522 (83%). `error_signatures --param signature="== not found" --param min_occurrences=1`: 393 window errors across 30 sessions and 200 threads. The proposed ban on bare `===` separators was never applied to mycelia's Bash guidance.

This iteration also resolved a reader contradiction. R-err1 found the shape nearly absent in its three sessions (3 of 85 errors in `26dfe608`); R-err2 found it dominant in its two (~22 of 52 in `24bfe69f`). Both are right locally — the idiom's per-session share depends on how discovery-probe-heavy the session's dispatches are — and the corpus query is what governs: 30 window sessions carry it. (The synthesis pass itself reproduced the artifact: chaining `echo ===` between two store queries in zsh flipped both to errors and swallowed the second's output.)

Recommendation (unchanged, now with a lapse to explain): land the guidance edit — ban bare `===`/`====` echo separators in mycelia's Bash conventions, prescribe `echo ---` or `printf`, and add `|| true` on optional trailing probe steps. Also worth asking why an accepted iteration-2 recommendation didn't land; nothing in the corpus can answer that.

### F2. Spawned agents are pointed at gitignored `handoffs/` and `issues-open/` paths their worktree doesn't contain (Recurring)

Four independent tool-error-stratum sessions show the same mechanism: the manager spawns an agent into a worktree, and its prompt references `handoffs/...md` (or `issues-open/`) — gitignored directories that exist only in the primary checkout. The agent's first reads fail:

- `26dfe608`: 20 of 85 errors; a run's own thinking names the cause — "The handoff file might be in the primary checkout rather than this worktree, possibly because the handoffs directory is gitignored as a scratch space" (`26dfe608`, `a961c52021ab5bfd9`, 12–16)
- `0db9e51a`: 9+ of 77 errors, same shape
- `24bfe69f`: 12 of 52 errors, including a dispatch prompt that literally instructs the run to read two `handoffs/*.md` paths that then 404 (`24bfe69f`, `a94f469a360fed62b`, 11–12)
- `8320539c`: one instance, reading another agent's worktree copy (`8320539c`, `a00b0aab844a0121f`, 17)

No corpus query can count this cleanly — the error text is a bare "File does not exist" with the path outside the signature — so it stays at Recurring on four session reports. Recommendation: make `handoffs/` (and `issues-open/`) reachable from spawned worktrees — copy or symlink them at worktree creation — or have the manager inline the handoff content in the spawn prompt. This is the largest single error class in two of the five error-stratum sessions.

### F3. Coordinator idle-wait reloads cost 10% of affected-thread spend (Counted; promotes A11, restated)

`context_reloads` (trailing window, corpus row): 605 reloads across 352 threads in 45 sessions; 449 (74%) follow an idle gap long enough to expire the cache, 16 follow a compaction; reload cost $560.23 against $5,503.28 total cost on affected threads (10.2%), or 5.6% of window spend.

The mechanism was confirmed qualitatively in the corpus's densest idle-reload thread (`4c0c9e8e` main: 13 reloads, 12 idle, $22.52 of $79.05 = 28.5%). All 6 sampled reload calls share one shape: the coordinator idles 1–4.75 hours mid-turn waiting on async work — a quota reset, a subagent, CI — and its next action pays a full cold rebuild (`cache_read=0`). Only 2 of 6 were SendMessage continuations into an open run; 4 of 6 were fresh `Agent` spawns (`4c0c9e8e`, main, api_call idx 45, 117 vs 61, 71, 132, 144). So A11 is promoted **restated**: the unit is not "SendMessage continuation" but "coordinator idle-wait" — any next action after a long wait reloads, continuation or fresh dispatch alike.

Recommendation, scoped to mycelia's overnight manager/coordinator pattern: iteration 2's "prefer fresh dispatch over continuation" would not remove this cost — fresh dispatches reload too. The honest options are (a) accept it as the price of multi-hour async waits (~5.6% of window spend), or (b) cost out a keep-warm heartbeat during known long waits before recommending it. No query today prices option (b).

### F4. Edit/Write-before-Read: 365 window errors, at least three distinct mechanisms (Counted; extends C3)

`error_signatures --param signature="File has not been read yet" --param min_occurrences=1`: corpus 797 errors (Edit 621 + Write 176) across 38 sessions; trailing window 365 (Edit 277/17 sessions, Write 88/26 sessions). The signature is identical across mechanisms, so their shares can't be split by query; the readers traced three:

- **Inspect-via-Bash, then Edit.** The agent reads a file with `grep`/`sed -n`, then Edits it; the read-tracking never saw a `Read` (`17af721e`, `a4a67d025e1e871fc`, 130–150). 83 of `17af721e`'s 171 errors are this signature, concentrated in its priciest implementer runs
- **Post-compaction read-state reset.** Compaction clears the tracked read state; the next Edit on an already-read file fails once, then self-corrects (21 of 85 errors in `26dfe608`, each within minutes of a compaction boundary — `26dfe608`, `afa965bcc1b7a691f`, 236–950 with `view_compactions`; also `8320539c`, `af281e64eedc97644`, 211, a Write block 108s after auto-compaction)
- **Long idle gap inside one turn** — one sighting after ~10.5h overnight (`5f4b59fb`, `af960c322c5ee58f7`, 185–188)

Recommendation: mycelia's implementer-type guidance should say "if you inspected a file only through Bash, `Read` it before editing" — that targets the largest traced concentration. The post-compaction variant is harness behavior costing one round-trip; mycelia's existing `post-compact-orient` hook is the natural place for a "re-Read before your next Edit" reminder, but each instance is cheap — fix the first mechanism first.

### F5. Worktree-isolation guardrail: 37 window Write blocks, and the wrong recovery is expensive (Counted mechanism, anecdotal blast radius)

`error_signatures --param signature="isolated in the worktree" --param min_occurrences=1`: 37 window Write blocks across 29 distinct agent worktrees (corpus 54 — mostly in-window, so recent), plus 13 window Bash blocks "too complex to verify that it stays inside the worktree" (8 threads). In `8320539c` this is the largest error class (9 of 45). The costly case: a doc-writer blocked on a shared-checkout Edit didn't retry with its worktree path — it spent ~7 minutes doing git surgery in the shared checkout, hitting a real merge conflict with another task's in-flight state before recovering (`e4003d83`, `a8920c4c35f380316`, 66–99).

The guardrail message already names the agent's worktree path; what's missing is the next move. Recommendation: mycelia's spawn prompts for worktree-isolated agents should state the worktree path up front and the recovery rule — "on an isolation block, retry the same edit at `<worktree>/<path>`; never operate on the shared checkout" — and keep Bash simple while isolated.

### F6. Coordinators re-task a live teammate thread across unrelated work — 7–9 compactions per thread vs a 1.1 baseline (Counted rates, 2-session mechanism)

`agent_compactions` (trailing window): `impl-rung1` 9.0 and `impl-cards` 7.0 compactions/thread against the implementer baseline of 1.1. Both are ad-hoc single-use definitions a `/coordinator` kept alive as a "teammate," re-tasked across sequential unrelated items (rung 1 → rung 2 → rung 3; PR3 → PR5 → PR6b) via `<teammate-message>` dispatches, auto-compacting every 15–30 minutes at ~155–168k tokens (`runs/aimpl-rung1-07b89bf51437c28f.md`, `runs/aimpl-cards-daba065d0bb4f8a2.md`). Single-task runs in the same sessions compact 0–1 times, so the mechanism, not task size, is the delta. Contrast sessions `d6563816` and `f087648e`: ordinary ~1-per-run compactions on single-task dispatches, no structural problem — which also scopes C4.

These threads' reloads are a **third** mechanism, distinct from F3: `context_reloads` thread grain shows 2/8 and 2/5 idle (vs 74% corpus) and 0 compaction-linked — busy-thread cache churn inside 45–154-call turns, not idle-wait. Caveat: R-compact worked at query/view grain and opened no raw records; the dispatch-message mechanism rests on `run_digest` text, not transcript slices. Recommendation, scoped to the `/coordinator` workflow: dispatch a fresh run per work item instead of re-tasking an open thread; two sessions (plus unread `impl-unbox`, flagged) show the pattern, so verify against the transcript before hard-coding the rule.

### F7. Two dispatch bugs with counted footprints: identical prompts to sibling forks, and dead-end prescribed retrieval paths

- **Fork dispatch.** An auditor sent the same full 3-branch prompt to 3 sibling forks meant to each own one branch; this fork tried to nested-fork the others — refused, "Fork is not available inside a forked worker": corpus 11 errors / 3 sessions / 5 threads (`error_signatures`) — then redundantly audited all 3 branches (`5a88789c`, `a61a059e3610e6fb4`, 1, 47–52, 165). Recommendation: scope each fork's prompt to its assigned branch; if cross-checking is wanted, declare it
- **Prescribed dead ends.** A research brief prescribed 4 Reddit-retrieval methods, all hard-blocked in this environment — "unable to fetch from" fires 27 times corpus-wide on reddit.com/old.reddit.com/web.archive.org/teddit.net (`error_signatures --param signature="unable to fetch from"`). The agent burned 8+ errors on the script before improvising a working Wayback-CDX-plus-curl method (`313a1013`, `a2d8ba9bd744c1d73`, 261). Recommendation: research briefs should prescribe the method that works here and name the known-blocked domains

### F8. Two-level fanout silently narrows its own coverage (Counted cap, anecdotal rationalization)

"Concurrent subagent limit reached. You can run 20 subagents at once. Do not retry": 31 errors / 3 sessions / 14 threads, all in-window (`error_signatures`). In `9ae3ae11` (7 leads × opus workers), 11 cap hits across 6 leads — no lead queued or retried a blocked spawn, so planned sub-audits never ran. One lead also lost 2 workers to silent reaping (`TaskStop` → "No task found") and rationalized the gap away — "my own foreground reads covered their ground" — without verifying (`9ae3ae11`, `a4a65e6075f124bf7`, 114–118). Recommendation: fanout briefs should size first-level breadth to leave headroom under the cap, and require leads to report blocked or lost workers as coverage gaps rather than absorbing them.

### F9. Guessing filenames instead of listing the directory (Recurring)

Four independent session reports, same shape — a Read on a guessed name 404s, then an `ls` finds the real one: screenshots in a shared worktree where the agent itself worried about a concurrent-writer race (`4b613b5a`, main, 1188–1195), a doc-writer guessing ADR and handoff names three times running (`2f3e6be5`, `ae322669d5230abb0`, 14–27), an ADR guess from an underspecified dispatch prompt (`4208c1bd`, `ae1b602e23653301f`, 11–19), an issue-file guess (`08483117`, main, 378–384). No query counts "404 followed by ls" today. Recommendation: a one-liner in mycelia's agent guidance — list the directory before Reading a file whose exact name you haven't seen — and give dispatch prompts exact filenames.

### F10. Anecdotes worth a targeted fix

- **Quota-reset resume message falsely claims "worktree intact."** Both parallel implementer lanes of `0a527620` were resumed with "Your worktree and any progress in it are intact — re-orient from your worktree's git status if needed"; both found the worktree gone and rebuilt from scratch, losing pre-quota progress (`0a527620`, `a3f7376ac0f48352e`, 80–109; `a8dc890b7fc92d39b`, 79–89). One session, two lanes — the resume template should verify before asserting, or say "may not exist"
- **Audit-fix-reaudit loop where the auditor's fix was the regression.** `cdedfb8f`'s "C3" workstream ran 6+ audit rounds, ~$150 of $529: round 3's prescribed semantic widening was itself the defect rounds 4–5 caught and reverted (`cdedfb8f`, `a7d42a5669ef824ae`, 1). Candidate rule, one session's evidence: when an audit's prescribed fix changes what a boundary case *means*, route it through design review, not apply-and-re-audit
- **Honeycomb MCP grant failure**, bounded: 11 corpus errors, all one session — a `honeycomb` agent run lost all 10 of its MCP calls to an OAuth grant error and correctly reported itself blocked (`0ba68577`, `acccfdd07b854efa3`, 38–43). Re-authorize the server; cheap fix, session-local evidence

### F11. The skills stratum is largely clean — a bounded null

All six sampled skills did what their text says: grill-me adapted correctly to unattended single-shot use (`4208c1bd`), manager ran Deep-tier routing end-to-end to a correct GATE-READY park (`c068966d`), merge-stack survived a real conflict plus a mid-merge main-move (`6562b226`), pr-and-document's fresh-eyes pass caught two real errors pre-ship (`17e0f606`), writing fired exactly where `docs/pull-requests.md` prescribes (`74dbe7ed`). This is sample-only (one session per skill), not a corpus count. Three real items:

- **handoff skill vs repo convention.** The global skill says save to the OS temp dir; mycelia's `docs/handoffs.md` says `handoffs/` at repo root. `08483117` followed the temp-dir habit without consulting the repo doc ("consistent with the previous run" — `08483117`, main, 358), while `c068966d` used the repo convention. Two agent-memory notes record downstream agents looking in the wrong place. Fix: the skill (or its dispatcher) should ask which convention applies
- **Skill attribution lands one hop down.** A run told to "invoke the pr-and-document skill" shows no Skill call in its own transcript; attribution appears on its doc-writer sub-run (`17e0f606`, `a6116a617f7f174c0`, 1, 230). "Did the skill fire" is unanswerable at the dispatched run's grain — a measurement gap for any future skill-compliance count
- **Auto-mode classifier denials now include `.claude/agents/*.md` edits.** Window total 70 denials (Bash 49/17 sessions; Edit 9/4 sessions — `error_signatures --param signature="auto mode classifier"`). `74dbe7ed` had two Edit denials on agent-definition files, worked around via a Bash heredoc rewrite (`74dbe7ed`, main, 1188–1199). If the block is expected, document the workaround; benign-vs-justified still rests on read sessions

One sample-only observation, carefully scoped: in the three read `/manager` cost-stratum sessions, implementer+auditor carry ~80–85% of both cost and errors (`f1a1eb9a`, `4c0c9e8e`, `8ee00a94`, each via `view_runs`). That is where the work is; nothing read shows it is waste. It stays an observation, not a finding.

## Carried-over findings and hypotheses

- **A11: promoted to Counted and restated** as coordinator idle-wait reload (F3)
- **C5: closed as F1** — denominator re-confirmed, fix confirmed not landed
- **C3: extended by F4** — mechanisms now traced, shares unbounded
- **C4: scoped by F6** — the compaction tail is re-tasked teammate threads; ordinary single-task runs sit near baseline
- **C6 (report-file Write block): blocked on data.** Corpus 43/8/39 unchanged; window fell to 18/6/14 purely because the window rolled. The iteration-2 regression test — window count falling toward zero after the spawn-prompt fix — cannot run until post-fix sessions exist. C6-signature errors still appeared in this iteration's reads (`0ba68577`, 7 of 58; `8ee00a94`, `aaf1c6fcb367d68fa`, 823)
- **A13 (manager scratchpad staleness): second session.** `0a527620` main, 197–198 repeats the stale-Edit shape from `cb76d8e4`. Two sessions — one short of recurring
- **A12, A14, C1, C2, R5–R7, R10: untouched** — no new sessions exist to move them
- **R8 (classifier denials): updated in F11**

## What analysis may consume from enrichment

Per the QC scorecards in `data/analysis/enrichment_qc_2026_08_13/`:

- **agent_run level: cleared, with corrections** (16 items, 2 seeds, all 14 categories). Corrections to apply: `outcome` can invert (one `partial`-graded run actually completed cleanly — spot-check outcomes before any failure-rate count); the `debug` category is contaminated by `review`-shaped adversarial audits (2 of 2 sampled); friction notes under-report minor real tool errors (4 of 16 said "none" over ≥1 actual error), so friction absence is not evidence of a clean run; one description silently narrowed a 3-item task to 2
- **turn level: cleared, with corrections** (9 items). Descriptions grounded in 8 of 9; treat numbers inside descriptions as pointers to re-check, never citable evidence (the one real error was a fabricated metric); command-only turns route inconsistently across `other`/`chat`/`configure`, so merge or re-check those strata in any category census. The sample contained almost no non-completed turns, so false-`completed` rates are unbounded
- **session level: verified and NOT cleared** (8 items, re-verified after the first pass's agent was lost). Axis scores (correct/arguable/wrong): description 5/1/2, category 4/4/0, outcome 4/3/1, friction 6/0/2. Both prior-pass hypotheses were confirmed as fabrications, and a third instance turned up unprompted: the shared mechanism is that a turn with **no model response** — a slash command, a session with 0 api_calls — gets described as if the model had acted (an invented "attempt," "clarification," or "unrecognized command"). Both zero-api-call sessions in the sample scored wrong descriptions; only 1 of the 6 sessions with a real model turn did. So `api_calls = 0` at session grain is a usable flag: verify before citing, and don't trust session-grain friction without that check. Category adds a `design`/`implement` boundary problem; outcome on single-call sessions needs a `stop_reason` sanity check

## Process review

Per the checklist in `docs/analysis.md`.

**Premise correction.** The iteration was framed as "the window turned over"; in fact the corpus is static and the window shrank over nothing. The actual trigger was enrichment completing. Future roster documents should state the trigger from the stamp, not assume growth.

**Strata.** Cost corroborated (F3's mechanism, F4's biggest concentration, F10's audit loop); tool-errors was the most productive stratum (F1, F2, F5, and F4's compaction variant all came from it); compactions answered its must-read question (F6) and produced two useful nulls; skill produced a bounded null plus three real items; discovery was half degenerate — 4 of 8 draws were sub-$1.50 near-empty sessions, and the other 4 produced F8, F10's resume-message item, and a stale-file anecdote. The run draw earned its slot: both F7 mechanisms came from `select_runs` picks no session stratum reached.

**Error stratum can top on noise.** `select_runs`' top-error pr-submitter run had 4 trivial self-recovered slips and two green PRs (`1ae6e5f6`, `a5d0ae93c86927be3`) — error count alone is a weak ranking signal at the run grain.

**Failed corroboration / stayed anecdote.** The quota-reset false "intact" claim (one session), the audit-loop rule (one session), teammate re-tasking's dispatch mechanism (two sessions, query-grain reading), the Honeycomb grant failure (one session). Nothing was promoted on read evidence alone.

**Reader context.** All 11 readers reported staying inside the digests; typical spend was light digests plus 1–35 `records_slice` calls at 150–4,000-char caps. R-compact opened no raw records at all — flagged above as a caveat on F6, and a reminder that query-grain reading buys breadth at the cost of mechanism confidence.

**Queries.** `context_reloads`, new this iteration, did exactly what iteration 2 asked for — it promoted A11 by itself. Two library irritations: `error_signatures` puts the worktree path inside the isolation-guardrail signature, splitting one mechanism across 29 one-row signatures (aggregated by hand for F5 — a normalization candidate); and `command_failures` denominators still need `--param min_occurrences=1`. No clean query exists for F2 (path is outside the signature) or F9 ("404 then ls") — both are candidates if those findings need corpus counts.

**For iteration 4.**

1. Give `select_sessions`' discovery stratum a min-substance floor (e.g. minimum tool calls or cost) so degenerate turns stop consuming read slots
2. Fix the session-level enrichment fabrication at the pipeline (hyphae-side, not mycelia): skip or special-case sessions and turns with 0 api_calls so the enrichment model is never asked to describe a model action that never happened — that one gate covers all three verified fabrications. Session enrichment stays unusable until it lands and a fresh QC pass clears it
3. Land the F1 guidance edit and the C6 spawn-prompt fix, then re-check both window counts once post-fix sessions exist — the regression tests are already defined
4. If F6's rule is adopted, verify the teammate-dispatch mechanism against raw transcript slices first
5. A `context_reloads` companion that prices a keep-warm heartbeat would settle F3's option (b)
