# Viewer UI overhaul — testing plan

Obligations for `plans/viewer-ui-overhaul/design.md`. Every leaf is reachable through the design's
chosen seam: `TestClient` over `corpus_db`, assertions on `data-field`/`data-*` attributes,
expectations derived from the served store, bounds by arithmetic in `tests/view/test_bounds.py`.
Leaves are tagged with the slice that must turn them green (`[S1]`–`[S5]`).

Three obligations the seam cannot reach as designed are collected under **Design findings** — they
go back to the designer, not into a weaker test.

## Facts re-checked against the fixture corpus

The design's numbers came from the private canonical store. The suite runs on `corpus_db`, so these
were re-probed there (`tests/conftest.py:corpus_db`, built from `tests/fixtures/`, 2026-08-18):

| Fact | Fixture corpus |
| --- | --- |
| Slash-command turns | **6, across three recorded fixtures** — `spine` (`/model`, `/night-run`), `model_only` (`/model`, `/clear`, `/reload-skills`), `teammate` (`/coordinator`). `command_args` is `[redacted]` or the empty string |
| `project_dir` values | `/Users/nob/repos/mycelia` ×15, `/invented/project`, `/repo`, NULL ×1 — **no worktree-shaped descendant** |
| Resume double-count | mycelia spend is **$14.8564 over `session_rollups`, $13.62 over `corpus_rollups`** — `resume_pair` makes the two views disagree by $1.2364 |
| Nav nodes per session | max **6** (`4208c1bd…`: 4 main turns + 2 runs). Nothing approaches `NAV.ceiling = 200` |
| Nested runs | real: `4208c1bd…` has `claude` → `Explore` (depth 2), `5a88789c…` has `auditor` → `fork` |
| Zero-cost sessions | 3 — the `share` gap case has recorded data |
| Tool errors | `088d63aa…` 1/5, `5a88789c…` 1/7 — real non-trivial rates |
| Agent types per session | never more than one run of a type — a `×N` count with N > 1 must be planted |
| Session timestamps | 2026-06-30 to 2026-08-06, fixed — they recede from any wall-clock `$now` |

## Obligations

### unit — `tests/view/test_format.py`, no store; values constructed at the boundaries

The file's own lead says why: the interesting values sit a millisecond either side of a unit
boundary and no recorded row sits on one. Constructed values are the established pattern here.

- `ago` prints the largest unit it fills, against a `now` the caller passes: `"just now"`, `"2h ago"`,
  `"3d ago"`. *Evidence:* `[S1]` a parametrized table over `(value, now, printed)` comparing the
  **whole** string, with the case one second either side of each unit boundary, in the shape of
  `test_a_duration_prints_in_the_two_largest_units_it_fills`
- `ago(None, now)` is `ABSENT`. *Evidence:* `[S1]` `ago` joins the parametrize list of
  `test_a_column_the_store_left_null_reads_as_one_dash`, which reds if the new filter is omitted
- `share` prints one decimal (`"2.2%"`), and a real zero numerator prints `"0.0%"` rather than a gap.
  *Evidence:* `[S1]` whole-string comparison for `share(1, 45)`, `share(0, 5)`, `share(1, 1)`
- **`share` is `ABSENT` when the part is None, when the whole is None, and when the whole is 0** —
  the three gaps a rate must not report as `0%` or crash on. *Evidence:* `[S1]` three cases asserting
  `ABSENT`; a zero whole is the one the store actually produces (3 zero-cost fixture sessions)
