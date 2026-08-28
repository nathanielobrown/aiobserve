# Testing plan: visual testing and the browser tier

Obligations for the design at `plans/visual-testing/design.md`. Each leaf names the slice it lands in and the artifact that proves it. The world the tests run against is the fixture corpus in every tier: `tests/conftest.py:build_enriched_store` builds it from redacted recorded sessions under `tests/fixtures/`, and nothing here invents a record.

## python tier (pytest over `TestClient`, `tests/view/` and `tests/gallery/`)

The described fixture store on disk, the app built over it, no browser and no network. This tier keeps owning the 38-route sweep; nothing below re-proves a 200 in a browser.

- (1) Every scenario carries a non-empty `title` and a `Group` member. *Evidence:* a leaf parametrized over `SCENARIOS` asserting both, failing with the route template that has neither.
- (1) No two scenarios share a title. A title is Chromatic's snapshot name and the gallery's link text, so a collision silently merges two baselines. *Evidence:* `len({s.title for s in SCENARIOS.values()}) == len(SCENARIOS)`.
- (1) The scenario keys still equal the routes the app declares, after the rename. *Evidence:* the existing `test_every_route_the_viewer_exposes_is_in_the_payload_sweep` (`tests/view/test_bounds.py:470`) green reading `set(SCENARIOS)`, at 38 keys.
- (1) All six readers take `.url` and stay green: `test_bounds.py`, `test_dev.py`, `test_enrichment.py`, `test_query.py`, `tests/gallery/test_serve.py`, `tests/gallery/serve.py`. *Evidence:* `mise run test` with no `ROUTES` symbol left in the tree (`rg -w ROUTES` returns nothing outside the viewer's own code).
- (1) The gallery index renders one link per scenario, in registry order, under the six group headings in `Group` declaration order, with the title as link text and the route template beside it. *Evidence:* the rewritten `test_the_index_offers_one_link_per_scenario_and_nothing_else` (`tests/gallery/test_serve.py:46`) comparing the parsed `(heading, title, url)` triples against `SCENARIOS`.
- (1) `data-scenario` survives the index rewrite. *Evidence:* the `LINK` regex at `tests/gallery/test_serve.py:32` still matching every row; the index leaf above depends on it.
- (1) The index is still djLint-clean and still under every path the formatter walks. *Evidence:* `mise run format-html-check` plus `tests/tools/test_mise_tasks.py:test_every_template_in_the_tree_is_one_the_formatter_walks`.

### The frozen clock (slice 2)

- **(2) A gallery page renders identical relative-time text with `fmt.utcnow` moved a week between renders.** This is the whole of the slice: an unfrozen clock makes every archived page differ from yesterday's baseline and turns Chromatic into noise. *Evidence:* two renders of `/sessions` and one node page through `serve.gallery(...)`, `fmt.utcnow` monkeypatched a week apart between them; the extracted `ago` strings compare equal.
- (2) The instant the gallery freezes to is derived from the corpus, not a literal date. *Evidence:* the frozen value compared against a timestamp read out of the store the gallery itself built, so a corpus with newer sessions moves it.
- (2) Freezing adds no door onto a private store. *Evidence:* `test_the_gallery_cannot_be_pointed_at_a_store` (`tests/gallery/test_serve.py:57`) still green — `parser()` parses to `{"port": ...}` alone and the source holds no `environ` or `getenv`.
- (2) The shipped viewer's clock is untouched. The freeze is a `setattr` on a module the package owns, so an import-order slip would freeze production. *Evidence:* a leaf importing `tests.gallery.serve` and asserting `hyphae.view.format.utcnow` is still the module's own function, and that a `build_app` page's `ago` text moves when the real clock does.

### `routes.json`, the contract between the tiers (slice 3)

- **(3) The checked-in `tests/e2e/routes.json` matches what the generator writes from `SCENARIOS`.** A drifted copy is a browser tier sweeping yesterday's pages while every Python leaf stays green. *Evidence:* a leaf running the generator into `tmp_path` and comparing bytes with the tracked file, failing with the regeneration command.
- (3) Every scenario reaches the file with its title and group intact, fragments included, so the browser tier filters rather than the generator. *Evidence:* the loaded JSON compared whole against `SCENARIOS` rendered to plain dicts.
- (3) `mise run e2e` and `mise run e2e-chromatic` name directories that exist, the way the djLint tasks do. *Evidence:* `tests/tools/test_mise_tasks.py` extended to the new tasks, asserting each named path resolves in the tree.
- (5) `.github/workflows/e2e.yml` is the only workflow that names a secret, sets `fetch-depth: 0`, and guards the fork case. *Evidence:* a leaf parsing both workflow files: `secrets.` appears in `e2e.yml` and not in `check.yml` (whose header at `:1-4` promises no secret and no network), the checkout step carries `fetch-depth: 0`, and the uploading job carries `if: github.event.pull_request.head.repo.full_name == github.repository`.
- (5) `mise run e2e-chromatic` refuses to run on a missing or empty token. *Evidence:* the task invoked with `CHROMATIC_PROJECT_TOKEN=` exits non-zero with a message naming the variable, before any network call.

## browser tier (TypeScript Playwright, `tests/e2e/`, real Chromium)

Playwright's `webServer` runs `mise run gallery --port 8479` — never 8477 (a live viewer) or 8478 (`serve.PORT`, the gallery default a reader may have open). The app under test is the real dev viewer over the fixture corpus, served over HTTP with the real `default-src 'self'` header. This tier owns only what a `TestClient` structurally cannot see.

- **(3) Every full page in `routes.json` loads with zero console errors, zero page errors, and zero CSP violations.** This is the gap the design opens with: an inline `<style>` or `<script>` in a template is invisible to every test we have. *Evidence:* `page.on('console')`, `page.on('pageerror')` and a `securitypolicyviolation` listener collected per URL; the assertion prints the URL and the message. Killed-mutant proof: adding an inline `style=` attribute to `base.html` locally reds the sweep, recorded in the PR.
- (3) The sweep visits exactly the non-fragment entries `routes.json` carries, and the count is asserted. *Evidence:* a spec-level assertion on the visited count against the filtered list, so a filter that matched nothing reds instead of passing empty.
- (3) The two recorded Python-Playwright traps are confirmed or retired in TypeScript before slice 4 leans on them: `wait_for_function` dying against this CSP (`.claude/rules/viewer-ui.md:168`) and the driver's scroll-into-view measuring the browser rather than the swap (`:107`). *Evidence:* a note in the slice-3 wrap-up naming which held, plus the spec waiting on a selector rather than a function if the first held.

### htmx interactions (slice 4)

Where the 24 `/fragment/…` routes stop being proven only as strings.

- **(4) Clicking a NavTree row swaps `#reading-pane` in place and leaves the NavTree's scroller where it stood.** Six attributes have to be in effect through htmx's inheritance walk, and two of them have harmless-looking defaults (`.claude/rules/viewer-ui.md:43-49`); served HTML reads the attributes, only a browser reads what they do. *Evidence:* click a row already visible, by coordinates — the driver's scroll-into-view measures itself (`:107`); assert the pane's heading names the new node, `location.pathname` matches the row's `href`, and `#nav-tree`'s `scrollTop` is unchanged across the click.
- (4) Pointing at a row fetches its popover once and places it at that row's top. *Evidence:* requests to `/fragment/numbers/` counted through `page.on('request')` — one on the first point, none on the second, so `once` holds; the popover's bounding box top compared against the row's.
- (4) Tabbing onto a row's link fetches the same popover, so the keyboard reaches what the pointer reaches. *Evidence:* focus by `Tab`, popover visible, one further `/fragment/numbers/` request.
- (4) The preset control still points at the selected node after a swap, because it renders inside `#nav-tree-rows`. *Evidence:* after a row click, the three preset links' `href`s name the node just swapped in, not the one the reader left.
- (4) A children-log row's View button opens the child's body in place without moving the reading pane, and no row inside the expansion opens another. *Evidence:* click the toggle; the expansion's body appears under its row, the pane's own title is unchanged, and the expansion's rows carry no further body toggle.
- (4) `?detail=` fetches the rest of a value cut at 4,000 characters, in place. *Evidence:* the detail's text length before and after the fetch, asserted past the cut, on the offload or tool-result page the scenario list already pins.
- (4) Children-log paging turns the page. The pager is a plain link, a full navigation rather than a swap — the leaf proves the turned page, not a swap the code does not do. *Evidence:* click the `?log=` next control; the log's first row changes and the pane's title does not.
- (4) No htmx fetch during any of the above answers other than 200. *Evidence:* a `page.on('response')` guard across the spec, failing with the URL and status.

## chromatic and CI (slice 5) — partly manual

The archive tier cannot be exercised offline: `@chromatic-com/playwright` uploads to a third party and Chromatic renders the archived DOM in its own browser.

- (5) `mise run e2e` passes with no token and no network reachable to Chromatic. *Evidence:* a local run with the variable unset, and the CI job for it on a fork PR, both green.
- **(5) A real Chromatic build appears in the project with one snapshot per full-page scenario, named by the scenario's title.** *Evidence — manual:* the build URL and snapshot count pasted into the PR description, the names read against `SCENARIOS`. Automatable only to the count: nothing in the repo can see the Chromatic project.
- (5) A deliberate visual change is reported and does not block. *Evidence — manual:* a scratch commit altering one token in `style.css` produces a build showing changed snapshots while the job still exits zero, per `--exit-zero-on-changes`; the scratch commit is dropped.
- (5) A fork PR runs the browser tier and skips the upload. *Evidence — manual:* one fork PR against this repo, or the guard leaf above plus a reading of the skipped job.

## not covered

- **Visual regressions blocking.** With `--exit-zero-on-changes` no automated leaf can red on a changed pixel; a human reading Chromatic's UI is the only gate. Deliberate while baselines settle, per the design's decision — tightening it later makes this an obligation.
- **Chromatic's render matching the local browser.** Chromatic re-renders the archived DOM without our CSP header in its own browser. No seam the design names can compare the two, so archive fidelity is Chromatic's claim, not ours.
- **Dark mode and the ≤900px layout** (`style.css:41` and `:321`). Real page shapes, deliberately out of scope; `playwright.config.ts` pins `colorScheme: 'light'` so the default is stated. Both stay witnessed by hand under `.claude/rules/viewer-ui.md`.
- **The dev-reload loop.** Driving a template write from a spec is a different kind of test, and `DEV_SHUTDOWN_SECONDS = 1` (`src/hyphae/view/app.py:52`) makes it fragile under a runner. Stays a manual witness.
- **Anything pointed at the real store.** The gallery cannot serve one, which is what makes uploading page archives to a third party acceptable at all.
- **Font rendering across macOS and `ubuntu-latest`.** Archiving the DOM is the reason this is not our problem; `toHaveScreenshot()` would have made it one.
