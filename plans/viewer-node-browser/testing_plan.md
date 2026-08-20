# Viewer node browser — testing plan

Obligations for `plans/viewer-node-browser/design.md`, one leaf per behavior, tagged with the
slice that must turn it green (`[S1]`–`[S8]`; S7 is docs and carries none). The seam is the
design's: session-scoped `TestClient` over the fixture corpus, expectations derived from the
served store, reads through `values()`/`fields()`/`inside()` on `data-*` attributes — never
rendered prose, never the database instead of the page — `plant()` for values the redacted
fixtures lack, bounds by arithmetic plus measured constants in `tests/view/test_bounds.py`.

Obligations the seam cannot reach are collected under **Seam limits** at the end — they are
findings for the designer or deliberate exclusions, never leaves quietly weakened.

## Facts re-probed against the fixture corpus (this run, 2026-08-19)

The design's numbers come from the canonical store; the suite runs on `corpus_db`
(`tests/conftest.py:build_store`). Re-probed by building the corpus and querying it:

| Fact | Fixture corpus |
| --- | --- |
| Sessions | 18 |
| Runs | 8, max `spawn_depth` 2 — nested chains in `5a88789c…` (auditor→fork) and `4208c1bd…` |
| Max fan-out | 4 everywhere: calls/turn 4, tools/call 4, main turns/session 4 |
| Compactions | 6, max 2 per source (`1de7cf38…` main) |
| Unattached runs (spawning call unresolvable) | 5 |
| Runs spawned from an unattributed call | **0** — the store's 9 have no fixture analogue; the disjoint-bucket boundary is plant-only |
| Unattributed calls | main max 7 (`turn_id` NULL); run-sourced buckets exist too (2 and 1) |
| Deepest expanded chain (derived from depth 2) | ~10 levels — a `DEPTH = 16` breach is plant-only |

Consequence: every cap tail (`KIN = 25`, `LOG = 12`, `DETAIL = 4000`) is unreachable from
recorded data. The design's down-only `?kin/?log/?detail` knobs exist for exactly this; cap
leaves drive the knob below the fixture's fan-out instead of planting hundreds of rows.

## Obligations

### unit — `tests/view/test_format.py`, no store; constructed values at the boundary

- `cut(text, n)` returns text unchanged at ≤ n chars, and n chars plus `…` when the query's
  `$n + 1` fetch overflows — the one-extra-char protocol every new cut query rides.
  *Evidence:* `[S6]` parametrized whole-string comparison at n−1, n, n+1 chars; invented
  strings, labelled as such (no recorded value sits on a cut boundary)

### route — `tests/view/test_app.py`, list layer (the PR's first commit, against today's code)

- **A reader can follow the pager.** The test scrapes the page-2 href the list itself mints
  (via `list_url`) and GETs *that string*, asserting the second page's rows continue the
  first with no repeat or gap, filters still riding. *Evidence:* `[S1]` the dereferenced
  href at `?size=5` over the 18-session corpus; kills the surviving `page > 1 → page > 2`
  mutant in `listing.py:list_url`
- A fully-filled form is legal: all nine `LIST_KEYS` submitted with valid values returns 200
  and rows. **No code change** — `narrowing()`'s `<=` admits equality (verified by execution,
  Explore fact 4; the audit reproduced it, noting `errors=1` not `errors=yes`). *Evidence:*
  `[S1]` the nine-key request and its 200; kills the `<=` boundary mutant
- Non-test S1 items carried for completeness: the "decile" → log-scale As-built clause in
  `plans/viewer-ui-overhaul/testing_plan.md` (a doc edit, no leaf)

### analyze tier — `tests/analyze/`, real corpus through the CLI runner

- **The windowed-query tier stays green at a far-future date.** PR #4 proved the pattern with
  a throwaway plugin faking `dt.date.today()` to 2030 — it caught `select_sessions` days from
  aging out — then discarded it. Make it permanent: the tier runs with the clock faked (the
  only `today()` read is `cli.py:165`, the `--as-of` default), so any future query or test
  binding that leans on the wall clock goes red now, not the morning the corpus recedes.
  *Evidence:* `[S1]` the permanent conftest fixture pinning the fake date and the tier
  passing under it
- Every new query runs against the corpus and returns rows: `view_tree_{turns,calls,tools}`,
  `view_{turn,call,tool}_header`, `view_turn_prompt`, `view_run_brief` join
  `FIXTURE_BINDINGS`; `view_session_nav` leaves it. *Evidence:* `[S2/S3]` the existing
  parametrized `test_every_query_runs` sweep, which fails on an unbound required param; the
  no-clock and params-match scans cover the new SQL for free

