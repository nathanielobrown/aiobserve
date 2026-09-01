# Design: organize `src/hyphae/view/` by page, then by kind

Move the viewer's modules into a tree an agent can navigate without reading: one package per page, the same file names inside each, and a shared layer above them. No route, URL, query, or rendered byte changes. The implementer works on branch `view-layout`, commits this plan first, and keeps `improvements.md` beside it (below).

Every path and line count here was read on 2026-09-01. Treat each as a hypothesis to check at the file before acting on it.

## Problem

`view/` is thirty modules on one layer plus `components/`. The only split is by kind, one level deep: markup under `components/`, everything else — routes, presenters, models, printing utilities, the server — mixed at the top. Two symptoms:

- A change to one page touches four places: its route module, its presenters, `components/<page>.py`, and whatever shared module it reaches. The CLAUDE.md rule "load only the context the task needs" is unenforceable when the context for one page is scattered
- The route modules are split by response kind, not by page: `fragments.py` (21 routes) and `expansions.py` (4 routes) all serve the node page. "Fragments" names nothing a reader looks for

The constraint that decides the shape: the node page is about half the package (~5,100 of ~10,400 lines), the two lists ~8%, the four small pages ~8%, and the rest is shared. Organizing by kind first would put a `node/` inside every kind directory; organizing by page first puts the weight where it is and leaves the node package to order itself by its own anatomy (`CONTEXT.md`, "Node-page anatomy").

## Call paths, current → proposed

A request runs the same path before and after; only module names change.

```
current:  app.py ─extends─ listing.router / node_pages.router / pages.router / expansions.router / fragments.router
          route ─calls─ browse.py, nav_tree.py, … ─calls─ components/*.py ─returns─ Html

proposed: app.py ─extends─ pages.<page>.routes.router, one per page package
          pages/<page>/routes.py ─calls─ pages/<page>/<presenter>.py ─calls─ pages/<page>/markup.py ─returns─ Html
          every layer above ─calls─ the shared layer: nodes, enrichment, citation, failures, store, text/, components/
```

## File-tree diff

```
src/hyphae/view/
  app.py  deps.py  dev.py  bounds.py            unchanged: the server
  store.py  manifest.py                         unchanged: store access
  nodes.py  enrichment.py  citation.py          unchanged: shared view-models
  failures.py                                   ← errors.py (Failure, Failures, failures, Step, stepped)
  text/                                         how one value prints
    format.py  cuts.py  labels.py  tool_names.py  render.py  highlight.py  inline_markdown.py
  components/                                   shared markup only
    layout.py  parts.py  citation.py  error.py  ← error.py holds components/pages.py:error_page
  pages/
    projects/  routes.py  markup.py             ← listing.py:projects_page + components/listing.py's project half
    sessions/  routes.py  markup.py             ← listing.py:session_list, narrowing, list_url + the session half
    node/
      routes/  __init__.py (assembles `router`)
               pages.py       ← node_pages.py (the 8 cold pages)
               expansions.py  ← expansions.py (thread_body, run_body, node_kin, loose_kin)
               popovers.py    ← fragments.py: *_numbers
               enrichment.py  ← fragments.py: *_description, *_friction
               details.py     ← fragments.py: call_text … run_result
      browse.py  builders.py  nav_tree.py  numbers.py  walk.py  detail.py  knobs.py  columns.py
      markup/  page.py  body.py  nav_tree.py  logs.py  numbers.py  values.py
               ← components/{node_page,node_body,nav_tree,logs,numbers,values}.py
    errors/    routes.py  markup.py             ← pages.py:errors_page + components/pages.py:errors_page, _failure
    query/     routes.py  markup.py             ← pages.py:query_page + components/pages.py:query_page, _bindings, _setup
    records/   routes.py  markup.py             ← pages.py:records_page + RecordRow, records_page, _record
    offload/   routes.py  markup.py             ← pages.py:offload_page + OffloadFile, offload_page
  static/                                       unchanged

deleted: listing.py  node_pages.py  pages.py  expansions.py  fragments.py  errors.py
         components/{listing,pages,node_page,node_body,nav_tree,logs,numbers,values}.py
```

Three lifts out of page packages into the shared layer, because a sibling page reads them:

- `browse.header_bound` → `store.py`, beside `Page.SESSION_HEADER` it binds (readers: node routes, `pages.errors_page`)
- `knobs.checked` → `deps.py`; it raises `HTTPException`, so it is a route concern, not a presenter's (readers: every route that takes a size)
- `columns.{CALL_ICON,RUN_ICON,TOOL_ICON}` → `nodes.GLYPHS`; then `columns.py` is node-page-only and imports `nodes`, not the reverse. Check what else `nodes.py:23` takes from `columns` (`Shape`, `COLUMNS`) and move or invert those the same way

## Key contracts

The layout is the contract. Four rules, each read off the source by `tests/view/test_layout.py` (new) the way `tests/view/test_components.py` reads the components package's rules today:

1. **Kinds by file name.** In a page package, `routes.py` or `routes/` is the only module importing `fastapi` or `starlette`; `markup.py` or `markup/` is the only module importing `htpy`; every other module is a presenter named for what it builds (`nav_tree.py`, `walk.py`), never `logic.py` or `utils.py`. `models.py` appears only when a model is read by more than one markup module of the page — none today
2. **Pages are leaves.** A page package imports the shared layer and itself, never a sibling page
3. **Downward only.** `pages/` → `components/`, `nodes`, `enrichment`, `citation`, `failures` → `store`, `bounds` → `text/`. `text/` imports nothing in `view/` outside itself and `bounds`
4. **The `components/` rules move to every markup module.** The three rules in `components/__init__.py` (no web framework, keyword-only parameters, no `Markup` construction) now cover `pages/**/markup.py` and `pages/**/markup/**`; `test_components.py`'s `MODULES` glob widens to say so

Path pins outside the package that must follow the move (`mise run check-fast` reports the ones it can see):

- `pyproject.toml` `[[tool.pyrefly.sub-config]] matches = "src/hyphae/view/components/**"` — must also match the page markup modules; `test_components.py:43` `NARROWED` mirrors it
- `.claude/rules/viewer-ui.md` frontmatter `paths:` and the body's `src/hyphae/view/...` references
- `docs/viewer.md`, `viewer-titles.md`, `viewer-bounds.md`, `ui-development.md`, `.claude/rules/testing.md`, `CONTEXT.md` (the "Component" entry and "the routes: the modules `app.py` mounts")
- `hyphae.analyze.macros` imports `view.tool_names`; `hyphae.cli` and `hyphae.analyze.manifest` import `view.manifest`
- `tools/gen_layout.py:71` walks `hyphae.view`; each new package needs a docstring for the Layout tree, then `mise run cogs`

## Chosen test seam

The existing suite, unchanged in what it asserts: it drives the app through URLs and reads bytes, so a move that changes behaviour fails it. Plus the new `test_layout.py`, which asserts the four rules above over the source tree and is the one red test this refactor starts from.