- A future timestamp (clock skew between a session's `started_at` and `now`) prints something rather
  than a negative unit. *Evidence:* `[S1]` `ago(now + 1h, now)` compared whole; the value is
  constructed and labelled as such — no recorded session carries one

Mutation: this file is the plan's main lean on `mise run mutate 'hyphae.view.format.*'`. The
boundary comparisons and the rounding in `share` are exactly the expressions a mutant flips, and the
file's own docstring records that a table of substring assertions once survived a run untouched.

### route — `tests/view/test_app.py` (updated), `TestClient` over `corpus_db`

The list moves to `/sessions` and recomposes; every leaf below derives its expectation from the
served store rather than a literal.

**Session list, recomposed** `[S1]`

- Every session still gets a row carrying its own numbers, with the counts now stacked into
  two-line cells. *Evidence:* update `test_the_list_holds_every_session_with_its_own_numbers` to read
  `data-field` primaries and secondaries from `fields(html, "data-session-id", id)` and compare
  against `session_rollups` per session — the derivation, not a literal table
- **Every integer a list row prints carries thousands separators; every cost prints as money; every
  rate prints as a share** — the formatting rule, which is what "no bare `{{ row.int }}` survives"
  means operationally. *Evidence:* a `plant` that clones one session's turns and api calls past 999
  rows (the `range()` clone pattern `test_bounds.py` already uses for skills and compactions), then
  asserts every numeric `data-field` on that row matches `\d{1,3}(,\d{3})+` or `ABSENT`, and that no
  cell holds a bare four-digit run. Planted, because no fixture session is that large
- A column the store left NULL reads as one dash rather than an empty cell. *Evidence:* the NULL
  `project_dir` session (`fork_byref`, 1 in the corpus) rendering `ABSENT` in its cell
- The Errors cell shows a rate and sorts by count. *Evidence:* `088d63aa…` (1 error / 5 tool calls)
  and `5a88789c…` (1 / 7) rendering the `share` of their store values, plus
  `test_a_sort_and_its_reverse_are_exact_opposites` covering `tool_errors` unchanged
- The Subagents cell counts runs by agent type, ordered by count descending, and cuts like skills.
  *Evidence:* derived from `SELECT agent_type, count(*) FROM live_agent_runs GROUP BY` for
  `4208c1bd…` (`claude`, `Explore`); a `plant` cloning one run row supplies the `×2` case and the
  over-cut case, and is labelled planted — no fixture session runs one agent type twice
- The Work cell counts turn categories, cut at 3, and is absent from an unenriched store.
  *Evidence:* `enriched_client` vs `client` over the same session; expectation derived from
  `turn_enrichments` category counts, whose planted cycle (`tests/conftest.py:PLANTED_CATEGORIES`)
  is the established pattern for model-written values
- `SORTS` shrinks: every remaining key names a column the query returns, and the dropped keys
  (`output_tokens`, `active_ms`) are refused. *Evidence:*
  `test_every_sort_key_names_a_column_the_query_returns` (auto-covers the shrink) plus the two dropped
  keys added as cases to `test_an_unknown_sort_or_direction_is_refused`
- The list, its pagers, its sort links, its filter form and its citation all live at `/sessions`
  `[S3]` — the move lands with the page that takes `/` over, because a list moved out of `/` before
  anything replaces it leaves the landing route a 404.
  *Evidence:* `test_the_list_is_served_a_page_at_a_time`, `test_a_filter_rides_the_links_and_the_citation`
  and `test_the_list_footer_cites_its_query_and_what_was_composed_around_it` re-pointed at `/sessions`;
  plus an assertion that every `href` the list mints starts `/sessions` (no `/?sort=` survivor)

**Command turns** `[S2]`

- **No heading the viewer renders contains `<command-`, on any page or fragment.** *Evidence:* a new
  leaf sweeping the session pages of the three fixture sessions that hold command turns, plus the run
  pages under them, asserting `"<command-"` and `"&lt;command-"` appear in no `data-field="prompt"`
  or `data-field="command_name"` element. The corpus proves the absence is bounded: 6 recorded
  command turns, whose raw prompts do contain the tags
- A command turn's heading shows the `/name` badge and the args head; a plain turn keeps its prompt.
  *Evidence:* `spine`'s `/night-run` turn (`command_name`, `command_args = '[redacted]'`) beside a
  plain-prompt turn in the same session, both derived from `live_turns`
- A command with an empty `command_args` renders the badge and no args tail rather than a stray
  separator. *Evidence:* `model_only`'s `/clear` and `/reload-skills` turns, whose recorded
  `command_args` is the empty string — the case the design's "non-NULL `command_name`" rule does not
  by itself cover
- A `<teammate-message …>` prompt still renders its opening tag. *Evidence:* the `teammate` fixture's
  turn; `docs/schema.md:81` is the contract, and this leaf keeps the command fix from eating it
- The same heading rule applies on a run page's timeline. *Evidence:* `tests/view/test_run.py`
  extended over a run whose thread holds a command turn, or an explicit assertion that no run page in
  the corpus renders `<command-`

**Projects landing** — new file `tests/view/test_projects.py`, mirroring the layout `[S3]`

- `GET /` serves projects; the session list is no longer there. *Evidence:* `data-project` rows
  present and `data-session-id` absent on `/`, and the converse on `/sessions`
- One row per folded project, plus one unlinked "(no project)" row. *Evidence:* the corpus's 3
  distinct non-NULL dirs and its 1 NULL-dir session (`fork_byref`), derived by the test re-running
  the fold in its own SQL rather than listing the paths
- **A worktree folds onto its shortest stored prefix-ancestor, and a sibling that merely shares a
  string prefix does not.** *Evidence:* a `plant` setting one session's `project_dir` to
  `/Users/nob/repos/mycelia/.claude/worktrees/wt-1` and another's to `/Users/nob/repos/mycelia-other`
  — the first folds into the mycelia row, the second stays its own row. Planted and labelled: the
  fixture corpus holds no worktree path, so this is the one place the design's canonical-store
  observation cannot be reproduced from fixtures
- **Project spend comes from `corpus_rollups`, not `session_rollups`.** *Evidence:* the mycelia row's
  all-time spend equals `SELECT sum(cost_usd) FROM corpus_rollups WHERE project_dir = …` ($13.62)
  and is strictly less than the `session_rollups` sum ($14.8564) — the `resume_pair` fixture makes
  the two views disagree, so a regression to the double-counting query reds here
- The 7d and 30d cells count and cost exactly the sessions inside the window the page cites.
  *Evidence:* the test reads `$as_of` out of the citation footer (`data-field` on the citation `<li>`,
  `base.html:18-27`) and re-runs the window query in the store bound to that same value, so the
  expectation reproduces the exact window
- **The windows are exercised rather than vacuously empty.** *Evidence:* a `plant` shifting three
  sessions' `started_at` to `now - 1d`, `now - 10d` and `now - 40d`, asserting the first lands in
  both windows, the second in 30d only, the third in neither. Without it the leaf above decays to
  all-zero as the fixed fixture timestamps recede from the wall clock — an unbounded absence
- Each row links to `/sessions?project=<root>`, and following the link returns exactly the folded
  set. *Evidence:* the href read off the row, fetched, and its `data-session-id` values compared with
  the fold's own session ids
- A page over a store with no sessions in a window renders the row with `ABSENT`, not a crash.
  *Evidence:* the `/repo` and `/invented/project` rows, whose single sessions sit outside both windows
- The page cites the query it ran, with `$as_of` and `$projects` bound. The design calls the
  clock parameter `$now`; the shipped name is `$as_of`, because the query library refuses a query
  whose SQL holds the identifier `now` (`tests/analyze/test_queries.py:CLOCK`). *Evidence:* the citation
  assertion pattern of `test_the_list_footer_cites_its_query_and_what_was_composed_around_it`

**Project filter becomes the prefix predicate** `[S3]`

- **`?project=/Users/nob/repos/mycelia` returns sessions under `…/mycelia/.claude/worktrees/*` and
  does not return `/Users/nob/repos/mycelia-other`.** *Evidence:* the same plant as the fold leaf,
  asserted through `/sessions?project=`; the boundary case (`ancestor || '/'`) is what separates a
  correct predicate from `starts_with(dir, ancestor)`
- The filter value still reaches DuckDB only as a binding. *Evidence:*
  `test_a_filter_value_reaches_duckdb_only_as_a_binding` extended with the new predicate's sample
- The filter box's `<datalist>` offers the folded roots, matching the landing rows. *Evidence:*
  the `<option>` set compared against the fold the projects page rendered
- `test_every_filter_the_list_offers_has_a_sample_to_check_it_with` still passes with the project
  sample updated. *Evidence:* the existing closed-set check over `FILTERS`

**Session page recompose** `[S4]`

- The page keeps its header facts, its timeline, its permalinks and its citations through the
  regroup. *Evidence:* `test_the_session_header_holds_what_the_store_says_about_it`,
  `test_the_timeline_is_the_sessions_turns_in_order`, `test_a_turns_permalink_opens_the_page_that_turn_starts`
  and `test_the_session_page_cites_every_query_it_ran`, unchanged except for `data-field` moves
- The header's numbers adopt the formatting rule. *Evidence:* the same planted over-999 store as the
  list leaf, asserted on `fields(page, "id", "session-header")`
- The sidebar asks for the nav fragment at the window the page rendered. *Evidence:* the `hx-get`
  URL read off the sidebar element carries the page's `after`/`turns`/`chips`, and fetching it
  returns 200 — the seam can prove the URL and the response, not that htmx fires it (see **not covered**)

**Nav fragment** — new file `tests/view/test_nav.py` `[S4]`

- The fragment holds one node per main-thread turn of the whole session, each with its direct runs
  nested. *Evidence:* node ids compared against `SELECT id FROM live_turns WHERE source='main'` and
  `live_agent_runs` for `4208c1bd…` (4 turns, 2 runs) — the whole session, not the window
- **Every run the session holds is accounted for in the nav exactly once.** *Evidence:* the shape of
  `test_every_session_page_accounts_for_all_of_its_runs`, run against the fragment; the corpus's
  orphan run (`teammate`'s `architect`, no spawning tool call) and its run spawned by a call under no
  turn are the cases that decide where an unplaceable run goes (see **Design findings**)
- **A run's own children sit in a `<details>` with no `open` attribute; turns and their direct runs
  do not.** *Evidence:* `4208c1bd…`'s `claude` → `Explore` pair and `5a88789c…`'s `auditor` → `fork`
  pair — recorded two-level forests, so this is a real nesting rather than a staged one
- A session with no runs renders a plain turn list and no `<details>`. *Evidence:* any of the 9
  single-turn zero-run fixture sessions
- **An in-window turn anchors to `#turn-<id>`; an out-of-window turn anchors to the permalink URL,
  and following it returns a page that holds that turn.** *Evidence:* `4208c1bd…` at `?turns=1`,
  which puts 3 of its 4 recorded turns out of window; the out-of-window href is fetched and its
  `data-turn` values asserted to contain the target — a round trip, not a string shape
- `data-here` marks exactly the turns the page rendered. *Evidence:* `values(page, "data-turn")`
  compared set-wise with `values(fragment, "data-here")` at the defaults and at `?turns=1`
- Every node states its cost and its share of the session's cost. *Evidence:* derived per node from
  `session_digest` / `view_runs` costs over `session_rollups.cost_usd` for `4208c1bd…` ($2.8293)
- A node in a zero-cost session shows `ABSENT` for its share rather than `0%`. *Evidence:* one of the
  3 zero-cost fixture sessions
- **The spend meter's decile class is arithmetic over the node's share, and any nonzero share rounds
  up to `s1`.** *Evidence:* class read with `inside(html, "data-nav", id, "class")` and compared
  against the decile computed from the store's costs; a `plant` giving one turn a share under 10%
  supplies the round-up case. This is the leaf `mise run mutate 'hyphae.view.threads.*'` should
  find no survivor under — a `//` or a boundary flip here silently blanks the meter.
  **As built,** the ten classes survive but the scale is logarithmic over three orders of magnitude
  rather than a decile — design.md carries the same clause, and a linear decile drew 525 of the
  store's 977 main-thread turn nodes with the same shortest bar. The leaf that landed asserts the
  order instead of a computed class: rank the turns by what they took and the steps never go
  backwards, with the two ends this corpus supplies pinned. It also checks that every class
  `meter()` can reach is one the served stylesheet draws — a step with no rule is a bar the reader
  never sees and nothing complains about
- Planted markup in a nav label arrives inert. *Evidence:* `test_planted_markup_arrives_inert`
  extended — the sentinel already lands on `turns.prompt` and `agent_runs.description`, so the
  fragment URL joins the `served` tuple; nav labels also draw on enrichment text, so the enriched
  store's sentinel leaf (`test_a_model_written_description_is_escaped_like_any_other_transcript_text`)
  gains the fragment too
- A nav label falls back prompt-ward: enrichment description, then `/command args`, then prompt head.
  *Evidence:* the same turn rendered from `enriched_client` and `client`, plus a command turn in
  `spine` — three recorded label sources for one assertion set
- A session id the store does not hold is a 404, and the fragment cites its query. *Evidence:*
  `MISSING` through the shapes of `test_a_fragment_naming_nothing_is_a_404` and
  `test_a_fragment_cites_the_query_that_fetched_it`
- Overflow past `NAV.ceiling` renders a "+N more turns" tail whose count matches what was cut, and
  paging still reaches the cut turns. *Evidence:* see **Design findings** — with `NAV` bindable down
  this is `?nodes=2` on `4208c1bd…`'s 6-node forest; without it, a plant cloning 200+ turn rows

**Cross-cutting, route level** `[S1]`–`[S4]`

- Every response still carries the CSP, the new routes included. *Evidence:*
  `test_every_response_carries_the_content_security_policy` parametrized over `/`, `/sessions` and the
  nav fragment
- No page asks for an asset the viewer does not ship — in particular no inline `style` attribute
  sneaks the spend meter past the CSP. *Evidence:*
  `test_every_asset_a_page_asks_for_is_one_the_viewer_ships`, plus an assertion that no served page
  contains ` style="` (the CSP trap the decile classes exist to dodge)
- Serving the store leaves it read-only. *Evidence:* `test_serving_the_store_leaves_it_read_only`

### arithmetic — `tests/view/test_bounds.py` (updated), planted `&` through the shipped templates

The file prices a page rather than observing one: every transcript character costs
`ESCAPED_CHAR_BYTES = 5`, and the measured markup constants are re-measured through the app by the
planted leaves at the bottom of the file.

- Every route is in the payload sweep and answers under `PAGE_BYTES`. *Evidence:*
  `test_every_route_the_viewer_exposes_is_in_the_payload_sweep` reds until `/sessions`,
  `/fragment/nav/{session_id}` and the projects route join `ROUTES` — the check is already the
  instrument, so this needs no new leaf beyond the entries
- Every viewer query still binds its page size and wraps its fat columns. *Evidence:*
  `test_every_page_size_in_a_viewer_query_is_a_bound_parameter` and
  `test_no_page_or_fragment_query_selects_a_fat_column_whole` over the three new SQL files
- **`prompt` and `command_args` join `FAT`, and every viewer query that selects them wraps them in
  `substr` or `length`.** *Evidence:* the two names added to `FAT`;
  `test_every_fat_column_is_still_a_column` proves they are real columns (both on `turns`), and the
  scan reds on any query selecting either whole. `command_args` reaches 7,947 chars in the canonical
  store, so this closes a live hole rather than a hypothetical one
- **The turn heading is priced as the max of its two arms, not their sum, and the rendered heading
  respects that price.** *Evidence:* the arithmetic in `worst_turn_bytes` taking
  `max(PROMPT_CHARS, COMMAND_ARGS_CHARS + badge)`, plus a planted leaf: one store with a turn holding
  a 300-char `&` prompt *and* a 300-char `&` `command_args`, whose rendered `<h2>` is measured at or
  below the priced arm. Without the second half the max-of-arms claim is an assumption about a macro
  nobody weighed — and the sum would put the page at ~513 KB
- `SESSIONS` is re-derived against the fatter row and the manifest pin holds. *Evidence:*
  `MEASURED_LIST_CHROME + bounds.SESSIONS.ceiling * worst_session_row_bytes() < PAGE_BYTES` with the
  new default (the design expects ≈ 100), and `worst_session_row_bytes` picking up the stacked cells,
  the counted agent types and the work categories through its existing `heads(SHOWN, …)` count rather
  than a new literal
- The re-measurement of a list row and of list chrome still holds through the recomposed template.
  *Evidence:* `test_a_session_list_of_nothing_but_escapes_costs_what_the_ceiling_budgets`, re-pointed
  at `/sessions` and extended so the plant fills the new list columns to their caps — the assertions
  at its foot are what prove the plant reached every cap
- **`NAV` is priced and re-measured: `NAV.ceiling` nodes of worst-case row plus wrapper stays under
  `PAGE_BYTES`, and one more node costs no more than the arithmetic gives it.** *Evidence:* a planted
  leaf in the shape of the list one — `&` at every nav cap (`$nav_chars`, agent type, task
  description) — measuring the marginal cost of a node by serving the fragment at two sizes
- `PROJECTS` is priced the same way: ceiling rows of a worst-case project row plus chrome under
  `PAGE_BYTES`, with the chrome re-measured through the app. *Evidence:* a planted leaf with `&` at
  every cap the project row shows and more projects than a page holds
- The bound manifest names every bound and every default is under its ceiling. *Evidence:*
  `test_the_manifest_pins_the_production_page_sizes` — its `set(declared) == {…}` assertion reds the
  moment `NAV` or `PROJECTS` lands unpriced, which is the design's own enforcement of "every new
  surface states its bound"
- The session page's own worst case does not grow. *Evidence:*
  `test_a_session_page_of_nothing_but_escapes_carries_the_chrome_the_ceiling_budgets` — its `chrome()`
  strip must exclude the sidebar, and the leaf's own both-ways check (`assert not values(html, …)`)
  is what keeps the strip honest

### enrichment — `tests/view/test_enrichment.py` (updated), `enriched_client` over `enriched_db`

- A store no pass has touched, one whose enrichment tables are empty, and a partly described one all
  render every page — the new ones included. *Evidence:*
  `test_a_store_no_enrichment_pass_has_touched_renders_every_page` and its two siblings, with `/`,
  `/sessions` and the nav fragment added to their `pages` helper
- The Work column and the nav's description-first labels are absent, not blank or crashing, on an
  undescribed store. *Evidence:* the same three stores, asserting the `data-field` is absent
- A described item still shows its tags and stale marking after the recompose. *Evidence:*
  `test_a_session_page_tags_every_turn_and_run_the_pass_described` and
  `test_an_item_described_under_an_older_prompt_is_marked_stale`, unchanged
- A run page's turns still carry no description of their own. *Evidence:*
  `test_a_run_pages_turns_carry_no_description_of_their_own`

### whole-suite gate `[S5]`

- `mise run check` is green: format, lint, pyrefly, the hook linter and the full suite. *Evidence:*
  the CI shape (`.github/workflows/check.yml`), run before the PR opens
- The branch's changed modules score under `mise run mutate`, cold and serial, with survivors read
  and either killed by an assertion or reported as a finding about the code. *Evidence:* runs scoped
  to `hyphae.view.format.*`, `hyphae.view.threads.*` and `hyphae.view.listing.*` — the three
  modules whose new logic is arithmetic (share, decile, fold predicate, window) rather than markup

## Not covered, and why

- **Anything a browser does.** The decile class is asserted; the width it paints is not. Same for the
  grid, `position: sticky`, the `-webkit-line-clamp` truncation, the 900px `<details>` collapse,
  dark mode and `:target` highlighting. There is no browser-level or visual-regression tier in this
  repo and the design does not add one; these are checked by eye during slice 5
- **htmx actually firing.** `TestClient` runs no JS, so the nav's `hx-trigger="load"` is covered as
  far as the attribute and the fragment's response — that the sidebar populates in a real browser is
  a manual check, and a no-JS reader losing the map is a stated design consequence
- **The canonical store's scale.** The design's counts (285 nodes, 7,947-char args, 22 agent types)
  come from `data/traces.duckdb`, which is private and gitignored. The suite bounds by arithmetic
  instead; those counts are hypotheses to re-probe at implementation time, not obligations
- **Old `/` list URLs.** The design accepts breaking them; no leaf asserts a redirect
- **A new `command` fixture.** The design's file-tree diff calls for one; the corpus already holds six
  recorded command turns (see below), so no fixture is added and no model answer about a private
  transcript is fabricated

## Design findings — back to the designer

1. **The `command` fixture the design asks for is unnecessary, and the claim behind it is wrong.**
   "None of the 16 fixtures holds one" does not hold: `spine`, `model_only` and `teammate` carry six
   command turns between them, all with non-NULL `command_name`, verified by building `corpus_db` and
   querying `live_turns`. Two of them (`/clear`, `/reload-skills`) have `command_args = ''`, an
   empty-string case the design's "when `command_name` is non-NULL" rule should state a behavior for.
   Recorded args are all `[redacted]`, so the 300-char head still needs a plant — but that is a
   plant, not a fixture.
2. **`NAV` needs to be bindable down, or the cap-tail obligation cannot use recorded data.** The
   fixture corpus's largest forest is 6 nodes against a ceiling of 200, so "+N more turns" is only
   reachable by planting ~200 cloned turn rows. The `CALLS`/`TOOLS` precedent — ceiling equal to
   default, bound down by a query parameter so a test can reach the boundary against the densest
   recorded turn — solves it for a few lines of route code. Recommended: `?nodes=` on the fragment.
3. **The nav does not say where an unplaceable run goes.** The corpus holds both shapes the session
   page already handles specially: an orphan run with no spawning tool call (`teammate`) and a run
   spawned by a call under no turn. The page has an `#unattached` section for them; the design's
   `NavNode` grain (turns, then their direct runs) has no home for either. The "every run accounted
   for exactly once" leaf above is written but cannot be discharged until the design names the
   behavior — an unattached nav section, or an explicit exclusion with a stated reason.

A smaller note, not a blocker: **`ago` freshness is untestable as specified.** The design requires a
closure that reads the clock at render rather than at `build_app`, but with no named clock seam a
`TestClient` test cannot tell the two apart. `.claude/rules/testing.md` asks for an injected clock
where non-determinism meets a test; a module-level `format.now()` a test can monkeypatch between two
requests turns this into a one-line leaf. Until then the drift is covered only by the unit leaves
above, which pass `now` explicitly.

## Green bar per slice

| Slice | Green when |
| --- | --- |
| 1 — session-list recompose | the `test_format.py` leaves, the recomposed-list leaves, `SESSIONS` re-derivation and the re-measured list row/chrome |
| 2 — command-cruft fix | the four command-turn leaves (heading, empty args, teammate tag, run page), the `FAT` additions, and max-of-arms priced *and* re-measured |
| 3 — projects landing | all of `test_projects.py`, the four prefix-filter leaves, and the `PROJECTS` bound |
| 4 — session-page recompose | all of `test_nav.py`, the session-page leaves, the `NAV` bound, and the inert-markup sentinel extended to the fragment |
| 5 — polish + doc-sync | `mise run check` green and the scoped `mise run mutate` survivors resolved |
