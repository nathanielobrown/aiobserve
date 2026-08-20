# Design: the session page as a tree-nav node browser

Implements the 17 decisions in `handoffs/handoff_2026_08_19_viewer-node-browser.md` (interview: `viewer_node_browser_questions.md` beside this file). Everything below either follows a decision or resolves a fork the decisions left open. Store facts were probed against `data/traces.duckdb` (schema 7, 575 sessions) and the fixture corpus on 2026-08-19; each carries its query in [Verified numbers](#verified-numbers).

## Problem

The session page is a paged timeline (`after`/`turns`/`chips`) with runs on separate pages and a spend map in a sidebar. Reading one node means scrolling past everything else, and a turn's prompt has no full-value route. The decisions replace all of it with a tree nav plus a single-node pane, under three constraints that shape everything: every node URL renders cold as a full page, no first-party JS (htmx 2.0.6 only), and every response stays under the 500 KB bound that `tests/view/test_bounds.py` enforces by arithmetic.

## Call paths, current → proposed

Current: `GET /session/{sid}` → `app.py:368` → `threads.session_threads()` interleaves digest/runs/compactions → `session.html` renders a windowed timeline; the map arrives separately via `hx-get /fragment/nav/{sid}` on load; runs render at `/session/{sid}/run/{rid}` through `run.html`.

Proposed: `GET <node URL>` → `app.py` → `tree.py` builds the one-open-path tree (the selection's expanded chain, each level's children capped at `KIN`) + `nodes.py` builds the pane (header, enrichment, detail head, children log windowed at `LOG`, `walk.py` prev/next) → `node.html` renders chrome + tree + pane in **one response**. A tree click is `hx-get` of the *same node URL* with `hx-select="#pane"`, `hx-select-oob="#tree-rows"`, `hx-push-url="true"` — cold and warm are byte-identical responses, so one bounds entry covers both, and the tree's scroll survives because only the `<ul id="tree-rows">` inside the stable scroll container is swapped (`hx-preserve` stays available as fallback; the vendored 2.0.6 carries `hx-select-oob`, and history-cache misses re-GET a URL that serves a full page by construction). Inline expansion in the children log fetches `/fragment/body/...` on `toggle once`, exactly today's pattern.

### URL shape (fork: mine)

Kind-first segment; `source` appears exactly where the store's PK carries it (`agent_runs` PK is `(session_id, id)` — no source):

| Node | URL |
|---|---|
| session | `/session/{sid}` |
| turn | `/session/{sid}/turn/{source}/{turn_id}` |
| run | `/session/{sid}/run/{run_id}` (path kept, page rewritten) |
| api call | `/session/{sid}/call/{source}/{api_call_id}` |
| tool call | `/session/{sid}/tool/{source}/{tool_call_id}` |
| compaction | `/session/{sid}/compaction/{source}/{compaction_id}` |
| unattributed-calls bucket | `/session/{sid}/unattributed/{source}` |
| unattached-runs bucket | `/session/{sid}/unattached` |

Rejected: uniform `/{kind}/{source}/{id}` with a placeholder source for runs — a fake segment for a table that has none. Query params every node URL carries: `nav=` (filter preset: absent=full, `noapi`, `agents`), `kin=`, `log=`, `detail=` (size knobs, down-only), `after=` (log cursor). **As built,** the three presets are also three links above the tree rows — the same node under each fold — because a knob nothing on the page names is a knob only its author turns. Both of decision 5's buckets are real nodes with URLs and both renders — the unattributed one per source (children: `view_turn_calls` with `$turn_id` NULL; a run's node gets one too, replacing the run page's continuation section), the unattached one session-scoped (children: the runs `view_runs` resolves to no spawning call — `spawn_source` NULL; a run whose call resolves but has no turn belongs to the unattributed bucket, see below).

### Tree construction (`tree.py`, replacing `threads.py`)

- **Expanded chain** = the selection's ancestors plus the selection itself (its children render too, per decision 3's picture). `DEPTH` bounds the *expanded chain length, selection included*; `ancestry()` raises `ValueError` when the chain exceeds it. Today's deepest chain is a tool call inside a spawn_depth-5 run: 13 ancestors + the selection = **14 expanded levels** (87 such tool calls exist; query below). `DEPTH = 16` leaves one spawn level of margin. A breach is *data* drift — an agent spawning deeper than any recorded session — not schema drift; the crash is still the right arm (the `cursorless_rows`/`MARKS` precedent: a bound the arithmetic depends on is enforced, not stretched), but it makes deep nodes of a legitimate session unreachable cold, so `docs/viewer.md` must state the limit and the margin.
- Each expanded node renders its children capped at `KIN` with a `+N more` tail row linking to that node's full view (whose log pages completely). Ancestry resolves tool→call (`api_call_id`), call→turn (`turn_id`), run→spawning turn via `view_runs`' join (fork self-copy exclusion `tc.source <> a.id` already in it). **Bucket homes are disjoint by the spawning edge:** a run whose spawning call resolves but sits under no turn (`spawn_source` set, `spawn_turn_id` NULL — 9 in the store) parents to that source's *unattributed* bucket, hoisted after its spawning call with the usual tie; *unattached* means the spawning call itself is unresolvable (`spawn_source` NULL). This narrows today's "NULL `spawn_turn_id`, whatever the cause" reading of unattached — the causal edge exists for those 9, so the tree keeps it.
- **Children are defined for every kind × preset** — the builder's function is total, and every visible node has a visible parent in every cell because presets filter children only; the expanded chain always renders whole, hidden kinds included:

