# Testing plan: the mycelia analysis process

Obligations for `plans/mycelia-analysis/design.md` (audit verdict CLEAR). Every leaf is an obligation; the *Evidence:* clause names the artifact that discharges it, and an auditor traces each leaf to that artifact.

This design is process plus light tooling. The **tooling** — the `query` subcommand and the `.sql` library — carries automated obligations. The **process** — reading protocol, templates, synthesis rules — carries a short inspection checklist, because its subject is a human and a subagent following a document, and a test that asserts a template's text proves only that the text exists.

Three rules shape everything below.

- Fixtures are **redacted excerpts of real sessions** under `tests/fixtures/`, with the Claude Code version in each directory's README (`.claude/rules/testing.md`). Every string outside the structural keep-list is redacted, so no leaf asserts on transcript text or its length. Leaves whose data is invented or planted say so
- **No leaf reads `data/traces.duckdb`.** It is gitignored, machine-local, and grows daily. The design's corpus numbers (575 sessions, ≤31 selected, $15,069) are claims each iteration re-derives and cites; the tests pin mechanics, not the corpus
- **Every parameter is bound, and no query reads the clock.** The design puts production defaults in the runner's per-query manifest and makes anything time-relative ride `$as_of`, so a test binds fixture-sized values against a frozen store while a bare invocation runs the production numbers

Fixture-store facts below were measured on 2026-08-07 by extracting every `tests/fixtures/*/` transcript into a temp store. Re-measure at implementation.

**Selection tests bind `$as_of = 2026-07-28`.** That opens the 28-day window at 2026-06-30, seven hours before the earliest fixture session, so all 13 mycelia sessions are in the window and the pool is 10. The runner's default of today would leave a window that empties as the calendar moves.

## Scaffolding

`build_store(path, directories)` in `tests/enrich/conftest.py` already does exactly what this tier needs: extract fixture transcripts into a real DuckDB the way `refresh()` would. **Promote it to `tests/conftest.py`** rather than copy it — two tiers now depend on it. The analyze tier's store is session-scoped and read-only; tests that plant rows copy it, as `mutable_db` does.

## The fixture corpus

Sixteen of the fixture transcripts export cleanly; the six under `invented/` that carry unknown record shapes crash by design and stay out of the store. What the sixteen give this design:

