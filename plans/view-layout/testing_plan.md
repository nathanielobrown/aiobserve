# Testing plan: organize `src/hyphae/view/` by page

Obligations for the design at `plans/view-layout/design.md`. Each leaf names the slice it lands in and the artifact that proves it. This is a pure-move refactor, so the obligations split in two: the *layout rules*, which nothing checks today and which `tests/view/test_layout.py` must newly assert, and *behaviour preservation*, which the existing URL-driven suite already discharges — those leaves are marked **(standing)** and are discharged by keeping a named test green, not by writing one.

The world every tier runs against is the fixture corpus: `tests/conftest.py:build_enriched_store` builds it from redacted recorded sessions under `tests/fixtures/`. Nothing here invents a record or a page.

Every path and line number below was read in the worktree on 2026-09-01. Treat each as a hypothesis to check at the file.

## source-tree rules (new `tests/view/test_layout.py`, slices 1–6)

Reads the checkout, not a running app: `ast` over the source and one fresh-interpreter import probe, the way `tests/view/test_components.py` already reads the components package's rules. Red from slice 1, fully green at slice 6; a slice turns its own rule green. Every scan leaf carries a companion assertion — a rule that finds nothing because the tree moved out from under it is the failure mode this whole file has.

### Rule 1 — kinds by file name (slices 4–6)

- **In every page package, only `routes.py` or `routes/**` imports `fastapi` or `starlette`.** This is the rule that keeps a presenter callable without a request and pyrefly owning it. *Evidence:* a fresh-interpreter probe per page package in the shape of `test_components.py:PROBE` — import every non-`routes` module of the package, print the intersection of `sys.modules` with `{"fastapi", "starlette"}`, assert empty; the negative control is the same probe over the package's own `routes` module, which must name both. An in-process assertion is worthless here: this suite's conftest builds `TestClient`s, so `fastapi` is in `sys.modules` before any test runs.
- Only `markup.py` or `markup/**` imports `htpy`. *Evidence:* an AST scan of every module under `pages/` for an `import htpy` / `from htpy import`, asserting the set of files that name it equals the set matching the markup glob; the companion is that the set is non-empty, so a renamed directory reds instead of passing.
- No module under `pages/` is named `logic.py`, `utils.py`, `helpers.py`, `common.py` or `misc.py`. *Evidence:* a denylist assertion over `pages.rglob("*.py")`, failing with the offending path. That a presenter is *named for what it builds* is a review judgement and is deliberately not machine-checked — see "not covered".
- Each page package holds exactly one routes kind and one markup kind: `routes.py` xor `routes/`, `markup.py` xor `markup/`, and at least one of each. *Evidence:* a leaf parametrized over the page packages discovered by `iterdir`, asserting the two exclusive-or conditions; discovery rather than a written list, so a page added next year is covered.
- A `models.py` exists in a page package only where more than one of that page's markup modules imports a name from it. *Evidence:* an AST scan resolving intra-package imports of each `models.py`; today the assertion passes on the empty set, and the companion is the rule's other half — with no `models.py` anywhere, the leaf asserts that too, so it reds the day one appears without a second reader.

### Rule 2 — pages are leaves (slices 4–6)

- **No page package imports a sibling page.** The rule that makes "the context for one page is one directory" true; `failures.py` and `header_bound` were lifted to the shared layer precisely to keep it. *Evidence:* an AST scan over every module under `pages/`, collecting absolute and relative imports resolved to a dotted path, asserting no import names `hyphae.view.pages.<other>`; the failure prints importer and imported. Companion: the same scan asserts each page *does* import `hyphae.view.pages.<itself>` or the shared layer, so a scan that resolved nothing reds.

### Rule 3 — downward only (slices 2–6)

- The import direction holds layer by layer: `pages/` may import `components`, `nodes`, `enrichment`, `citation`, `failures`, `store`, `bounds`, `text/`; those may import `store`, `bounds`, `text/`; `text/` imports nothing inside `view/` except itself and `bounds`. *Evidence:* one AST scan building the `view/`-internal import graph, checked against a layer number per module; the assertion prints the offending edge as `importer → imported`. Companion: the graph is asserted non-empty and to contain a known-good edge (a node presenter importing `nodes`), so an empty graph cannot pass.
- `text/` is a leaf. *Evidence:* the same graph, restricted: every `view/` import out of `text/**` resolves inside `text/` or to `bounds`. This is the one rule that can go green at slice 2, and the design's verification step for that slice depends on it.
- `nodes.py` does not import a page package. The design inverts `columns` into `nodes.GLYPHS` for this; the inversion is only real if the edge is gone. *Evidence:* the graph assertion above, plus a leaf naming `hyphae.view.nodes` explicitly and asserting no `pages.` import — the shared layer is where a cycle would be reintroduced silently.

### Rule 4 — the components rules cover page markup (slice 4)