| kind \ preset | full | `noapi` | `agents` |
|---|---|---|---|
| session | main turns ⋈ main compactions (by time), then unattributed bucket, unattached bucket | same as full | main-spawned runs (by start), then unattached bucket |
| turn | api calls by index, each run hoisted after its spawning call with `↖ from api call N` | its calls' tool calls and runs, hoisted to the turn in call/tool index order | the runs hoisted under it only |
| run | its turns ⋈ its compactions, then its unattributed bucket | same as full (turn level unchanged) | its child runs (`parent_agent_id`) |
| api call | tool calls by index | same as full | runs its tool calls spawned |
| tool call | none (a `Task` body leads with the run link, decision 13) | none | the run it spawned, if any |
| compaction | none | none | none |
| unattributed bucket | its api calls (`$turn_id` NULL), each run spawned from one hoisted after it with the tie | those calls' tool calls and runs, hoisted | runs spawned from its calls |
| unattached bucket | its runs | its runs | its runs |

- The walk (below) ignores the preset — the filter is a nav view, not a reading order. The hoist tie names the spawning call even when the preset or the `KIN` cut hides its row.
- `meter()` (log-scale spend bar) moves here from `threads.py`; share basis is session spend, bars only on rows with a cost (decision 9).
- Tree row label: enriched description head (with bare `✨`) when present, else prompt/command/agent-type head — merged in Python from the page's one `view_enrichment` fetch.

### Prev/next (`walk.py`)

Depth-first over the whole session in full-preset order, `KIN`-independent (the walk reads SQL neighbors, not the rendered tree): next = first child, else next sibling, else the parent's next sibling, recursively. Compaction rows and both buckets are stops, and **a stop descends** — the walk enters a bucket's children like any node's, so the unattributed calls (store max 163/source) stay inside decision 7's "walk the whole session". Each lookup is one thin per-level query plus the already-resolved chain — no full-walk materialization. Controls show the neighbor's kind plus its enriched description (turns/runs) or its label head (calls/tools/compactions/buckets).

### Queries (in `analyze/queries/`, manifest entries in `queries.py`)