### route — `tests/view/test_node.py` (new), node pages over `corpus_db` + `enriched_db`

- **One body, two mounts.** A turn node's full view carries breadcrumb, children log, and
  prev/next; `/fragment/body/...` for the same node serves the body alone with children as a
  count and a link — decision 1's no-recursive-accordion rule. *Evidence:* `[S2]` the
  `data-*` field sets of both responses compared: body fields equal, wrapper fields present
  only on the full view
- **Cold equals warm, byte for byte.** The same node URL fetched bare and with htmx request
  headers returns identical bytes — what lets one bounds entry price both. *Evidence:*
  `[S2]` byte equality of the two responses
- Every node kind renders its header facts and 404s on `MISSING`: turn, run, api call, tool
  call, compaction (trigger + `pre_tokens → post_tokens` drop), both buckets. *Evidence:*
  `[S2/S3]` one leaf per kind against store-derived expectations; the `MISSING` id per route
- The detail block shows the modeled value head at `DETAIL` with the whole-value fetch
  beneath and the raw archived line in a closed `<details>` wired to `/fragment/record`.
  *Evidence:* `[S3]` planted value longer than `DETAIL`; head length and the fragment URLs
  read from attributes
- **The prompt gap closes.** A turn's whole prompt is served by the `view_turn_prompt` Value
  route, and a run's whole task brief by `view_run_brief`; a planted oversized value renders
  proportional to the store, not the page cap. *Evidence:* `[S3]` planted long prompt/brief
  round-tripped whole through the value routes
- `agent_runs.description` is labelled "task brief" everywhere it renders — never
  "description". *Evidence:* `[S3]` the `data-field` key on run header and tree row
- A `Task` tool call's node leads with the link to the run it spawned. *Evidence:* `[S4]`
  (agents-cell data) the run URL read from the tool node's body
- The children log pages by keyset: following its own `after` continuations shows every
  child exactly once, in order, no OFFSET. *Evidence:* `[S3]` the walk-the-pages loop at
  `?log=1` over a 4-child fixture node, in the shape of today's turn-fragment leaf
- Down-only knobs enforce their ceilings: `?kin`, `?log`, `?detail` above the `Bound`
  ceiling → 400; below it, honored. *Evidence:* `[S3]` the `checked()` pattern per knob
- Enrichment renders on the partly-described store: a described item shows `✨` + description,
  an undescribed one shows neither, and the page renders either way. *Evidence:* `[S3]`
  `enriched_client` (described-but-for-one at each level) leaves per level
- The pane glyph's `title` names model, `enriched_at`, prompt and taxonomy versions, and
  staleness — `view_enrichment` widened; a version-bumped plant reads stale. *Evidence:*
  `[S6]` `enriched_plant` bumping `prompt_version`; the `title` attribute read whole
- Tree glyphs are bare — no `title` — decision 10's byte saving. *Evidence:* `[S6]` absence
  of `title` on tree-row glyph spans

### route — `tests/view/test_tree.py` (new), tree construction as served HTML

- The tree renders exactly one open path: the selection's chain expanded (children of every
  chain node, selection included), nothing else expanded. *Evidence:* `[S2]` the
  `data-*` row set for a depth-selected node in `4208c1bd…` compared to the store-derived
  chain + capped children, and no rows from a sibling's subtree
- Every tree row is an `hx-get` of its own node URL with `hx-select="#pane"`,
  `hx-select-oob="#tree-rows"`, `hx-push-url="true"`, and `#pane`/`#tree-rows` occur exactly
  once in the page — the server-side preconditions of decision 16's scroll mechanism.
  *Evidence:* `[S2]` attribute reads plus an id-uniqueness count
- The `+N more` kin tail appears when children exceed the cap, links to the node's own full
  view, and N is the cut count. *Evidence:* `[S3]` `?kin=2` on a 4-child fixture node: 2
  rows, tail row, N = 2
- Runs hoist under the turn immediately after their spawning call with the `↖ from api call
  N` tie, and the tie names the call even when its row is hidden. *Evidence:* `[S3]` fixture
  run hoisted in full preset; `[S5]` same tie under `?nav=noapi` where no call row renders