Tests move with the modules where a test is about one module (`test_format.py` → `tests/view/text/test_format.py`; `test_nav_tree__*.py`, `test_numbers__*.py`, `test_walk.py` → `tests/view/pages/node/`; `test_projects.py`, `test_query.py`, `test_records.py`, `test_offload.py`, `test_errors.py` → their page's directory). Tests that drive the whole app (`test_app*.py`, `test_bounds*.py`, `test_scenarios.py`, `test_lifecycle.py`, `test_components.py`) stay flat. Shared readers (`budgets.py`, `nav_trees.py`, `scenarios.py`, `selections.py`) stay flat; the gallery imports `scenarios.py` by path. `tests/view/conftest.py` already covers the subdirectories.

## Slices

Each slice is one or more commits and ends green on `mise run check`. Move commits change no line inside a function; a cleanup taken during a slice is its own commit after the move, so a reviewer can read each as one thing. Run the `commit` skill for messages.

1. **Branch and pin the shape.** Commit this plan and `improvements.md`. Write `tests/view/test_layout.py` asserting the four rules against the proposed tree; it is red until slice 6
2. **`text/`.** Move the seven modules; rewrite imports with `fastmod`; update the pins listed above. Verify: `mise run check`, and `test_layout`'s `text/` rule goes green
3. **The shared lifts.** `errors.py` → `failures.py`; `header_bound` → `store.py`; `checked` → `deps.py`; the icons → `nodes.GLYPHS`. Verify: `mise run check`
4. **The list pages.** Split `listing.py` and `components/listing.py` into `pages/projects/` and `pages/sessions/`. Widen the pyrefly sub-config and `test_components.py` globs here — this is the slice that proves the markup rules survive the move. Verify: `mise run check`, `mise run e2e`
5. **The four small pages.** `pages.py` and `components/pages.py` into `errors/`, `query/`, `records/`, `offload/`; `error_page` to `components/error.py`. Verify: `mise run check`
6. **The node page.** Routes into `routes/` by anatomy, presenters flat, markup into `markup/`. `app.py` now extends seven routers. Verify: `mise run check`, `mise run e2e`; `test_layout` is fully green
7. **Tests follow.** Move the per-module tests as listed under the seam. Verify: `mise run check`
8. **Docs.** Dispatch `doc-writer` for doc-sync over the branch; `mise run cogs`; update `CONTEXT.md` and `viewer-ui.md` by hand where a term changed. Verify: `mise run check`, then open the PR with the `pr` skill

Cleanup policy for every slice: a fix that is local to the files being moved and under about fifteen minutes — a name that now reads wrong, a constant defined twice, a dead import, a helper that became private — is done in its own commit right after the move. Anything larger, or anything touching a file the slice isn't moving, goes into `improvements.md` with `file:line`, what is wrong, and the fix you would make. Never silently skip a smell you noticed.

## Decisions

- **Page first, kind inside** — rejected kind first (`routes/`, `presenters/`, `markup/`): it keeps a change to one page across four directories, and the node page would need a subpackage inside each kind anyway
- **Page boundaries from `CONTEXT.md`'s "Viewer pages"**, not from route count — rejected one package per route module: `listing.py` serves two pages and the node page is one page across three route modules
- **`markup.py`** for the htpy module — rejected `view.py` (`view` already names the package, and "view-model" is used throughout) and `components.py` (the glossary's *component* is the function; the module holding them is the page's markup, as `components/__init__.py`'s own lead says)
- **Node routes split by anatomy** (`expansions`, `popovers`, `enrichment`, `details`) — rejected one 1,200-line `routes.py`, and rejected keeping `fragments.py`: an expansion, a popover, and a detail are glossary terms a reader looks for; a fragment is not
- **`routes.py` + `markup.py` in every page, including 50-line ones** — rejected letting a small page be one module: consistency is what lets an agent find the file without listing the directory, and rule 1 needs the file name
- **View-models stay in the markup module that consumes them**, as today — rejected a `models.py` per page: it would split every `NamedTuple` from the one function that reads it. Revisit if a model gains a second reader
- **`failures.py` in the shared layer**, not in `pages/errors/` — the node page's stepper reads the same list, and a sibling import would break rule 2. A session's failures are a session fact both pages read, like its nodes
- **`text/` may import `bounds`** — `highlight.py` and `inline_markdown.py` read sizes from it; sizes are a leaf too. Rejected moving `bounds` into `text/`: it composes the query manifest and belongs beside `store`
- **Tests mirror only where a test is about one module** — rejected mirroring everything: most of the suite drives the app and is named by behaviour, which `.claude/rules/testing.md` prefers

## Out of scope

- Any change to a URL, a route's response, a `view_*.sql` query, or `static/`. The browser tier must pass unchanged
- The larger refactors the 2026-08-30 audit left open (`plans/refactor-audit-2026-08-30/findings.md` S2, S3, S12, S13, S14) — check whether each still stands and record the answer in `improvements.md`; do one only if a move makes it trivial
- Renaming things inside functions. Move first; a rename that a move makes obvious is a cleanup commit, and a rename that isn't waits for `improvements.md`

## Open questions

- **pyrefly sub-config globs.** Whether one `matches` can name both `components/**` and `pages/**/markup*`, or two sub-configs are needed. Settle by trying it in slice 4 and reading `mise run check`
- **`knobs.py`'s markup imports.** It takes `PresetChoice`, `Pager`, `Step` from markup modules — a presenter reading types the markup owns. Under the rule that view-models live in markup, this is allowed; if it reads as backwards once `pages/node/` exists, note it in `improvements.md` with the alternative (a `models.py` in `node/`)
- **`components/listing.py`'s `Described`** beside `enrichment.py`'s own model of what a pass wrote — possibly one thing twice. Check while splitting in slice 4