New thin tree queries, all label heads `substr(x, 1, $nav_chars + 1)` for the ellipsis: `view_tree_turns` (generalizes `view_session_nav` with `$source`; replaces it), `view_tree_calls`, `view_tree_tools`. New headers: `view_turn_header`, `view_call_header`, `view_tool_header` — the latter two retire the hard-coded `substr` literals (`view_call_tools.sql:15`, `view_turn_calls.sql:31`) by taking bound params. New Value queries: `view_turn_prompt` (closes the prompt gap), `view_run_brief` (whole task brief). Changed: `view_enrichment` widened with `model`, `enriched_at` (verified missing today); `view_turn_calls`/`view_call_tools` become the turn/call children-log queries, their previews at `$log_chars`, their LIMIT params repointed at `LOG`. Kept as-is: `view_runs`, `view_compactions`, `view_session_header`, `view_run_header`, `session_digest`/`run_digest` (still the session/run logs, windowed by `store.window()` — citable, unlimited). Deleted: `view_session_nav`.

## The 500 KB arithmetic (decision 17)

One response = chrome + tree + pane; per-response bound unchanged. The worst page is a deep run node — a 15-crumb chain, the full tree, and `run_digest` log rows — priced at the repo's **measured** constants, not estimates: today's simpler nav row prices at 754 B through the app (`MEASURED_NAV_NODE_MARKUP`, `test_bounds.py:146-153`) and a digest log row at 5,100 B (`worst_turn_bytes()`, executed 2026-08-19). Replacing `TURNS`/`CHIPS`/`CHIP_BUDGET`/`MARKS`/`NAV`:

| Constant | What it bounds |
|---|---|
| `KIN = Bound(25, 25)` | children per expanded tree node, `?kin=` down-only |
| `LOG = Bound(12, 12)` | children-log page size, all kinds, `?log=` down-only |
| `DETAIL = Bound(4000, 4000)` | chars of one modeled-value head, `?detail=` down-only |
| `LOG_CHARS = 300` | a log row's text preview (manifest param) |
| `DEPTH = 16` | expanded chain length, selection included — a backstop like `MARKS`, not a knob; `ancestry()` raises past it |
| `TREE_ROW_BYTES = 800` | the pinned worst price of one tree row (754 B measured floor + glyph + tie); a row template that busts it is a slice failure, not a knob to turn |

Worst case at 5 B/escaped char: tree ≤ `1 + DEPTH×(KIN+1)` = **417 rows**. The `+1` is the level's tail row, and `KIN` covers every child including the one the open path descends through — `tree._kin` keeps that child *inside* the cap, because a rescue added past it would put every level at `KIN+1` and the page 16 rows over its price. **As built (measured through the app, slice 6, re-measured after the pane-swap fix):** tree 417 × 914 B = 381,138 + crumbs 16 × 558 = 8,928 + log 12 × 1,616 = 19,392 + 2 detail heads × 20,600 = 41,200 + chrome 16,000 = **466,658 of 500,000, headroom 33,342 B**. Slice 6 pinned the row at 983 B, with 5,669 B spare. Landing a clicked node in the pane rather than inside the link clicked took two more htmx attributes, `hx-target` and `hx-swap`; writing the five a row shares once on `#tree-rows` instead of 417 times over more than paid for them and took the row to 914 B, while a log row, which writes the swap out, rose to 1,616 B and the chrome to 16,000 B. The estimate above the line read ≈473 KB with 27 KB spare: it over-priced the digest log row by ~43 KB in total and under-priced the tree row by ~183 B each, and every link now carries the reader's knobs (38 B a copy, twice per tree row). `MARKS` dies (compactions count inside `KIN`/`LOG`); `CALLS`/`TOOLS` fold into `LOG`; `CURSORLESS_TURNS`, `RECORDS`, `CHUNK`, `SESSIONS`, `PROJECTS` stay. Every new route joins `ROUTES` and the manifest pin in `test_bounds.py` — the sweep fails otherwise. Store maxima the caps cut (829 calls/turn, 106 runs/turn, 212 main-spawned runs) stay reachable through the paged logs. The final byte pin lands in slice 6, after glyphs, `title` text, and the ellipsis exist — pinning earlier would pin a page that is still growing.

