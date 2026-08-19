# Viewer UI overhaul — design

Store facts below were probed read-only against the canonical store (`data/traces.duckdb`, schema 7, 2026-08-18). Enumerated counts are hypotheses to re-check at implementation time; the queries are one-liners over `session_rollups` / `live_turns` / `live_agent_runs`.

## Problem

The viewer is complete but flat. The session list prints 14 near-equal columns of raw integers (six of them unformatted); the landing page answers "which session" but not "which project, lately"; the session page is a turn stream with no map — on a 79-turn session with 240 agent runs there is no way to see the shape of the work or where the money went. And every slash-command turn renders its raw `<command-message>…</command-message><command-name>/…</command-name>` cruft, because `_parts.html` renders `prompt` and never reads the `command_name`/`command_args` columns the extractor already stores (verified: 423 live turns carry `command_name`, every one with a prompt starting `<command-`; 0 counterexamples).

The constraints that decide the shape: CSP `default-src 'self'`, zero first-party JS today, no build step, and the 500 KB arithmetic page budget (`bounds.py` + `tests/view/test_bounds.py`). Every new surface must state its bound.

## Information hierarchy

One rule throughout: **identity and outcome first, volume second, provenance dim.** A row's first read is *when, what, what it cost, did it go wrong*; counts of calls and tokens are texture. The instrument for this is the **two-line cell**: primary value at body size, secondary value below it at 0.75rem in `--dim`. That halves the column count without dropping data, and it is the pattern Nathaniel asked for by name.

## Call paths, current → proposed