Discharged by widening `tests/view/test_components.py` rather than by new leaves; `test_layout.py` asserts only that the widening is real.

- `test_components.py`'s `MODULES` and `SOURCES` cover every page markup module as well as `components/**`. *Evidence:* a leaf in `test_layout.py` comparing the module list `test_components.py` scans against the markup modules `test_layout.py` discovers on the tree; equality, so a page whose markup escapes the components rules reds in the file that owns the rules' scope.
- **Every page markup module clears the three components rules unchanged: no web framework, keyword-only annotated parameters returning `Html`, no `Markup(` construction.** *Evidence:* the existing `test_every_component_clears_the_signature_floor`, `test_no_attribute_a_component_writes_is_handed_a_markup_producer` and `test_no_component_constructs_markup` green over the widened glob; slice 4 is where this first bites, on `pages/projects/markup.py` and `pages/sessions/markup.py`.
- `test_no_component_constructs_markup`'s companion still finds the four producer modules. It reads `VIEW.glob("*.py")` — top level only (`test_components.py:255`) — and `render.py`, `highlight.py` and `inline_markdown.py` move into `text/` in slice 2, so the companion breaks there and must become a recursive walk. *Evidence:* the leaf green at slice 2 with `PRODUCER_MODULES` unchanged and the walk widened; a deliberate local check that deleting `text/render.py`'s `Markup(` reds it.
- The pyrefly narrowing still names every path that needs it and no more. `test_components.py:test_the_only_check_this_package_is_excused_is_the_one_htpy_forces` asserts `[matched for matched in scoped if matched.startswith("src/")] == [NARROWED]` — a single-element list. Whichever way the design's open question settles (one widened glob or a second sub-config), that assertion changes. *Evidence:* the leaf rewritten to compare the src-scoped narrowings against a named tuple of globs, with the same "no other kind, no per-line escape" assertions intact, and the canary leaf untouched.

## behaviour preservation (existing python tier, `tests/view/` + `tests/gallery/`, every slice)

The fixture store on disk, the app built over it through `build_app`, nothing mocked. These obligations are already discharged; the plan's job is to name what covers what, so a slice that goes green is known to have proved something.

- **(standing) Every route the app declares is still exactly the set of scenarios, after each slice.** A route lost in a move — a router not extended in `app.py`, a decorator dropped — is the single most likely failure of this refactor, and this is the leaf that sees it. *Evidence:* `tests/view/test_bounds.py:547-551` (`exposed == set(SCENARIOS)`), `tests/view/test_dev.py:214-215` (both the prod and dev apps), and `tests/gallery/test_serve.py:146`. Slice 6 must not relax any of the three when `app.py` starts extending seven routers.
- (standing) Every node of every fixture session still answers 200 at its URL. *Evidence:* the `conftest.pages()` sweep — one URL per node read from the store the way the routes read it — driving the whole corpus.
- (standing) Every scenario URL still renders its page's content, not merely a status. *Evidence:* the scenario-parametrized leaves in `test_app.py:357`, `test_bounds.py:554`, `test_dev.py:184`, `test_enrichment.py:39` and `test_query.py:38`.
- (standing) The knob, cut and paging bounds still hold after `knobs.checked` moves to `deps.py` and `knobs.py` moves into `pages/node/`. *Evidence:* `test_bounds.py`, `test_bounds__lists.py`, `test_bounds__node.py`, `test_bounds__values.py` green in slices 3 and 6; plus `tests/tools/test_gen_bounds.py`, which imports `hyphae.view.knobs` directly.
- (standing) The htmx pane swap still lands the pane in the pane over every link. *Evidence:* `test_nav_tree__rows.py:test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane`, which resolves attribute inheritance rather than grepping.
- (standing) The failure list and the error stepper read the same failures after `errors.py` → `failures.py`. *Evidence:* `tests/view/test_errors.py` green in slice 3, before the errors page moves in slice 5.
- **A move changes no rendered byte.** No leaf asserts this and none can be added cheaply — see "design findings". *Evidence:* a throwaway capture, not committed: at slice 1, write every `SCENARIOS` URL's response body to a temp directory through a `TestClient` over the fixture store; after each slice, re-capture and `diff -r`. The implementer records the diff being empty in each slice's commit message or the PR body. Where a diff is non-empty and intended (none is expected), it is named.

## path pins (existing tooling tier, `tests/tools/`, and `mise run check`)

Pins outside `src/hyphae/view/` that a move breaks. Each is already guarded; the obligation is that the guard is run and read, not that a test is written.