**Truncation-ellipsis fork: IN for the new surfaces, OUT for the sessions list.** Every new cut query selects `$n + 1` chars and a `format.cut()` helper trims to `$n` plus `…` — building the new surfaces without it means rebuilding them immediately. The sessions list (`listing.py`, untouched here) keeps today's behavior; retrofitting it is the separate small PR the followups handoff imagined. No `title="full text"` anywhere — that would put an uncut fat column in a bounded response.

## Presentation calls

- **Tooltips: native `title=`** is the project pattern (documented in `.claude/rules/viewer-ui.md`). Rejected: CSS `::after` tooltips — styling upside isn't worth a positioning convention, and both are hover-only anyway.
- **Glyph:** `✨` prefixes every model-written string; in the pane it sits in a `<span title="{model} · {enriched_at} · prompt v{n} · taxonomy v{n} · {fresh|stale}">`, in the tree it's bare (decision 10). `agent_runs.description` renders as **"task brief"** (`labels.py`), never "description".
- **Pygments** (new runtime dep in `pyproject.toml`; already in `uv.lock` transitively via pytest/rich): `HtmlFormatter` with CSS classes — CSP `default-src 'self'` blocks inline `style` attributes — stylesheet checked in at `static/pygments.css`. JSON (tool input/result, raw records; applied when the value parses as JSON) and SQL (`/query/{name}`) only. Above `HIGHLIGHT_CHARS = 256_000` — deliberately **characters** (`len()`), not decision 13's suggested 256 KB: equal for the ASCII payloads tools write, up to 4× more bytes for multibyte text, an accepted deviation because the ceiling guards CPU and inflation, not a wire budget — plain text plus a line saying why. The ceiling also caps highlight inflation on the Value routes.
- **Citations** collapse into a footer `<details>`; each name links to `/query/{name}?{bindings}`, a new route serving the SQL file (name validated against `queries.QUERIES`, so no path traversal) highlighted, with the page's bindings beside it.
- **Detail block:** modeled value head at `DETAIL` with the whole-value fetch beneath, then the raw archived line in a closed `<details>` fetching `/fragment/record` on open (`view_turn_records` supplies the line). `command_args` gets a header head only — its full value is deliberately reachable via the raw record, not a Value route.

## File-tree diff

```
src/aiobserve/view/
  tree.py, walk.py, nodes.py, highlight.py    new
  threads.py                                  deleted (meter() moves to tree.py)
  app.py        node routes ×8, /query, /fragment/body ×2 (run + kinded), value fragments prompt/brief;
                /fragment/{nav,turn,tools} deleted
  store.py      Page/Fragment/Value enums: +8 names (tree ×3, headers ×3, prompt, brief), −SESSION_NAV
  bounds.py     KIN, LOG, DETAIL, DEPTH, TREE_ROW_BYTES, HIGHLIGHT_CHARS in;
                TURNS, CHIPS, CHIP_BUDGET, MARKS, NAV out
  format.py     + cut() ellipsis helper       enrichment.py  + model/enriched_at/title text
  templates/    node.html, _node_body.html, _tree.html, query.html new;
                session.html, run.html, fragments/{nav,calls,tools}.html and the timeline/nav_nodes/
                run_list/prompt_heading macros deleted
  static/pygments.css                          new
analyze/queries/  view_tree_{turns,calls,tools}, view_{turn,call,tool}_header,
                  view_turn_prompt, view_run_brief new; view_enrichment, view_turn_calls,
                  view_call_tools changed; view_session_nav deleted; queries.py manifest updated
tests/view/    test_tree.py, test_walk.py, test_node.py, test_query.py new; test_nav.py deleted;
               test_run.py and test_app.py session sections rewritten; test_bounds.py ROUTES,
               manifest pin, measured-markup constants, and worst-case functions rewritten;
               conftest pages()/chipped() updated
tests/analyze/test_queries.py   binds every manifest query by name: 8 additions, view_session_nav removed
docs/viewer.md rewritten (incl. the DEPTH limit and its margin); docs/store.md + CLAUDE.md privacy line;
.claude/rules/viewer-ui.md new
pyproject.toml + pygments
```

