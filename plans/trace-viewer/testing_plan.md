# Testing plan: the local trace viewer

Obligations for `plans/trace-viewer/design.md` (audit verdict CLEAR). Every leaf is an obligation; the *Evidence:* clause names the artifact that discharges it, and an auditor traces each leaf to that artifact.

Four rules shape everything below.

- Fixtures are **redacted excerpts of real sessions** under `tests/fixtures/`, with the Claude Code version in each directory's README (`.claude/rules/testing.md`). Redaction flattens every string, so no leaf asserts on recorded text or its length — size and markup obligations use **planted sentinels on real rows**, listed once below and labeled at each call site
- **No leaf reads `data/traces.duckdb`.** The design's corpus numbers (575 sessions, 2.09 GB of raw, 55 unattached runs) are measurements to re-run, not assertions
- **The viewer never writes.** Its connections are read-only, and one leaf proves that the absent-enrichment path does not create the tables `EnrichmentStore` creates on open
- **Untrusted content is the threat model.** Escaping leaves are the load-bearing ones and they sit at two levels, because a `render.py` unit test cannot see a template that pipes a value through `|safe`

Fixture-store facts below were measured on 2026-08-07 by extracting every `tests/fixtures/*/` transcript into a temp store. Re-measure at implementation.

## Scaffolding

`build_store(path, directories)` in `tests/enrich/conftest.py` builds a real DuckDB from the fixture transcripts the way `refresh()` would; `plans/mycelia-analysis/testing_plan.md` already asks for it to move to `tests/conftest.py`. The viewer tier adds two fixtures beside it: a session-scoped store for read-only route tests, and a `planted_store` copy carrying the oversized and markup sentinels.

## What the fixtures already carry

Sixteen transcripts export cleanly. The join and absence shapes the viewer's hardest contracts need are all recorded — **no new fixture session is required**:

| Shape | What carries it |
| --- | --- |
| **The fork self-copy** | `fork_origin`'s run `a61a059e3610e6fb4` — its spawning call `toolu_012WL3…` sits in source `a61a059e3610e6fb4`, the fork's *own* transcript, and resolves to turn `33438141…` of the fork's own timeline. Without `tool_calls.source <> agent_runs.id` the fork chips onto its own turn and its run page lists itself as its own child. `fork_byref`'s `afa3946951a08a798` is the second copy |
| Chip join, both ways | 4 runs resolve to a turn without the exclusion, 3 with it — the fork is the one that drops |
| Unattached runs, **four observed causes** | NULL `tool_use_id` (`aarchitect-5144001ac50718bc`, rootless teammate); self-copy excluded (`a61a059e…`, `afa39469…`); and `tool_use_id` naming a tool call **absent from the store** (`acbc29008a04b9702`, `a3b37063695183556`). The section is defined by the join failing, so the leaf asserts the set, not the causes |
| Run-under-run, both parent rules | `af6473ae437c9608d` (parent via `parent_agent_id`), `ac461ef46b4bb8e32` (no `parent_agent_id`; parent is the spawning call's source `main`) |
| Continuation section | `afa3946951a08a798`'s 2 api calls carry NULL `turn_id` |
| Unattributed row | `0a76f771…` — 5 calls with NULL `turn_id` holding **all $2.39** of its live cost; a page that drops them disagrees with its own header by the whole total |
| Turn → raw record link | all 19 live turns join `raw_records.uuid` on `(session_id, source)` — 19/19 |
| Records paging | `2352492b…` main holds 47 records; `0a76f771…` main holds a **3,054-char** record, past the 160-char preview |
| Compaction markers | 5 sessions, all `source='main'` |
| PR links, skills, agent types | `4208c1bd…` has 2 PR links; 4 distinct skills and 7 distinct agent types across the store |
| Enrichment absent | the store has no enrichment tables, matching the canonical store exactly |

**What the fixtures cannot reach at production settings**, because redaction flattened every string and the sessions are small: max 4 api calls per turn, max 4 tool calls per api call, max `text` 51 chars, max tool `input` 438, max `prompt` 145, a 159-byte offload file, and 47 raw records in the densest `(session, source)`.

The design's amendment makes three of those boundaries reachable by **binding fixture-sized values** for `$page_calls`, `$page_tools` and `$chunk_bytes` — the boundedness leaves below bind 2, 2 and 64 against fixture shapes of 4, 4 and 159, so each crossing is real rather than staged. That drops the two heaviest plants the earlier draft needed (26 api-call rows, a 100 KB blob). The truncation widths (300 / 2000 / 200 / 160) stay SQL literals, so they still need one oversized value each; the records browser's `LIMIT 100` stays a literal too and is the one boundary still out of reach (finding A).

### Planted sentinels

All planted onto **real rows of a copied store**, labeled invented at each call site, because no redacted fixture can carry them:

- **Markup**: `<script>alert(1)</script>` and `![](https://evil.test/px?d=1)` into a real `api_calls.text`, and a `</script>` into a real `tool_calls.result`
- **Oversized values**: `turns.prompt` past 300 chars, `api_calls.text` past 2000, `tool_calls.input` past 200. `raw_records.raw` needs no plant — the recorded 3,054-char record already clears the 160-char preview
- **A transcript-controlled offload name** containing a space and a `%`. Recorded names are random alphanumerics and none of the 636 on this machine repeats, so the awkward name has to be planted
- **Enrichment rows**, written through `EnrichmentStore.upsert` so the keys are the ones the pipeline really writes: one stamped with the current `PROMPT_VERSION`/`TAXONOMY_VERSION`/`DEFAULT_MODEL`, one with a bumped `prompt_version`

Four plants, all small. The paging and chunk-size plants the earlier draft needed are gone: the bound parameters reach those boundaries on recorded shapes.

---

## unit (render) — `tests/view/test_render.py`

`render.py`'s helpers, no I/O and no app. Every input is invented markup, labeled: this is the one place invented data is the only option, since a redacted fixture cannot carry a payload.

- **`html=False` is pinned: a `<script>` in markdown source arrives as literal text.** *Evidence:* render `<script>alert(1)</script>`; assert the output contains no `<script` and carries the escaped literal. Bolded: markdown-it-py's `commonmark` preset sets `html=True`, so the pin is one constructor argument away from being undone and nothing else in the suite would notice.
- **The markdown image rule is off: `![](https://host/px)` produces no `<img>` and no reference to the host.** *Evidence:* render the image syntax; assert no `<img` element and that the host appears in no attribute, with the placeholder text present. Bolded: this is transcript-controlled egress that survives `html=False`, so it is a second, independent hole.
- Linkify is off: a bare URL does not become an anchor. *Evidence:* render a bare `https://host/path`; assert no `href="http` in the output.
- Pretty-printed JSON escapes markup in values. *Evidence:* a record body whose string value holds `</script>` and `<img src=x onerror=y>`; assert both arrive escaped inside the `<details>` block.

## integration (routes) — `tests/view/test_app.py`

FastAPI `TestClient` over `build_app` pointed at the fixture-built store. Nothing mocked; the store is a real DuckDB file and every assertion is on served HTML or status.

- The list holds one row per session with its rollup numbers. *Evidence:* assert 16 rows, and that `4208c1bd…`'s turns, tool calls, agent runs, compactions and PR-link count match `session_rollups` and `pr_links` read directly.
- **An unknown sort or filter key 400s, and a known key's value reaches DuckDB only as a bound parameter.** *Evidence:* `?sort=nonsense` returns 400; `?skill=grill-me` returns the 2 grill-me sessions; and `?skill='; DROP TABLE sessions; --` returns 200 with zero rows, after which `sessions` still holds 16 — a value that reached SQL as text would either error or execute. Bolded: this is the only place user input meets SQL, and the closed dict is the whole defence.
- Every key in the composition dict resolves, and a direction reverses. *Evidence:* parametrize over the dict's keys; assert 200 for each, and that asc and desc are exact reverses on a distinct-valued column — a typo'd column fragment fails here rather than in the browser.
- The footer cites the library query name, the resolved bindings, and the applied sort/filter keys. *Evidence:* assert the cited name is the `.sql` the route loaded and that an applied filter key appears in the citation — the same claim-carries-its-query shape the runner's header owes.
- **The session page's numbers agree with its header, unattributed calls included.** *Evidence:* `0a76f771…`'s 5 NULL-`turn_id` calls hold all $2.39 of its live cost; assert the unattributed row shows that count and cost, and that the timeline's total equals `session_rollups.cost_usd`. Bolded: dropping the row is a silent halving, not an error.
- **A fork does not chip onto its own turn or parent itself.** *Evidence:* `a61a059e3610e6fb4`'s spawning call lives in its own source and resolves to a turn of its own timeline; assert the fork is no turn's chip on `5a88789c…`'s session page, that its own run page lists no child runs, and that it appears exactly once as a child of `acbc29008a04b9702` via `parent_agent_id`. Bolded: the fixtures reproduce the join bug exactly, so this leaf fails loudly if the `source <> id` exclusion is dropped from either the chip join or the child-run join.
- **The unattached-runs section is the chip join's complement, not a list of known causes.** *Evidence:* build the expected set by running the chip join across the fixture store and taking the runs it fails to resolve — five, from three distinct causes — then assert the pages hold exactly that set, each run once. The expectation is computed from the join rather than enumerated, so a run whose cause nobody has seen lands in the section instead of vanishing. Bolded: a section built from a cause list passes today and silently drops rows the first time Claude Code ships a new spawn shape, which is the failure the design's union construction exists to prevent.
- The run page breadcrumbs by both parent rules and shows the continuation section. *Evidence:* `af6473ae…` breadcrumbs to `ac461ef4…` via `parent_agent_id`; `ac461ef4…` has none and breadcrumbs to `main` via its spawning call's source; `afa39469…`'s 2 NULL-turn calls appear under continuation.
- The records browser pages by keyset on `line_no`, never repeating or skipping. *Evidence:* `2352492b…`'s 47 main records; assert successive `?after=` fetches partition the line numbers exactly and that the query text contains no `OFFSET`. The 47 sit under the literal `LIMIT 100`, so this proves the keyset walk, not the page boundary — see finding A.
- **A citation tuple maps to a working URL mechanically.** *Evidence:* take a real `(session_id, source, line_no)` from `raw_records`; assert `/session/{sid}/records/{source}?after={n-1}#L{n}` returns 200, carries that record's row, and contains an element with id `L{n}`. Bolded: this mapping is what the analysis reports' citations are for, and it is the design's stated reason the URL shape is all natural keys.
- A per-value fragment returns exactly one value. *Evidence:* `/fragment/tool/{sid}/{source}/{tool_call_id}` for a real tool call; assert its result is present and no sibling tool call's id appears in the response.
- A transcript-controlled offload name round-trips, and a traversal-shaped name does not escape the store. *Evidence:* plant a name holding a space and a `%` onto the real `offload/` row; assert the listed href is URL-quoted, that following it returns that row's content, and that `../../etc/passwd` returns 404 without touching the filesystem.
- A missing entity 404s rather than 500s. *Evidence:* an unknown session id, run id, source and offload name; assert 404 for each.
- **Every response carries `Content-Security-Policy: default-src 'self'`.** *Evidence:* parametrize over a page, a fragment, a 404 and the 503; assert the header on each. Bolded: it is the second wall behind the image rule, and a header set per-route instead of per-app goes missing on exactly the error paths nobody looks at.
- **A planted `<script>` in a real `api_calls.text` arrives inert through the whole template chain.** *Evidence:* plant the sentinel into a copied store, fetch the turn-expand fragment, and assert no `<script` element with the literal text present. Bolded: `render.py`'s unit leaves cannot see a template that pipes the value through `|safe`. The design calls escaping two non-substituting layers, and this leaf is the second one — dropping it would leave the outer layer unproven.

## integration (boundedness) — `tests/view/test_bounds.py`

The same `TestClient`, against the copy carrying the oversized sentinels. Every boundary here is crossed by binding a fixture-sized value for `$page_calls`, `$page_tools` or `$chunk_bytes` — the fixture shapes (4 calls in the densest turn, 4 tool rows on the densest call, a 159-byte offload file) sit above the bound values, so each crossing is a real overflow of recorded data rather than a staged one.

- **The manifest's production defaults are pinned: `$page_calls` 25, `$page_tools` 40, `$chunk_bytes` 100 KB.** *Evidence:* assert the three defaults by value against the manifest entry, with a comment naming the design paragraph that fixes them. Bolded: every other leaf in this section binds test-sized values, so without this pin the whole section would pass against any defaults at all — including a `$page_tools` of 5,000 that breaks the payload bound in production while CI stays green.
- **No page or fragment exceeds the stated ceiling.** *Evidence:* sweep every route the app exposes against the planted store **at the production defaults**, not the test bindings, and assert each response body is under the ceiling the design states. The sweep is the obligation — a route added later that ships a fat column fails here without anyone remembering to write a test for it. See finding B: the design's own arithmetic at the new defaults comes to ~350 KB while the ceiling is written as ~300 KB, so the number this leaf asserts needs settling first.
- **No query behind a page or a list selects a fat column untruncated, and every nested tool list is capped.** *Evidence:* static read of every viewer `.sql`; assert that any reference to `raw`, `text`, `thinking`, `result`, `input` or `content` outside a per-value query sits inside a `substr(`, and that the turn-expand query's tool subquery carries a `LIMIT $page_tools`. Bolded: the design says the payload bound holds by construction, and this leaf *is* the construction — it needs nothing running and it is the only leaf that generalises past the fixture store to any future session shape.
- SQL truncation holds at each stated width. *Evidence:* the planted `turns.prompt` (>300), `api_calls.text` (>2000) and `tool_calls.input` (>200), plus the recorded 3,054-char raw record against the 160-char preview; assert each rendered preview is cut at its width and the tail is absent from the response.
- **The `$page_tools` cap truncates the nested list and the "+N more" indicator pages the rest.** *Evidence:* bind `$page_tools = 2` against the recorded api call carrying 4 tool rows; assert the fragment shows 2 rows and a "+2 more" indicator, that following the indicator returns the other 2, and that the two fetches partition the 4 with no repeat. The count in the indicator is the leaf's teeth — a cap that renders `LIMIT 2` without a total silently loses rows and looks identical.
- The turn-expand fragment pages calls by keyset and partitions them. *Evidence:* bind `$page_calls = 2` against a recorded turn holding 4 api calls; assert successive `?after=` fetches partition the four `"index"` values exactly, repeating and skipping nothing, and that the query text contains no `OFFSET`.
- The offload route chunks and continues. *Evidence:* bind `$chunk_bytes = 64` over the recorded 159-byte file in `7e37bb35…`; assert the first chunk is exactly 64 bytes, the continuation link carries the next offset, the last chunk is the 31-byte remainder, and the concatenated chunks equal the stored content byte for byte.

## integration (enrichment light-up) — `tests/view/test_enrichment.py`

Two stores: the plain fixture store, and a copy where `EnrichmentStore` has created the tables and upserted rows through its own `upsert`, so the keys are the ones the pipeline really writes.

- Tables absent means no enrichment UI plus the hint. *Evidence:* against the plain store, assert no enrichment column on the list, no card on a session or run page, no chip on a turn row, and the one-line "enrichment not run" hint.
- Tables present means cards at all three levels. *Evidence:* upsert one session, one run and one turn enrichment; assert the list column, the session-page card, the run card on both the chip and the run page, and the description/category chip on the main-turn timeline row.
- **Tables present but empty drops no row.** *Evidence:* create the tables without upserting; assert the list still holds all 16 sessions and the session page still shows all its turns. Bolded: a LEFT JOIN written as an inner join passes every other enrichment leaf and silently empties the list the moment enrichment lands.
- A row whose `(prompt_version, taxonomy_version, model)` differs from the current constants renders `stale vN`; a matching row does not. *Evidence:* upsert two enrichments, one stamped from `PROMPT_VERSION`/`TAXONOMY_VERSION`/`DEFAULT_MODEL` and one with a bumped `prompt_version`; assert the marker on exactly the second, and that the viewer computes no `input_hash` — staleness is version-only by design.
- **The absent path never creates the enrichment tables.** *Evidence:* run the full route sweep against the plain store, then assert `duckdb_tables()` still lacks all three. Bolded: `EnrichmentStore` creates them on open, so a route that reaches for the store's helper instead of a presence check would mutate the file the design calls read-only — and every later "absent" test would pass against tables the viewer itself made.

## integration (store lifecycle) — `tests/view/test_lifecycle.py`

Real stores in real states: a second process holding the write lock, and hand-built stores of the wrong vintage.

- **A store under another process's write lock serves the 503 page, not a traceback.** *Evidence:* a subprocess holding a write connection, with `@pytest.mark.slow` and a why-comment; assert 503, the retry wording, and that the wrapper caught `duckdb.IOException` — the branch the design names. It must be a subprocess: an in-process second connect raises `duckdb.ConnectionException` about a differing configuration instead (both verified 2026-08-07), so a convenient in-process test would prove a branch production never takes.
- The per-request schema check catches a store swapped under a running app. *Evidence:* serve one request against a good store, overwrite the file with one stamped `SCHEMA_VERSION + 1`, and assert the next request returns the version message rather than failing mid-page.
- A foreign or older store refuses at launch, with the message shape `EnrichmentStore._check_base_schema` uses. *Evidence:* reuse `tests/export/test_duckdb.py`'s `old_store` helper and a DuckDB file holding someone else's table; assert the launch path raises the version error naming re-extract.
- Launch on a taken port names the port and the fix. *Evidence:* bind 8477 in the test, attempt launch, and assert the message says to kill the old `aiobserve view` rather than surfacing a bare `EADDRINUSE`.

## integration (query library) — `tests/analyze/test_queries.py`

Shared with `plans/mycelia-analysis/testing_plan.md`. These leaves extend that file's smoke obligations to the manifest field this design adds; whichever slice lands first owns the file, the second adds to it.

- Every viewer `.sql` has a manifest entry, and every entry carries a `scope` of `corpus` or `keyed`. *Evidence:* the existing manifest/glob equality now covers the viewer's files; assert every entry's scope is in the closed set, so a query landing with no scope fails rather than defaulting to one.
- **A `keyed` query is exempt from `--project` and the corpus predicate; a `corpus` query still requires them.** *Evidence:* `aiobserve query run_digest --param session_id=… --param source=…` succeeds with no `--project`; the same omission on a corpus query exits naming `--project`. Bolded: this is the one contract two in-flight implementations share, and the failure mode is a corpus predicate quietly zeroing a keyed query's rows.
- A keyed query's keys are required with no default. *Evidence:* invoke `run_digest` without `session_id`; assert it exits naming the parameter rather than binding a default.
- Every viewer query executes against the fixture store through the shared loader. *Evidence:* the analysis smoke leaf covers this once the viewer's `.sql` files sit in the same directory — named here so the viewer slice knows it inherits the obligation rather than writing a second sweep.

---

## Not covered, and why

- **Browser behavior.** htmx swapping, `<details>` open-on-first-fetch, anchor scrolling. `TestClient` asserts the fragment's HTML and its `hx-get` attributes; whether Chrome swaps it correctly is a manual check, and a headless-browser tier would cost more than it catches at this size
- **The measured timings.** "List join under 30 ms", "every session-page query < 10 ms on the monster session" are claims about a 575-session store on this machine. A 16-session fixture proves nothing about them, and a timing assertion in CI is a flake generator. The design's growth threshold is the trigger to re-measure
- **The `aiobserve extract` side of the collision.** The design says a collision fails extract with DuckDB's lock error and documents it rather than retrying. That is extract's behavior under a held read lock; the viewer's obligation is the reverse direction, which is covered
- **Rendering fidelity.** That markdown *looks* right, that pretty-printed JSON is readable, that the timeline reads as a conversation. Escaping and boundedness are contracts; layout is Nathaniel's eye at manual-launch time, which slice 1 already calls for
- **The 1.27 MB single raw record.** A per-value fetch has no cap by design — the record is the unit. The largest fixture record is 3 KB, and planting a 1.27 MB blob would prove only that DuckDB returns what it stored
- **Auth, multi-store, live tailing.** Out of scope in the design

## Findings for the designer

The amendment resolved all five findings this plan opened against the previous draft: the tool rows are SQL-capped at `$page_tools`, the three sizes are bound parameters with pinned defaults, the 503 branch is named as `duckdb.IOException`, unattached is defined by the failing join, and escaping is stated as two layers. Two small ones remain, both mechanical.

- **A. The records browser's `LIMIT 100` was not parameterized with the other three.** Its densest fixture `(session, source)` holds 47 records, so the keyset page boundary is the one boundary no test can cross. The paging leaf above proves the walk, not the boundary. Same fix as the other three: a `$page_records` with a manifest default. Cheap, and it makes the parameter rule uniform — every page size in the viewer is bound, no exceptions to remember.
- **B. The bound's arithmetic and its stated ceiling now disagree.** At the new defaults, ≤ 25 calls × (2 KB text + 40 tool rows × ~0.3 KB) = 25 × 14 KB ≈ **350 KB**, but both the turn-expand paragraph and the performance paragraph still state ~300 KB. Raising the cap from the observed 32 to 40 moved the product. Either restate the ceiling as ~350 KB or set `$page_tools` to 32, whichever the design means — the sweep leaf asserts against this number and cannot be written until it is settled.

## As built: slice 1

- **The 503 leaf carries no `slow` marker.** Holding the lock from a subprocess costs about 0.1 s, so the marker would have been unearned. The subprocess is still load-bearing, and for a second reason the plan does not name: a holder that does not keep the connection referenced is freed at once and never takes the lock at all
- **Finding B is settled at ~350 KB.** `PAGE_BYTES` in `tests/view/test_bounds.py` is the one place the number lives, and the sweep covers the routes slice 1 exposes
- **The ceiling leaf projects rather than measures.** The 16-session fixture corpus is smaller than one page, so its own weight says nothing about a full one. The leaf takes the marginal cost of a row — the list less the same page holding one session — and asserts `PAGE_SESSIONS` of them fit under the ceiling
- **The fat-column scan carries its own instrument test**, on invented SQL: no shipped query selects a fat column, so a green sweep alone would not show the scan can see one
- **The route-level sentinel leaf landed in slice 1**, ahead of the `render.py` unit leaves it is meant to back up

## As built: slice 2

- **Finding A is answered by a rule rather than by the parameter.** The records browser is slice 3 and its query does not exist, so there is nothing to add `$page_records` to. Instead a leaf reads every `view_*` query and asserts each `LIMIT` is a `$parameter` declared in that query's manifest entry, with its own instrument test on invented SQL. That covers `$page_records` and `$chunk_bytes` before either is written, and it cannot go stale the way a list of three names can
- **The filter leaves check a proper, non-empty cut.** A filter that matched every row would pass a subset assertion while filtering nothing, and one that matched no row would pass it vacuously; the leaf asserts the narrowed list is a strict subset and not empty. The sample values are read off the fixture corpus, and a leaf asserts every `FILTERS` key has one, so a filter added without a case fails rather than going untested
- **The run page's parent-rule leaf carries a fork whose trail is empty.** The corpus holds a fork whose spawning call is in files the store does not hold — both parent rules come back empty — so the leaf pins "the trail stops rather than guessing `main`" on recorded data rather than on a planted row
- **A new query joined the smoke tier by landing.** `view_projects.sql` needed no new leaf: the library tier parametrizes over `queries/*.sql`, so it ran, was scoped, and was checked against its manifest entry the moment the file appeared

## Obligation count

| Area | Obligations |
| --- | --- |
| unit (render) | 4 |
| integration (routes) | 15 |
| integration (boundedness) | 7 |
| integration (enrichment light-up) | 5 |
| integration (store lifecycle) | 4 |
| integration (query library) | 4 |
| **Total** | **39** |