| Shape the design needs | What carries it |
| --- | --- |
| Corpus predicate: in, out, and NULL | 13 sessions at `/Users/nob/repos/mycelia`, one at `/repo`, one at `/invented/project`, and `fork_byref`'s `07a769d7…` with **NULL `project_dir` and NULL `started_at`** — the recorded twin of the store's 4 bookkeeping stubs |
| Cost ranking with a real collapse | in pool order: `4208c1bd…` $2.83, `088d63aa…` $2.28, `10d0349d…` $1.62, `2352492b…` $1.48, `5a88789c…` $0.90, then five below $0.52 |
| Tool errors, and a metric that runs out | exactly 2 pool sessions have any error tool call — `088d63aa…` (#2 by cost) and `5a88789c…` (#5). The other 8 have zero, so an error quota of 3 must stop short |
| Compactions, and a pure walk-down | exactly 2 pool sessions compacted — `2352492b…` (#4 by cost) and `registry-zoo…` (#9). A cost quota of 4 takes the first, so the compaction stratum must walk to the second |
| Skills across pool sessions | `grill-me` 2 sessions/2 calls, `pr-and-document` 1 session/4 calls, `deep-research` and `night-run` 1 session each. **`grill-me`'s two users are the top two by cost**, so a cost quota of 2 collapses the skill entirely |
| Unattributed api calls | 10 calls with NULL `turn_id`. `0a76f771…` (the resume) carries **5 of them holding all $2.39 of its live cost** — a digest that drops them reports $0 against a front matter of $2.39 |
| Agent runs for `select_runs` and `run_digest` | 7 runs across 7 distinct `agent_type`s, including a parent/leaf pair in `spine/` |
| Pool exclusion | 4 sessions with zero turns and zero runs in `corpus_rollups`, 3 of them mycelia — 13 in-window sessions give a pool of 10 |
| Two windows over one store | `$as_of` 2026-07-28 gives 13 in-window sessions and a pool of 10; `$as_of` 2026-08-07 gives 6 and 5 |
| ISO weeks | 5 weeks, 2026-W27 … 2026-W31, unevenly filled (4, 4, 3, 1, 1) |
| A record past the `records_slice` cap | one `raw_records` row of 3,054 chars in `0a76f771…` |

Two shapes no fixture holds, planted onto real rows and labeled at the call site:

- **A worktree `project_dir`.** No recorded fixture sits under `<project>/.claude/worktrees/`. Re-export a real trace with `project_dir` replaced, as `test_a_rollup_can_be_scoped_to_one_project` already does for `/repos/other`. The column value is invented; the rest of the session is recorded
- **A prompt past 300 chars.** The longest fixture prompt is 145 chars after redaction, and asserting on redacted text length proves nothing. Plant a unique sentinel into one real turn row of a copied store

No new recorded fixture is needed. With the quotas and the skill threshold now bound parameters, every selection obligation is reachable on this corpus at fixture-sized bindings.

---

## integration (runner) — `tests/analyze/test_query.py`

The CLI driven through `cli.main("query", …)` against a fixture-built store on `tmp_path`, `capsys` splitting stdout from stderr. Nothing is mocked; the store is a real DuckDB file.

- `--project` selects the project's sessions and no other project's. *Evidence:* the fixture store holds 13 mycelia sessions beside `/repo` and `/invented/project`; assert the returned session ids are exactly the 13.
- **A session under `<project>/.claude/worktrees/` is in the corpus, and a sibling sharing the prefix without a `/` is not.** *Evidence:* two re-exported real traces with planted `project_dir`s — one worktree child, one `/Users/nob/repos/mycelia-old`; assert the child is in the result and the sibling is not. Bolded: `starts_with(project_dir, $project)` without the `/` silently annexes every neighbouring checkout, and the failure is a wrong number, not an error.
- A trailing slash on `--project` gives an identical result set. *Evidence:* the same query run with and without the slash; assert the row lists compare equal.
- **The excluded-session count goes to stderr, and `--csv` stdout stays machine-readable.** *Evidence:* `fork_byref`'s recorded NULL-`project_dir` session; assert stderr reports 1 excluded and that `csv.reader` over stdout yields the header plus exactly the data rows, with no commentary line. Bolded: this is the one contract a piped analysis depends on, and prose on stdout breaks it silently.
- **The citation names the query file and every resolved binding, as a header of table output and on stderr under `--csv`.** *Evidence:* assert the header names `queries/<name>.sql` and each resolved `k=v` pair including `$as_of`; under `--csv` assert the same line is on stderr and absent from stdout. Bolded: this line is the whole mechanism behind "a claim carries its query", and it must show the *resolved* value — a citation naming `$as_of` without its date cannot be rebound by a reader.
- A bare invocation runs the manifest's production defaults, and an explicit `--param` overrides one without disturbing the rest. *Evidence:* run `select_sessions` with no `--param` and assert the citation reports the production quotas; run it again overriding one quota and assert only that binding changed. **As built,** discharged against `records_slice`'s `$max_chars`, since selection lands in the next slice; the quota pin below still owes its own leaf.
- `--since` binds and filters, and omitting it means the full corpus. *Evidence:* `--since 2026-07-15` over the fixture store returns the 5 mycelia sessions started on or after that date; the same query with no `--since` returns 13.
- An unknown query name, or a `--param` the query does not declare, exits with a message naming it. *Evidence:* assert `SystemExit` and that the message contains the offending name — a silently ignored `--param` produces a plausible wrong number and no signal.
- A store stamped with another schema version is refused, and the message sends the reader to the store guide. *Evidence:* a copy of the fixture store with `meta.schema_version` decremented; assert the run exits naming `docs/store.md`. Added after the tier landed: nothing exercised the runner's schema check, and its remedy read "delete the database and re-extract" — advice that can destroy the only copy of a session Claude Code has pruned.
- **The store is opened read-only.** *Evidence:* a query file written into `tmp_path` containing DDL; assert the run raises and that the store's table list is unchanged. Bolded: the design puts the analysis layer out of the mutation business by construction, and read-only is the whole mechanism.

## integration (library smoke) — `tests/analyze/test_queries.py`

Every shipped `.sql` executed against the same fixture store, driven through the runner's per-query manifest with fixture-sized overrides where a production default returns nothing on 16 sessions.

- **Every `queries/*.sql` executes and returns a result set.** *Evidence:* parametrize over `glob("*.sql")` and assert each run completes; an empty result passes, an exception fails. This is what catches a query broken by a schema bump, and it is the reason `_TABLES`-style discovery beats an enumerated list.
- A query shipped without a manifest entry fails the test rather than being skipped. *Evidence:* assert the manifest's key set equals the globbed file-stem set, so the failure names the missing entry — the manifest is production code now, and a query with no defaults is unrunnable from the command line.
- Every parameter a query declares has a manifest entry — a default or an explicit required marker — and every manifest entry names a parameter the query declares. *Evidence:* parse each `.sql` for its `$name` references and compare with its manifest entry; a stale entry silently does nothing and a missing one crashes only for the reader who ran it. (The required marker is finding B.)
- A query the manifest marks cross-session reads only `corpus_*` views, and any query selecting `cost_usd` also selects `unpriced_api_calls`. *Evidence:* static read of each `.sql` text; assert the column pairing and that no cross-session query names a `live_*` view or a base table. Puts the rule the `export/duckdb.py` docstring owns under a check instead of under review.
- No query file references `current_date`, `now()`, or `today()`. *Evidence:* static read of every `.sql`; assert none matches. A clock-reading query passes on a fixture store today and returns nothing next month (finding 2).
- **As built,** the first leaf is stricter than "an empty result passes": it runs each query with `--csv` and asserts at least one data row, naming `FIXTURE_BINDINGS` in the failure. Every shipped query clears it on the 16-session store, and the one that could not — `co_occurrence` at its production floor of 3 sessions — states a fixture-sized floor there. A query that returns nothing runs green while asking its question of no data, which is the shape this tier exists to catch. Reverting is a one-line change if a legitimately empty query ever has to ship.
- **As built,** the corpus-scope leaf accepts either relation the runner materializes. `session_period` is a view over `project_sessions` that puts each in-window session in the corpus group and the trailing-window group, so a count groups by window instead of filtering by it; a query reading only the view is still scoped to `--project`. The leaf reads the tuple `runner.CORPUS_RELATIONS` rather than a literal, so adding a third relation cannot leave the check testing a stale name.

## integration (selection) — `tests/analyze/test_select.py`

`select_sessions.sql` and `select_runs.sql` through the CLI, with the quotas, the skill threshold, the seed, and `$as_of` bound to fixture-sized values. Bindings are stated per leaf; `$as_of = 2026-07-28` throughout, giving a pool of 10.

- **The same store and the same bindings give an identical set and identical stratum tags, however the store was built.** *Evidence:* run twice on one store, and once on a store `build_store` produced with the fixture directories in reversed order; assert the `(session_id, stratum)` lists are equal all three times. Bolded: the reversed-order build is what kills a latent dependence on insertion order, and reproducibility is the entire claim the selection makes.
- **A session an earlier stratum took does not consume a later stratum's quota; the later stratum walks down to its next unselected session.** *Evidence:* cost quota 4 takes `2352492b…`, one of only two sessions that compacted; with compaction quota 1, assert the compaction stratum holds `registry-zoo…` and not `2352492b…`, and that its quota is met. Bolded: without the walk-down the read set collapses to the same few monster sessions, which is the reason the strata exist.
- **A ranked stratum takes only nonzero-metric sessions and stops short when the metric runs out.** *Evidence:* only `088d63aa…` and `5a88789c…` have any error tool call; with error quota 3 and a cost quota of 0, assert the error stratum holds exactly those two and that no zero-error session carries the `tool-errors` tag. Bolded: the tag is what the report's realized composition is built from, and a stratum that pads to quota makes every count drawn from those tags a lie.
- An unused ranked slot passes to the discovery quota, bounded by what is left in the pool. *Evidence:* error quota 3 leaves one slot unused; with discovery quota 2 assert discovery returns 3 and the total equals the quota sum, then re-run with a discovery quota that exhausts the 10-session pool and assert the total stops at the pool size rather than looping. (The design's "exactly 31 only when every metric lasts" reads against its own pass-through rule — see finding A.)
- Ranking is `metric DESC, session_id ASC`. *Evidence:* the pool's costs are all distinct, so bind the tiebreak instead on the compaction metric, where `2352492b…` and `registry-zoo…` both hold 1; with cost quota 0 and compaction quota 1, assert the lower `session_id` is taken.
- Major-skill qualification counts distinct pool sessions, not calls. *Evidence:* at threshold 2, `pr-and-document` has 4 calls in 1 pool session (its other user `0a76f771…` is pool-excluded for zero turns and zero runs) while `grill-me` has 2 calls across 2 pool sessions; assert `grill-me` qualifies and `pr-and-document` does not. A call-counting implementation ranks `pr-and-document` first and fails.
- Major skills are iterated in skill-name order, each taking its most recent unselected user. *Evidence:* with cost quota 1 (taking `4208c1bd…`, one of `grill-me`'s two users) and threshold 2, assert the `grill-me` slot walks down to `088d63aa…`, and that the skill tags appear in skill-name order.
- A major skill whose every pool user is already selected contributes nothing. *Evidence:* with cost quota 2, both of `grill-me`'s users are taken; assert the skill contributes no row and that its slot reappears in discovery rather than vanishing.
- The seeded discovery stratum is a function of the seed and never re-picks. *Evidence:* two runs at `seed=a` and one at `seed=b`; assert the first two sets are equal, the third differs, and none intersects the ranked strata.
- **A session whose turns made no api call is outside the pool, and the floor that excludes it is bound.** *Evidence:* a copy of the store with one real session's api and tool calls deleted, leaving the `/model`-only shape no fixture records; assert the pool-exhausting draw returns 9 rather than 10 and omits it, then bind `min_api_calls=0` and assert it is back. Bolded: iteration 1 spent three of its eight discovery slots on config-only sessions, and a floor that is not in the citation describes a pool no reader can reconstruct.
- Sessions with zero turns and zero runs are outside the pool entirely. *Evidence:* the store's 4 such sessions; assert none appears under any stratum, discovery included.
- The selection window rides `$as_of`, so moving it moves the pool and nothing else. *Evidence:* the same bindings at `$as_of` 2026-07-28 and 2026-08-07 draw from pools of 10 and 5; assert the second set is drawn entirely from the second pool.
- **The manifest's production defaults are the design's values.** *Evidence:* assert the defaults are 8 cost / 5 error / 4 compaction / 8 discovery and threshold 5. Bolded: every other leaf here binds fixture-sized values, so this pin is the only thing standing between an edited quota and a committed report citing a number nobody ran.
- `select_runs` gives every `agent_type` its highest-error and highest-cost run under the same tiebreak. *Evidence:* the store's 7 runs across 7 distinct `agent_type`s; assert one row per type, each tagged with the stratum that took it.
- **As built,** that leaf binds `min_runs=1` and gains a second half: at `min_runs=2` — above every fixture type's single run — the same draw over the same runs comes back empty. The floor exists because the first production run drew 75 runs across 59 types where the design sized ~20: `agent_type` is an open set (`docs/schema.md`), and a session that names its own subagents wins a reading slot per invented name. At the pinned default of 5 the draw is 28 runs across 14 definitions.

## integration (digests) — `tests/analyze/test_digests.py`

The digest and error-listing queries through the CLI, against the fixture store; each truncation leaf uses a copy with a planted sentinel.

- **`session_digest` emits an unattributed row for NULL-`turn_id` api calls, and its cost sums to the session's rollup.** *Evidence:* `0a76f771…`, whose 5 api calls all carry NULL `turn_id` and hold all $2.39 of its live cost; assert the unattributed row's call count and cost, and assert the digest's total equals `session_rollups.cost_usd`. Bolded: a plain turn join drops these rows and the digest reports $0 while the front matter reports $2.39 — the disagreement the design added this row to prevent.
- The digest's cost identity holds against exactly the scope the digest claims. *Evidence:* `088d63aa…` carries a NULL-`turn_id` call under an agent source, not `main`; assert the digest's total matches the rollup restricted to the digest's own source scope, so a digest listing only `main` rows cannot advertise a session-wide total.
- **`run_digest` returns one run's rows and no other's.** *Evidence:* `spine/`'s run `ac461ef46b4bb8e32` and the leaf `af6473ae437c9608d` it spawned; assert the digest's turn and call counts equal `count(*)` over `live_*` for that `(session_id, source)`, and that no leaf row appears under the parent. Bolded: a join that fans out inflates every number a reader copies into a report, and the counts still look plausible.
- Prompts truncate to 300 chars. *Evidence:* a copied store with a >300-char sentinel planted into one real turn's `prompt` (invented, because the longest recorded prompt is 145 chars after redaction); assert the digest's cell is 300 chars and does not carry the sentinel's tail.
- **`error_records` lists a session's failed tool calls whatever thread they ran in, and the line it gives is one `records_slice` can read.** *Evidence:* `5a88789c…`, whose only error sits inside an agent run; assert the session-keyed query returns that call with its run as `source`, that slicing the line it names returns a record holding the call's id, and that binding `source=main` returns nothing. Bolded: readers scanned a thousand raw records a session hunting for `is_error` and three runs' errors were never found at all — the line number is what replaces the scan.
- `error_records` returns the failed calls and no others, including a server-side one whose result rides an assistant record rather than a user one. *Evidence:* `088d63aa…`'s one `advisor` error beside its successful calls; assert the row count matches `count(*) FILTER (is_error)` and that the row names the tool. A `type = 'user'` join finds the client errors and silently drops this one.
- An error's text is cut to 200 chars, with its full length beside it. *Evidence:* a copied store with an over-long sentinel planted as one real error's result (invented, because fixture results are redacted to a word); assert the cell is 200 chars, the tail is absent, and `error_chars` reports the planted length.
- `view_runs` carries each run's cost, failed tool calls and compactions, so ranking a session's runs takes one query rather than a `run_digest` per run. *Evidence:* `5a88789c…`'s two runs, one holding the session's only error and the other a compaction moved onto it from a main thread (invented placement: no recorded fixture run compacted); assert each row's four numbers against `live_*` for that `(session_id, source)`, and that the error and the compaction land on different rows — a session-wide total puts both on both.
- **`records_slice` refuses to run without a line range**, and caps `raw` at 2000 chars. *Evidence:* assert the CLI exits naming the missing parameter; and assert against `0a76f771…`'s recorded 3,054-char record that the returned cell is 2000 chars. Bolded because a defaulted line range is the failure the design's "mandatory" is there to prevent — a reader who omits it would pull an unbounded slice of private transcript into context and see no error (finding B).

## integration (corpus counts) — `tests/analyze/test_counts.py`

**As built,** iteration 1's process review added the counting queries that promote a recurring observation to a counted finding. Their leaves need a population the recorded corpus does not hold — every recorded error is a one-off redacted to a word — so each plants one onto real rows and says so at the fixture.

- **`error_signatures` counts one signature over many bodies.** *Evidence:* a copied store marking every `Read` in `4208c1bd…` and `5a88789c…` failed with a shared first line and a per-call tail (invented, because a recurring error is exactly what the recorded corpus lacks); assert the eight calls come back as one row carrying 8 errors, 2 sessions and 3 threads. Bolded: grouping on the whole result splits a recurring error into a group per call site, which reads as no error recurring at all.
- Each signature is counted over the trailing window and over the corpus, like every other corpus count. *Evidence:* at `$as_of` 2026-08-07 the older of the two planted sessions falls out of the window; assert the window row drops its four errors while the corpus row still holds all eight.
- **`agent_compactions` counts every compaction under the thread that had it, and no compaction goes uncounted.** *Evidence:* a copied store with a recorded compaction copied onto `5a88789c…`'s `auditor` run under a fresh id (invented placement: no fixture run compacted, and a fresh id keeps the `corpus_*` first-seen rule from preferring a twin); assert the run's definition carries it and that the `compactions` column sums to the store's own total for the project. Bolded: the query carries no floor precisely so the sum holds, and a floor is the obvious thing for a later edit to add.
- The main thread rides in as its own row, counted over every session in the period rather than only the ones that compacted, and thread count stays separate from compaction count. *Evidence:* the fixture's five recorded main-thread compactions over four sessions, one of which compacted twice; assert the row's `threads`, `compacting_threads` and `compactions`, and that a definition that never compacted still gets a row.
- A bound `$signature` counts a phrase anywhere in the error text, and `$min_occurrences` bounds the listing. *Evidence:* the planted signature beside the two recorded one-off errors; assert the floor of 2 leaves only the planted group, and that binding a phrase that appears only in the planted tails returns that group with its full count.
- **`command_failures` groups failures by the command that ran, so a bare `Exit code 1` is attributable.** *Evidence:* a copied store rewriting the eight `Read` calls of `4208c1bd…` and `5a88789c…` as Bash calls carrying invented command lines — invented because fixture redaction replaces every tool input, and shaped after the canonical store, where 839 of the window's 1,487 failed Bash commands open with a `cd … &&` wrapper; assert that four wrapped `grep` failures and two bare `grep` successes reach one head, that `gh pr checks` keeps its subcommands, and that the successes come back under a NULL signature as the denominator. Bolded: the head is what makes the table publishable — no flag, path or quoted argument may reach it, and `$head_chars` is the backstop, asserted by binding it down to four characters.

## integration (windows and trends) — `tests/analyze/test_windows.py`

The windowed and trend queries against the fixture store, with `$as_of` bound rather than read from the clock.

- **The full-corpus count and the trailing-28-day count agree: the window's count equals the full count restricted to the window, and its session set is a subset.** *Evidence:* at `$as_of` 2026-08-07 the window opens 2026-07-10 and covers 6 of the 13 mycelia sessions; assert both counts and the subset relation. Bolded: the dual window is what every report's numbers are quoted in, and two independently written queries drift.
- ISO-week buckets partition the window: each session lands in exactly one week, and the weeks sum to the total. *Evidence:* the store's 5 unevenly filled weeks (2026-W27 … W31, at 4/4/3/1/1); assert the per-week counts and that their sum equals the full-corpus count.
- **As built,** the undated leaf goes further than the recorded corpus can: `fork_byref`'s session is excluded by the corpus predicate anyway, so a copy plants a `project_dir` on it and asserts the trend gives it an `undated` bucket and the buckets still sum to the corpus count.
- **A session with a NULL `started_at` cannot break the partition.** *Evidence:* `fork_byref`'s `07a769d7…` has both NULL `project_dir` and NULL `started_at`; assert it is in neither the total nor any bucket, and that the sum identity still holds. Bolded: it is the one row that makes the corpus predicate and the trend query interlock, and a NULL bucket silently swallows sessions.
- **`$as_of` alone decides the window, and the runner defaults it to today.** *Evidence:* the same query at `$as_of` 2026-07-28 and 2026-08-07 returns 13 and 6 in-window sessions on the identical frozen store; and a bare invocation's citation reports today's date as the resolved value, which is what the corpus stamp records. Bolded: this is the anti-rot obligation — the smoke test's clock-reference check bans the mechanism, and this one proves the replacement works.
- **As built,** that leaf gained a third `$as_of` because both planned values sit after the last fixture session, so neither could exercise the window's far edge — deleting the upper bound passed the whole tier. At `$as_of` 2026-07-19 the window covers 11 of the 13 sessions; the leaf asserts the out-of-window set is exactly the sessions started after that date, and that the one started at 20:27 that evening is still in, since the bound runs to the end of the day.

## inspection (process artifacts)

Not automated. Each is a line an auditor confirms by reading the named file, on the PR that lands it.

- `docs/analysis.md` exists and is reachable from the CLAUDE.md Layout tree with a one-line gloss. *Evidence:* the Layout entry and the link resolve.
- The improvement-category vocabulary is defined in exactly one place. *Evidence:* the nine slugs plus `other` appear in `templates/session.md`; `grep` finds no second copy in `docs/analysis.md` or `templates/run.md`, which link to it.
- Both templates carry `template_version` and the front-matter keys the design lists, and state their body caps (60 lines, 30 lines). *Evidence:* read both files.
- `docs/analysis.md` states the reader protocol's bounded brief, the digest-only rule, the fixed process-review checklist, and the quoting contract — citation plus rule-based redaction — with `reports/README.md` linking rather than restating it. *Evidence:* read both files.

**As built,** all four check out, with two notes.

1. Confirmed. The Layout tree's `docs/` block carries `analysis.md` with a one-line gloss, above `schema.md`.
2. Confirmed. `rg` over the repository outside `plans/` finds the ten slugs on two adjacent lines of `templates/session.md` and nowhere else; `templates/run.md` points at that file by path, and `docs/analysis.md` never names a slug. The design itself still lists them, which is what a plan is for — it is a record of the decision, not a second definition anything reads.
3. Confirmed, with one deviation. Both templates carry `template_version: 1`, and both state their cap — "Body cap: 60 lines" and "Body cap: 30 lines", each counting what the reader writes rather than the guidance comments it deletes. The session template carries every front-matter key the design lists. The run template carries `session_id` and `agent_id` — the design's key — plus `agent_type`, and drops `compactions`, `skills` and `commands`: a run has no compaction of its own and starts no slash command, so those three would be fields no reader could ever fill. Both add `unpriced_api_calls` beside `cost_usd`, under the house rule that a cost never travels without the count of what our price table missed.
4. Confirmed. The reader-protocol section states the bounded brief and names what a brief holds; states that readers work through digests with `records_slice` as the only route to raw text; and says plainly that both are convention, with the bounded brief and the review checklist as the mitigations. The checklist is five fixed questions. The quoting section states the citation and the redaction rule. `reports/README.md` gains one sentence linking to the guide and naming what it covers — no clause of it is restated there.

---

## Not covered, and why

- **The production selection and every corpus number.** "≤31 sessions", "$15,069", "263 in-window sessions" are claims about `data/traces.duckdb`, which is gitignored and grows daily; a test asserting them fails tomorrow and proves nothing today. The mechanics are pinned at fixture-sized bindings and the defaults by the pin leaf; the numbers are re-run and cited per iteration, which is the design's own rule
- **Slice 4 — iteration 1 itself.** Reader-subagent behavior, synthesis quality, whether a promoted finding deserved its confidence level. The design calls this the process's own acceptance test, and its review section is the artifact
- **`records_slice` as a privacy mechanism.** The design says plainly that the caps are convention: a reader has Bash and can open the store. The cap's arithmetic is tested; the reader's compliance is a process-review question
- **Redaction judgement on committed quotes.** The obligation the design assigns is that every quote carries a citation, which makes Nathaniel's PR check mechanical. Whether a given line is safe to publish is his call, not a test's
- **The `*_enriched.sql` variants.** The enrichment tables do not exist yet (`plans/enrichment/design.md` owns them). When they land, the base-variant-still-works obligation is one leaf on a store without those tables
- **Query performance at 575 sessions.** The fixture store is 16 sessions. If a query turns out to be slow on the real store, that is an iteration-1 process-review finding

## Findings for the designer

The four findings this plan raised against the first draft are resolved by the amendment, and every obligation they blocked is now reachable:

1. Quotas and the skill threshold are bound parameters with production defaults — the selection section binds fixture-sized values, and a pin leaf guards the defaults
2. The window rides `$as_of` and clock-reading SQL is banned — one static leaf bans the mechanism, one behavioral leaf proves the replacement
3. The run-out rules are stated — the nonzero-and-stop-short leaf replaces the pad-to-quota leaf, and the collapsed-skill leaf now asserts where the slot goes
4. The citation channel is stated — table header, stderr under `--csv`, carrying resolved bindings

Two residuals, both worth a sentence rather than a redesign:

- **A. The set size is over-determined.** The amendment says unused ranked slots pass to discovery, "which has no metric to run out of", and also that the set is "exactly 31 only when every metric lasts and no skill collapses". Both cannot hold: if every unused slot passes to a stratum that never runs out, the set is the quota sum whenever the pool is large enough, and the only thing that makes it smaller is an exhausted pool. On the 10-session fixture pool both bounds bite at once, so the leaf above asserts the pass-through and the pool ceiling separately. Pick one sentence — either the pass-through is best-effort against the remaining pool (then say the shortfall shows up as a smaller set only when the pool runs out) or the "exactly 31" clause goes.
- **B. "Every parameter has a production default" collides with `records_slice`'s mandatory line range.** A defaulted line range is not mandatory: a reader who omits it gets a silent window instead of an error, which is exactly the unbounded raw-text pull the cap exists to stop. Its `session_id` and `source` have no sensible default either. Let a manifest entry declare a parameter required with no default — which is also the house rule on defaults (`CLAUDE.md`: give a parameter a default only when a sensible one genuinely exists). Both affected leaves are written against the "mandatory" reading.

## Obligation count

| Area | Obligations |
| --- | --- |
| integration (runner) | 10 |
| integration (library smoke) | 5 |
| integration (selection) | 14 |
| integration (digests) | 9 |
| integration (corpus counts) | 6 |
| integration (windows and trends) | 4 |
| inspection (process artifacts) | 4 |
| **Total** | **47** |