## Key contracts

- One body, two mounts: `_node_body.html` macros render header + enrichment + detail per kind; the full view wraps with breadcrumb + children log + prev/next; `/fragment/body/...` serves the body alone, children as a count + link (decision 1).
- `tree.py`: `ancestry(conn, node) -> list[Node]` (raises when chain + selection exceed `DEPTH`), `kin(conn, node, preset, cap) -> Paged`-shaped children, total over the kind × preset table above; `Node = (kind, source | None, id, label, glyph, cost, meter, tie)`.
- `walk.py`: `neighbors(conn, node) -> tuple[Step | None, Step | None]`, `Step = (node, kind_label, description | None)`.
- Every request value binds as a named parameter; the citation contract (`queries.citation`) is unchanged.

## Chosen test seam

Route level, unchanged pattern: session-scoped `TestClient` over the fixture corpus, assertions on `data-*` attributes, `plant()` for values fixtures lack (a `DEPTH` breach plants a too-deep run chain; the `DETAIL` cut and `LOG_CHARS` preview plant lengthened values). Fixture fan-out maxes at 4 children, so `?kin=`/`?log=`/`?detail=` down-only knobs are what make every cap tail testable — they exist for that reason. Fixtures already hold nested runs (depth 2), 6 compactions, 5 unattached runs, unattributed calls, slash-command turns (probed this run).

**Residual the seam cannot reach:** decision 16's observable promise — a tree click pushes the URL and the tree's scroll offset survives the swap — is htmx executing in a real browser; `TestClient` asserts only the server-side preconditions (the `hx-*` attributes, byte-identical cold/warm responses, the stable `#tree-rows` container). Resolution: a scripted manual browser pass in the PR flow, run via Chrome automation against a running viewer before the PR opens — click a node deep in a scrolled tree, confirm the URL bar changed, confirm the tree's scroll offset is unchanged, press back and confirm the prior node renders. Rejected: accepting the residual unchecked — the scroll mechanism is this design's substitution for the decision's stated `hx-preserve`, so it is exactly the claim that needs one witnessed run. **As built,** that run happened in a real Chromium on 2026-08-20, and what it settled is recorded in `.claude/rules/viewer-ui.md`: the scroll survives because `#tree` carries the scrollbar and only `#tree-rows` inside it swaps, so `hx-preserve` was never needed.

## Slices (= the PR's commits, in order)