- **The two buckets are disjoint by the spawning edge** — the audit's remaining fix, pinned:
  a planted run whose spawning call resolves but has `turn_id` NULL parents to that source's
  *unattributed* bucket (hoisted after its call, tie intact) and is absent from *unattached*;
  its breadcrumb agrees with its tree placement. *Evidence:* `[S3]` the plant (an UPDATE
  nulling a spawning call's `turn_id` — no fixture holds the shape, per the probe table) and
  the run's single home asserted in both renders
- Both buckets are real nodes: their URLs render cold with their children (main's 7
  unattributed calls; the 5 unattached runs), and a run node carries its own unattributed
  bucket in place of the old continuation. *Evidence:* `[S3]` cold GETs of
  `/session/{sid}/unattributed/main`, `/session/{sid}/unattached`, and a run-sourced bucket
- Tree labels prefer the enriched description head (bare `✨`) and fall back to
  prompt/command/agent-type head. *Evidence:* `[S3]` fallback on `client`; `[S6]` preference
  on `enriched_client`, both rows read by `data-*`
- Spend bars sit only on rows with a cost — turns, runs, api calls — never tool calls, on the
  same `s1..s10` log scale. *Evidence:* `[S3]` meter attributes on fixture cost rows and
  their absence on tool rows
- **`ancestry()` raises past `DEPTH`** — the crash arm, not a degrade: a planted spawn chain
  whose expanded length reaches 17 makes the deep node's URL raise (selection-inclusive
  definition, `DEPTH = 16`). *Evidence:* `[S3]` the plant building the chain row by row (the
  corpus tops out near 10 levels, per the probe table) and the propagated error naming the
  bound; a 16-level chain still renders
- Labels arrive pre-cut with the ellipsis protocol: a planted `NAV_CHARS`-overflowing prompt
  shows `NAV_CHARS` chars plus `…`; a log row's preview cuts at `LOG_CHARS` the same way.
  *Evidence:* `[S6]` planted 49-char and 301-char values, the rendered heads compared whole

### route — `tests/view/test_tree.py`, filter presets

- **Each kind × preset cell of the design's table renders the children it defines**, 8 kinds
  × 3 presets, parametrized; the three unattributed-bucket run cells ride the disjointness
  plant above (no fixture data — probe table). *Evidence:* `[S5]` per-cell children compared
  to a store-derived expectation; compaction and same-as-full cells included so a table edit
  is a test edit
- **Every visible node has a visible parent, and the expanded chain renders whole, hidden
  kinds included:** a turn selected under `?nav=agents` and an api call under `?nav=noapi`
  still show their full chains. *Evidence:* `[S5]` the two hidden-kind selections plus a
  sweep asserting each rendered row's parent row is rendered
- Presets ride the URL: every tree row href and pager link under `?nav=` carries it.
  *Evidence:* `[S5]` href scan in the shape of the list's filter-riding leaf

### route — `tests/view/test_walk.py` (new), prev/next

- **Next from the session node walks the whole session depth-first and prev walks it back:**
  every node — turns, calls, tools, runs (entering and popping out of nested `5a88789c…`),
  compactions, both buckets *and their children* (stops descend) — visited exactly once, in
  full-preset order. *Evidence:* `[S4]` the follow-the-links loop compared to a store-derived
  DFS; the reverse loop equal to its mirror
- Controls name the neighbor's kind plus its enriched description (turns/runs) or label head
  (calls/tools/compactions/buckets). *Evidence:* `[S4]` control `data-*` reads; `[S6]` the
  description variant on `enriched_client`
- The walk ignores presets and `?kin=`: neighbors under `?nav=agents` and `?kin=1` equal the
  full-preset neighbors — the walk reads SQL, not the rendered tree. *Evidence:* `[S5]` the
  same node's controls compared across presets and knob settings

### route — `tests/view/test_query.py` (new), citations and highlighting

- Citations collapse into the footer `<details>` and each cited name links to
  `/query/{name}?{bindings}` carrying the page's bindings. *Evidence:* `[S6]` the citation
  hrefs read from a node page and dereferenced to 200
- `/query/{name}` serves only names in `queries.QUERIES`: an unknown or traversal-shaped
  name (`../../secret`) is 404, never a file read. *Evidence:* `[S6]` the traversal probe
  and the 404
- JSON tool input and SQL files highlight via CSS classes — no inline `style=`, CSP intact —
  and `/static/pygments.css` serves. *Evidence:* `[S6]` class-bearing spans in the
  response, zero `style=` attributes in the highlighted block, the stylesheet's 200