- Every module that imports a moved module still imports it. *Evidence:* `mise run check` — an unresolvable import is a pyrefly error and a collection error, both in `check`. The importers outside the package are `src/hyphae/cli.py:45` and `src/hyphae/analyze/manifest.py:32` (`app`, `manifest`, both unmoved), `hyphae.analyze.macros` (`tool_names`, moves in slice 2), **`tools/gen_bounds.py:20-21` (`bounds`, `nodes`, `knobs` — `knobs` moves into `pages/node/` in slice 6, and the design's pin list omits this file)**, `tools/gen_routes.py:22-23`, and the test modules named above.
- The route table in `docs/viewer.md` and `docs/documentation.md` is unchanged by the move. `tools/gen_routes.py` builds it from the app's own routes and each handler's first docstring sentence, so a handler that moves without its docstring silently rewrites the table. *Evidence:* `mise run cogs-check`, inside `check`; the design's pin list omits `gen_routes.py`, and this is what covers it.
- The knob and bounds tables in `docs/viewer-bounds.md` are unchanged. *Evidence:* the same `cogs-check`, over the three `tools.gen_bounds` blocks, which depend on `hyphae.view.knobs` and `hyphae.view.bounds`.
- The `CLAUDE.md` Layout tree still matches the tree. It lists `src/hyphae/view/` alone and lifts its gloss from `hyphae.view.__doc__`, so it moves only if that docstring or the curated entry list in `tools/gen_layout.py:ENTRIES` changes. *Evidence:* `cogs-check` plus `tests/tools/test_gen_layout.py`; a package added under `view/` forces nothing here (see "design findings").
- `tests/e2e/routes.json` still matches `SCENARIOS`. *Evidence:* `tests/tools/test_gen_e2e_routes.py:21`, which compares the checked-in bytes against a fresh generation.
- `.claude/rules/viewer-ui.md` frontmatter `paths:` still matches the files the rule governs. *Evidence:* `mise run lint-docs-check` for the links and paths it can resolve, plus a read of the frontmatter glob against the new tree in slice 8 — a `paths:` glob matching nothing fails no gate today, so this one is checked by eye.

## browser tier (`tests/e2e`, slices 4 and 6)

Playwright over the gallery in a real Chromium, outside `check` because it needs a browser. Nothing here is re-proved from the python tier; it covers what a `TestClient` structurally cannot see.

- (4, 6) Every full page in `routes.json` still loads with zero console errors, zero page errors and zero CSP violations. *Evidence:* `mise run e2e`, the existing `pages.spec.ts` sweep, at the two slices the design names.
- (6) The htmx swaps still work end to end after the node routes split into five modules. *Evidence:* `mise run e2e`, `htmx.spec.ts`, in the slice where the routes serving every fragment move.

## not covered

- **That a presenter is "named for what it builds."** Rule 1's positive half is a judgement about English, not a property of the source; the denylist above is the checkable floor and `.claude/rules/viewer-ui.md` plus review own the rest.
- **Rendered bytes as a committed baseline.** A golden corpus over 38 scenarios would churn on every real page change and would be read by nobody; the throwaway capture above buys the same evidence for this branch at no ongoing cost. Chromatic holds the visual baselines and runs outside `check`.
- **That the new tree is easier to navigate.** The design's motivating claim is unmeasurable here; it is a bet, and `improvements.md` is where its costs are recorded.
- **Line-level equivalence of moved functions.** The design forbids changes inside a function during a move commit; `git log -p --follow` and review enforce that, not a test.

## design findings

Reported to the manager rather than implemented past.

- **Byte-for-byte preservation is asserted by no leaf.** The existing suite asserts many *properties* of the served HTML — statuses, `data-` attributes, route inventory, swap attributes — but nothing compares whole responses before and after. The design's "no rendered byte changes" claim is therefore stronger than its seam. The mitigation above (a throwaway pre/post capture over `SCENARIOS`) is cheap and stays inside the seam; the design should name it as a verification step in slice 1 rather than leave it implicit.
- `tools/gen_bounds.py:21` imports `hyphae.view.knobs`, which the design moves into `pages/node/`. The pin list omits it, as it omits `tools/gen_routes.py` (which drives two cog blocks off route-handler docstrings). Both fail loudly under `check`, so this is a completeness gap in the plan, not a hole in the seam.
- `tools/gen_layout.py:71` is a curated `Entry`, not a walk: the Layout tree lists `src/hyphae/view/` and lifts its gloss from the package docstring. New sub-packages need no entry and `mise run cogs` changes nothing unless `hyphae/view/__init__.py`'s docstring does. The design's claim that "each new package needs a docstring for the Layout tree" is wrong as stated; docstrings on the new packages are still worth writing, for the reader rather than for the generator.
- `test_components.py`'s producer companion reads `VIEW.glob("*.py")` — top level only — so it breaks in slice 2 when three of the four producer modules move into `text/`. Expected and easy, but the design's slice-2 verification step should say so instead of promising a clean `mise run check`.
- The design's test-move list is incomplete: `test_highlight.py`, `test_render.py` and `test_tool_names.py` are single-module tests for modules moving into `text/`, and it does not say where `test_nav_tree.py`, `test_numbers.py` or the five `test_node__*.py` modules go. Slice 7 should settle those by the same rule it states, not by a list.