1. **List-layer fixes** (survive the rewrite): a test that follows the pager's minted page-2 href and asserts the second page's rows; a boundary test submitting all nine `LIST_KEYS` and asserting 200 — **no code change**: `narrowing()` with nine valid keys returns normally (executed this run; the `<=` admits equality); the "decile"→log-scale As-built clause in `plans/viewer-ui-overhaul/testing_plan.md`; the permanent far-future-date guard for windowed queries (PR #4's discarded plugin, made a test).
2. **Seam slice:** turn node page + tree for the session→turn chain + `hx-select` swap + its `ROUTES`/manifest entries. Proves one-body-two-mounts, cold=warm, and the bounds gate on one kind.
3. **All kinds:** remaining node routes (rewriting `/session/{sid}` and `/run/{rid}` in place), both buckets, hoist + tie, children logs, raw-record details, prompt/brief value routes; delete `threads.py`, old templates, old bounds; interim arithmetic entries in place. Verify: bounds tier + rewritten `pages()` sweep green.
4. **Walk:** prev/next including nested-run pop-up, compaction and bucket stops (fixture `5a88789c` has the depth-2 chain).
5. **Filter presets:** `?nav=`; test per preset × kind cell that children match the table and every visible node has a visible parent.
6. **Pygments + `/query` + glyphs/tooltips + `view_enrichment` widening + `cut()` ellipsis — then the final byte pin:** re-measure the row constants (`TREE_ROW_BYTES` among them) through the app and pin the worst-case functions, now that every byte the page will carry exists.
7. **Guidance:** `CLAUDE.md` privacy ("the store keeps everything" — store only, fixtures stay redacted), `docs/store.md`, new `.claude/rules/viewer-ui.md`, `docs/viewer.md` rewrite (via doc-sync at PR time).
8. **Mutation triage last:** `mise run mutate` over the PR's changed files; classify survivors real-gap / equivalent / not-worth-it; close the real ones.

## Decisions (with the rejected alternative)

1. One route per node, `hx-select`/`hx-select-oob` over the full-page response — rejected twin `/fragment/pane` routes: doubles `ROUTES` and splits the arithmetic; ~15 KB of chrome per click is the price.
2. Kind-first URLs, `source` only where the PK has one — rejected uniform shape with placeholder source.
3. `DEPTH` raises past 16 expanded levels — rejected degrading (collapsing ancestor levels): the bound is what the arithmetic stands on, and a session deeper than any recorded (today's max: 14) is data the design was not priced for; crash loudly rather than serve an unpriced page. The cost — deep nodes of such a session are unreachable cold — is stated in `docs/viewer.md`.
4. Presets filter children, never the expanded chain; walk ignores presets — rejected per-preset walk orders (three reading orders to test for one reading need).
5. Native `title` tooltips — rejected CSS `::after`.
6. Ellipsis in for new surfaces, out for the list — rejected all-in (unrelated `listing.py` churn) and all-out (immediate rebuild of every new cut).
7. Session/run logs stay on `session_digest`/`run_digest` windowed — rejected new log queries: the digests are what reports cite. Their measured 5.1 KB row is why `LOG = 12`; rejected shrinking the digest row instead (it is the row reports quote).
8. Tree label prefers enriched description — rejected prompt-head-always: the model-written line is the better handle and the glyph marks its source.
9. Both buckets as real nodes with URLs and walk stops that descend — rejected inline-only tail groups: pane content unreachable cold breaks decision 2's rule, and a non-descending stop drops up to 163 unattributed calls out of decision 7's walk.
10. `TREE_ROW_BYTES` pinned as a measured constant, final pin in slice 6 — rejected "implementer re-prices and adjusts the caps": the caps are reader-facing contract; the row budget, not the cap, is the degree of freedom.

## Out of scope

- Keyboard navigation, any first-party JS (decision 16); the sessions-list ellipsis retrofit; highlighted diffs/Python/shell (decision 13); recursing expansions one more level (interview Q7 "later"); the empty `project_dir` fail-fast (belongs at extract); enrichment content changes — the viewer only widens what it reads; redacting the store (explicitly reversed: the store keeps everything).

## Open questions

- Whether the `+N more` tree tail should carry the count of *hidden* preset-filtered kinds too, or only the kin cut — I designed count-of-cut only; a preset already says what it hides.

## Verified numbers

Store `data/traces.duckdb`, schema 7, 575 sessions, probed 2026-08-19 (queries in the probe: `GROUP BY` counts over `live_*`): max main turns/session 79; max api calls/turn 829; max tool calls/call 32; max runs per spawning turn 106; max spawn_depth 5; 87 tool calls inside spawn_depth-5 runs (deepest expanded chain: 14 levels); max compactions/source 18; max unattributed calls/source 163; max unattached runs/session 17; max main-spawned runs/session 212. Measured prices (executed via `test_bounds.py` machinery): nav row 754 B, digest log row 5,100 B. Fixture corpus (built via `tests/conftest.py:build_store`): 8 runs, max depth 2, 6 compactions, 5 unattached, max fan-out 4.