- Above `HIGHLIGHT_CHARS` the value serves plain with the line saying why; the ceiling is
  characters, not bytes — a multibyte value under the char ceiling but over 256 KB of bytes
  still highlights (the design's named deviation, pinned so it stays deliberate).
  *Evidence:* `[S6]` planted values one char either side of the ceiling, plus the invented
  multibyte case, labelled as such

### bounds tier — `tests/view/test_bounds.py`, arithmetic + measured constants + sweeps

- Every new route joins `ROUTES` and every route the app exposes is swept — the existing
  set-equality gate does the enforcement; the obligation is the rewritten dict: 8 node
  routes, `/query/{name}`, both `/fragment/body` shapes, prompt/brief value routes, the
  dead fragments gone. *Evidence:* `[S2]` the turn-node entries; `[S3/S6]` the rest;
  `test_every_route_the_viewer_exposes_is_in_the_payload_sweep` green
- `pages()` in conftest enumerates every node URL of every kind — every turn, call, tool,
  compaction, run, and bucket the store holds — and the sweep holds each under `PAGE_BYTES`
  at 200. *Evidence:* `[S3]` the rewritten `pages()` and the sweep
- The manifest pin asserts exactly the new bound set: `KIN`, `LOG`, `DETAIL`, `RECORDS`,
  `CHUNK`, `SESSIONS`, `PROJECTS` as `Bound`s plus `DEPTH`, `TREE_ROW_BYTES`,
  `HIGHLIGHT_CHARS`, `LOG_CHARS`, `CURSORLESS_TURNS`; `TURNS`/`CHIPS`/`CHIP_BUDGET`/
  `MARKS`/`NAV` gone. *Evidence:* `[S3]` the pin test's exact-set failure on any drift
- The fat-column scan covers every new query: tree and header queries bounded, the two
  retired hard-coded `substr` literals (`view_call_tools.sql:15`, `view_turn_calls.sql:31`)
  now bound params, `view_turn_prompt`/`view_run_brief` declared `Value` and asserted to
  select their fat column whole. *Evidence:* `[S3]` the enum updates in `view/store.py`
  (+8, −`SESSION_NAV`) feeding the existing parametrized scans
- **The worst page prices under 500 KB at measured constants:** tree `1 + DEPTH×(KIN+1)` =
  417 rows × `TREE_ROW_BYTES` + pane (16 crumbs, header, enrichment, 2×`DETAIL`×5 body,
  `LOG`×measured digest row, controls) + chrome ≈ 472.6 KB — the audit's re-derived number,
  pinned as arithmetic the constants feed. *Evidence:* `[S3]` interim worst-case functions;
  `[S6]` the final pin after glyphs, titles, and ellipses exist
- **A worst tree row measures ≤ `TREE_ROW_BYTES = 800` through the app** — label at
  `NAV_CHARS` planted full of `&`, meter, glyph, and the hoist tie all present. Only 46 B of
  slack sits over the 754 B measured floor, and a tie row on today's markup would brush
  ~840 B: a bust is a loud slice-6 failure demanding a slimmer template, never a quiet cap
  raise (design decision 10). *Evidence:* `[S6]` the measured-markup leaf in the shape of
  `MEASURED_NAV_NODE_MARKUP`, re-measured with every new byte on the row
- The digest log row constant (5,100 B measured) is re-measured and feeds the log line item.
  *Evidence:* `[S6]` the repointed `worst_turn_bytes()` machinery

### mutation — `mise run mutate` over the PR's changed files

- Survivors in the new modules (`tree`, `walk`, `nodes`, `highlight`, the rewritten `app`
  regions) classified real-gap / equivalent / not-worth-it, real gaps closed with leaves
  above or new ones. *Evidence:* `[S8]` the triage list carried into the PR body, each
  real-gap survivor paired with the leaf that kills it

## Deliberately not covered

- **htmx client execution** — the swap actually replacing `#pane`, `hx-select-oob` leaving
  the tree's scroll intact, `hx-push-url` history, the history-restore re-GET. No browser
  runs in the suite (it never has); the server-side preconditions are pinned instead:
  attribute wiring, unique target ids, cold=warm byte identity, and every URL serving a full
  page. See Seam limits
- Live-server lifecycle beyond the existing `test_lifecycle.py`, which is untouched
- The canonical store's maxima (829 calls/turn, 212 runs) as rendered pages — the arithmetic
  and the knobs bound them; no fixture will ever hold them
- Visual appearance: pygments colors, meter geometry, tree indentation

## Seam limits (report to the designer, not weaker tests)

1. **Scroll preservation and URL push are untestable at the chosen seam.** Decision 16's
   observable promise — a tree click keeps the reader's scroll position and updates the
   address bar — is htmx behavior in a browser the suite doesn't run. The plan pins every
   server-side precondition (wiring attributes, id uniqueness, byte-identical cold/warm,
   full-page-everywhere), but if the mechanism itself regresses (say, `hx-select-oob`
   against a wrong id), only manual use catches it. The design accepts this implicitly
   (`hx-preserve` named as fallback); it should say so explicitly, or name a manual check
   in the PR flow
2. No other obligation was found unreachable: every cap tail rides the down-only knobs, the
   `DEPTH` breach and the bucket-disjointness boundary ride `plant()` (both absent from the
   fixture corpus — probe table above), and cold reachability is a sweep