**Landing.** Now: `GET /` → `session_list` (`app.py:165`) → `sorted_sessions` over `view_sessions.sql`. Proposed: `GET /` → `projects_page` → new `view_project_rollups.sql` over **`corpus_rollups`** (a resume copies its ancestor's records; summing `session_rollups.cost_usd` across sessions double-counts spend — `docs/store.md`). The session list moves whole to `GET /sessions`; `listing.list_url` re-points; each project row links to `/sessions?project=<root>`.

**Session page.** Now: `session_page` (`app.py:276`) → windowed `session_digest` + `threads.session_threads` → single-column `session.html`. Proposed: same route, same digest window, same fragments — `session.html` becomes a two-pane grid: `<nav id="map">` left, existing header + timeline right. The map itself is a new htmx fragment, `GET /fragment/nav/{session_id}` (`hx-trigger="load"` on the sidebar, the turn-fragment precedent): it runs one new whole-session query `view_session_nav.sql` (one cheap row per main-thread turn) and `threads.nav_tree(...)` composing those rows with the run forest `session_threads` builds. Timeline fragments, htmx triggers, records browser, run page: unchanged.

**Command turns.** Now: `_parts.html:70` renders `entry.row.prompt` raw. Proposed: `session_digest.sql`/`run_digest.sql` add `substr(command_args, 1, 300) AS command_args`; the turn heading macro renders, when `command_name` is non-NULL, a `/name` badge plus the args head — badge alone when args are empty (a bare `/command` invocation; the empty string is a recorded shape, not an absence) — and falls back to the raw prompt when `command_name` is NULL. No extract or schema change. Bounds price the heading as the **max of its two arms**, not their sum — the macro renders args *instead of* the prompt, so the worst row is the prompt arm plus the badge's few bytes (summing the two 300 × 5 B heads would put the page at ~513 KB and fail `test_bounds.py` with no nav at all). `command_args` (store max 7,947 chars, 56 rows over 300) joins the fat-column scan, and `prompt` — absent from it today, a pre-existing gap this change leans on — joins alongside.

## Key contracts

```python
# format.py — the one place numbers are shaped
def utcnow() -> dt.datetime                                    # the clock seam: one-line wrapper over dt.datetime.now(UTC), module-level
                                                              # so tests monkeypatch `aiobserve.view.format.utcnow` and freshness is testable
def ago(value: dt.datetime | None, now: dt.datetime) -> str   # "3d ago", "2h ago", "just now"; the registered filter is a closure that
                                                              # calls format.utcnow() at render, never captured at build_app — a
                                                              # long-lived server must not drift
def share(part: float | None, whole: float | None) -> str      # "2.2%" (one decimal); ABSENT when part is None or whole is None/0 —
                                                              # 163 sessions have zero cost, and a zero-whole share is a gap, not 0%
# Formatting rule: every integer count a template prints goes through `| count`,
# every cost through `| money`, every rate through `| share`. No bare `{{ row.<int> }}` survives.
```

```jinja
{# _parts.html — the two-line cell #}
{% macro stacked(field, primary, secondary_field, secondary) %}
<span data-field="{{ field }}" class="primary">{{ primary }}</span>
<span data-field="{{ secondary_field }}" class="secondary">{{ secondary }}</span>
{% endmacro %}
```

**Session list columns** (13 → was 14; sortable keys in `SORTS` shrink to match):

| Column | Primary | Secondary | Sort key |
|---|---|---|---|
| Started | `ago` | full `when`, dim | `started_at` |
| Session | title, 2-line clamp | enrichment description, 2-line clamp, + tags | `title` |
| Project | path, dim | — | `project_dir` |
| Turns / Calls / Tools / Compactions | `count` | — | as today |
| Errors | `tool_errors \| share(tool_calls)` | "12 errors" | `tool_errors` (count, not rate — 1/1 = 100% would rank junk first) |
| Cost | `money` + unpriced `*` | output tokens `count` | `cost_usd` |
| Wall | `duration` | "active 4m 12s" | `wall_ms` |
| Subagents | `designer ×4, implementer ×2` — counted list, cut like skills | — | `agent_runs` |
| Skills | as today | — | — |
| Work | `implement ×21, debug ×4` — top turn categories, cut at 3; only when store is enriched | — | — |

`view_sessions.sql` replaces its discarded `agent_types` list with the counted variant (GROUP BY agent_type, ordered count desc; max 22 types/session in store); `SHOWN` keeps it and cuts it like `skills`. `Work` rides `view_described_sessions.sql` (the existing `described` join seam), sourced from `turn_enrichments`. Two-line clamp is `display:-webkit-box; -webkit-line-clamp:2` — no JS.

**Projects page** (`view_project_rollups.sql`, params `$now`, `$projects`): per project — sessions and spend over 7d, 30d, all-time (two-line cells: count over spend), last-active. Windows computed from a route-bound `$now`, not SQL `now()`, so the citation footer reproduces the exact window. **Worktree folding:** each `project_dir` folds onto its shortest stored prefix-ancestor (the `sessions.project_predicate` shape: equal, or `starts_with(dir, ancestor || '/')`). Verified need: the store holds 4 distinct non-NULL dirs and 3 of them are `…/mycelia/.claude/worktrees/*`, which would otherwise masquerade as projects. NULL `project_dir` (4 sessions) renders one unlinked "(no project)" row. Consequence: `FILTERS["project"]` becomes the same prefix predicate, so the click-through covers the folded set — this unifies the viewer filter with the CLI's `--project` (`docs/viewer.md:42` currently documents the difference; doc-sync removes it).

**Nav tree** (`NavNode(kind, href, label, cost_usd, share, in_window, children)`):

- Node grain: main-thread **turns** (label = enrichment description head, falling back to `/command args` head, then prompt head — `$nav_chars = 48`) and, nested under each, its **agent runs** (label = agent_type + task-description head). Never api calls or a run's inner turns — the ask is *more* high-level than pi
- Expansion rule: turns and their direct runs are always visible; a run's own children sit in a `<details>` closed by default ("expanded at subagent level only"). A session with no runs is a plain turn list — "expand turns" is then trivially satisfied
- **Unplaceable runs appear once, in an "unattached" tail group** mirroring the page's `#unattached` section: runs that resolve to no turn (orphans, and runs spawned by calls under no turn — `threads.unattached()` already collects both) render after the last turn node as ordinary run nodes under a dim group heading, counted against the NAV bound like every other node. Every run therefore appears in the nav exactly once: under its spawn turn, or in the tail
- Every node states cost and `share` of the session's `cost_usd` ("$41.20 · 12%")
- Navigation is anchors, zero first-party JS: in-window turns → `#turn-<id>`; out-of-window turns → the permalink URL the timeline already mints (`?after=<index-1>#turn-<id>`); runs → their run page. `:target` highlights the landing row (the records browser already does this)
- Emphasis is server-rendered and **coarser than pi's — an accepted trade, not parity**: every in-window turn node (20 at defaults) gets `data-here`, out-of-window nodes render dimmed, and after a click `:target` highlights the *content* row only. The nav has no single-active-node tier, no auto-scroll to the current node, and `<details>` expansion state resets on every cross-window navigation. Accepted because closed-by-default runs and ≤ 200 nodes keep the map scannable without JS
- **Signature element — the spend meter:** each node carries a thin left-edge bar whose length encodes cost share. CSP trap: `default-src 'self'` blocks inline `style` attributes, so the width is a decile class (`s0`–`s10`), not `style="--share:…"` — and any nonzero share rounds up to `s1`, or whale-distributed cost blanks the meter. **As built,** the ten classes survive but the scale is logarithmic over three orders of magnitude, from a thousandth of the session to all of it: a linear decile drew 525 of the store's 977 main-thread turn nodes with the same shortest bar

**Bound — the nav is its own response.** The session page's worst case is already ~483,000 B against the 500,000 B ceiling (`test_bounds.py` constants: 15,000 chrome + 20 marks + the turns=19/chips=10 argmax), leaving ~17 KB of headroom — no inline nav fits, and since labels are only ~240 B of a ~550 B row, shrinking `$nav_chars` cannot close a markup-dominated gap. So the nav loads as its own fragment (the ceiling is per-response) with its own bound: `NAV = Bound(default=200, ceiling=200)` flattened nodes, worst ≈ 200 × 550 B + wrapper ≈ 115 KB, comfortably bounded, and the session page stays at today's 483 KB. The fragment takes `?nodes=` clamped by `checked()` against the ceiling — the CALLS/TOOLS precedent: default equals ceiling, so the param only goes down. It exists for the same reason theirs do: the cap tail is unreachable from recorded fixtures otherwise (the corpus's largest forest is 6 nodes against the 200 ceiling). Overflow renders a "+N more turns" tail (paging still reaches them). Implementation prices the real row by rendering the shipped macro (the timeline-paging method). Store maximum today: 285 nodes (45 turns + 240 runs, session `1de7cf38…`) and exactly one session exceeds 200, so the tail bites once on this store. Cost of the fragment: the map arrives one request after the page and needs htmx — the same dependency every turn's api calls already have; a no-JS reader keeps the timeline and loses the map. **As built,** a node prices at 840 B rather than the ~550 B sketched here, so the map's worst case is 169,500 B; the session page's own worst case rose to 489,000 B when the turn heading grew, and both hold under the ceiling.

**Layout:** `#session { display:grid; grid-template-columns: minmax(220px,280px) minmax(0,1fr); }`, sidebar `position:sticky` with its own scroll, content column capped near 52rem (pi's 800px reading-measure lesson). Below 900px the nav becomes a closed `<details>` above the content — no hamburger, no JS. **As built,** it folds above the content but stays *open*: a stylesheet cannot close a `<details>` at one width and open it at another, so the narrow map scrolls inside half a viewport and the handle closes it. The session header `<dl>` regroups its 18 facts into four clusters (identity / time / volume / spend) and adopts the formatting rule.

**Visual direction:** stay the quiet paper instrument — light+dark, system fonts, no framework, boldness spent on the spend meter alone. `style.css` grows a real token block: the five existing colors plus `--bad` (error wash), a 4-step type scale, a 4-step space scale; numbers keep `tabular-nums`; nav rows set in `ui-monospace` at 0.75rem so gutters align. Expected total stylesheet ~250 lines, still one file, no build.

## File-tree diff

```
src/aiobserve/view/app.py            ~  / → projects_page; session list → /sessions; + GET /fragment/nav/{session_id}; ago/share filters
src/aiobserve/view/bounds.py         ~  + PROJECTS, NAV; SESSIONS re-derived (fatter row → expect default ≈ 100)
src/aiobserve/view/format.py         ~  + ago, share
src/aiobserve/view/listing.py        ~  SORTS shrink; project filter → prefix predicate; list_url → /sessions
src/aiobserve/view/threads.py        ~  + nav_tree()
src/aiobserve/view/templates/projects.html      +
src/aiobserve/view/templates/sessions.html      ~  recomposed columns
src/aiobserve/view/templates/session.html       ~  grid, nav, grouped header
src/aiobserve/view/templates/_parts.html        ~  stacked(), nav macros, command-aware heading
src/aiobserve/view/templates/fragments/nav.html +
src/aiobserve/view/static/style.css             ~  tokens, grid, nav, stacked cells, meter classes
src/aiobserve/analyze/queries/view_project_rollups.sql  +
src/aiobserve/analyze/queries/view_projects.sql ~  datalist suggestions fold onto stored prefix-ancestors, matching the landing rows
src/aiobserve/analyze/queries/view_session_nav.sql      +
src/aiobserve/analyze/queries/view_sessions.sql         ~  counted agent_types
src/aiobserve/analyze/queries/view_described_sessions.sql ~  + work categories
src/aiobserve/analyze/queries/session_digest.sql, run_digest.sql ~  + command_args head
src/aiobserve/analyze/queries.py     ~  new params (NAV_CHARS, …)
docs/viewer.md                       ~  doc-sync: routes, columns, nav, filter semantics
tests/view/                          ~  see seam; no new fixture — spine/model_only/teammate already record six slash-command turns, two with empty-string args (both heading arms reachable)
```

## Chosen test seam

Unchanged and load-bearing: `TestClient` over `corpus_db`, assertions read `data-field`/`data-*` attributes, expectations derived from the served store, bounds by arithmetic in `test_bounds.py`. New surfaces join it: `data-project` rows on the landing page (counts/spend derived by re-running the fold in the test's own SQL), `data-nav`/`data-here` nodes on the nav fragment, `data-field="command_name"` on the heading plus an assertion that no rendered heading contains `<command-`. Nav labels are a new untrusted-text surface (enrichment text, command args, prompt heads), so the planted-markup sentinel (`test_planted_markup_arrives_inert`) extends to nav nodes. The formatting rule is pinned by `Planter`-planted values over 999 asserted comma-formatted. Bounds tests gain the NAV-fragment and PROJECTS arithmetic, price the command heading as max-of-arms, add `command_args` and `prompt` to the fat-column scan, and re-derive SESSIONS.

## Slices

1. **Session-list recompose** — `stacked()` macro, `ago`/`share`, formatting rule, counted Subagents + Work columns, SORTS shrink, SESSIONS re-arithmetic. Proves the two-line pattern, the format seam, and the bounds workflow end to end. Green: updated `test_app.py` list tests + `test_bounds.py`
2. **Command-cruft fix** — digest `command_args`, heading macro (bounds priced max-of-arms), `command_args`+`prompt` into the fat scan, count-formatting on session/run/turn surfaces; drives the recorded slash-command turns in spine/model_only/teammate. Green: new heading test (no `<command-` in any rendered heading; empty-args turn renders badge alone) + `test_bounds.py`
3. **Projects landing** — new SQL + route, `/sessions` move, prefix project filter, `$now` citation. Green: new `test_projects.py` deriving fold/window expectations from the corpus
4. **Session page recompose** — grid, nav fragment route + `view_session_nav.sql` + `nav_tree`, NAV bound, emphasis, spend meter, header regroup. Green: nav-fragment tests (anchors in/out of window, closed nested runs, cap tail via `?nodes=`, unattached tail group, share strings, inert sentinel) + bounds
5. **Polish + doc-sync** — token pass, dark-mode check, `docs/viewer.md` rewrite; `mise run check`

## Decisions

- **Hand-rolled CSS, extended tokens** — rejected vendored classless framework (Pico et al.): tens of KB against an arithmetic budget, restyles elements it doesn't own, and gives no components without JS
- **Zero-JS server-rendered nav** — rejected a pi-style first-party JS tree: pi needs class-only updates for 32k nodes; ours is capped at 200, and anchors + `:target` + server-marked window emphasis deliver a coarser but sufficient version inside the CSP and the existing test seam
- **Nav served as a bounded htmx fragment** — the inline page has ~17 KB of headroom under a ~483 KB worst case; rejected raising the ceiling a third time (500 KB is a discipline, not a quota, and the nav is the only tenant that needs the room) and rejected buying room from `CHIP_BUDGET` (200 → ~130 frees ~126 KB but caps a single-turn chip page at 65 runs, below the 94-run forest the `CHIPS` ceiling was sized to reach)
- **Nav grain = turns + runs, deeper runs closed** — rejected message-grain (pi's): unbounded and duplicates the content pane
- **Spend meter via decile classes** — inline `style` attributes are blocked by `default-src 'self'`
- **Projects from `corpus_rollups`** — `session_rollups` double-counts resumed spend across sessions
- **Fold worktrees by shortest stored prefix-ancestor; project filter becomes the prefix predicate** — rejected exact grouping (worktrees masquerade as projects) and path-shape heuristics (`.claude/worktrees` is one client's convention)
- **`/` becomes projects; list moves to `/sessions`** — rejected `/projects` beside the list: the ask is landing-first; old list URLs break, accepted (early project, session/record citation URLs — the ones reports quote — survive)
- **Command cruft fixed at render time** — rejected extract-time stripping: rewrites the "prompt as recorded" contract (`model.py:62`, `docs/schema.md`) and forces re-extracting a 14 GB store to fix existing rows
- **Errors sorts by count, displays rate** — a 1-tool session at 100% would top a rate sort
- **`$now` bound by the route** — SQL `now()` makes the citation footer non-reproducible
- **Output tokens and active time demoted to secondary lines** (their sort keys dropped) — fewer columns beats sortability of texture metrics

## Out of scope

- Run-page sidebar — run pages get the shell and styles only; nav there is a later slice
- `<teammate-message>` tag styling — the opening tag identifies the sender by design (`docs/schema.md:81`)
- Nav search/filtering, sidebar resizer, expansion-state persistence, hamburger — all need JS
- Markdown in nav labels; per-api-call nav nodes; any extract or stored-schema change; analysis/report surfaces

## Open questions

None — both resolved by Nathaniel at the design gate (2026-08-18):

1. Nav grain confirmed as designed: turns and their direct runs visible, nested runs closed
2. Work column counts categories by turn (`implement ×21`), matching the Subagents cell
