# Visual testing and the browser tier

## Problem

Three gaps, one shape between them.

The viewer has no browser tier. `tests/view/test_bounds.py:445` proves every route answers 200 under budget through a `TestClient`, but nothing proves htmx swaps land where they should, and nothing proves the CSP holds — `default-src 'self'` (`src/hyphae/view/app.py:59`) forbids inline scripts and styles, and one slipped into a template is invisible to every test we have. No test sees a rendered pixel.

The gallery lists 38 rows of route template plus a raw URL carrying UUIDs. It says what a page *is addressed as*, never what it *shows*.

The constraint that decides the shape: **Chromatic has no Python integration.** Both non-Storybook paths, `@chromatic-com/playwright` and `@chromatic-com/cypress`, are npm packages that hook the JS runner's lifecycle to archive each page. So the browser tier is TypeScript, and it needs a name per page — which is the same thing the gallery needs. One list serves both.

## Call paths, current → proposed

**The scenario list.** Today `tests/view/scenarios.py:42` is `ROUTES: dict[str, str]`, route template → URL. Six modules read it (`test_bounds.py`, `test_dev.py`, `test_enrichment.py`, `test_query.py`, `tests/gallery/test_serve.py`, `tests/gallery/serve.py`). It becomes `SCENARIOS: dict[str, Scenario]`, and each reader takes `.url` off the value. A new generator writes `tests/e2e/routes.json` from it; a leaf reads the checked-in file back against `SCENARIOS` so a drifted copy reds.

**The clock.** `fmt.utcnow()` is the viewer's only clock — `templating.py:43` and `listing.py:290` both reach it as a module attribute at call time, and `listing.py:287` deliberately keeps SQL's `now()` out. `tests/gallery/serve.py:66` will `setattr(fmt, "utcnow", ...)` to a constant derived from the corpus before `build_app`, so a snapshot taken today and one taken next month agree. `format.py:32-36` already documents this as the intended seam.

**The browser.** Playwright's `webServer` runs `mise run gallery --port 8479` — not 8477 (a live viewer) and not 8478 (a gallery you have open), per `.claude/rules/viewer-ui.md:102`. Specs read `routes.json`, drive the page, and `@chromatic-com/playwright` archives it. `npx chromatic --playwright --exit-zero-on-changes` uploads.

## File-tree diff

```
tests/view/scenarios.py       ROUTES -> SCENARIOS; Scenario, Group added
tests/gallery/index.html      grouped headings; titles as link text
tests/gallery/serve.py        freeze the clock before build_app
tests/{view,gallery}/test_*   six call sites take .url
tools/e2e_routes.py           + writes tests/e2e/routes.json
tests/e2e/                    + package.json, playwright.config.ts, routes.json
  specs/pages.spec.ts         + console-error sweep + one archive per page
  specs/htmx.spec.ts          + popover, expansion, detail, log paging
mise.toml                     node pinned; sync, format-ts, e2e, e2e-chromatic
.github/workflows/e2e.yml     + a second workflow, the only one holding a secret
```

## Key contracts

```python
class Group(StrEnum):          # the gallery's headings, in display order
    PAGES = "Pages"
    NODES = "Node kinds"
    VALUES = "Value fetches"
    ENRICHMENT = "Enrichment fetches"
    PARTS = "Page parts"
    QUERY = "Queries"

class Scenario(NamedTuple):
    url: str        # one real URL against the fixture corpus
    title: str      # what the page shows, in words — the gallery's link, Chromatic's snapshot name
    group: Group
    note: str = ""  # why this URL and not another, where the title cannot say

SCENARIOS: dict[str, Scenario]  # keyed by the route's own path template, as today
```

`note` carries the only default: most rows need none, and the ones that do are the ones whose `#` comments already explain the choice (the described-enrichment ids at `scenarios.py:34-36`, the turned-down `kin` window at `:138`).

The gallery index drops the raw URL column. Each group becomes a heading; each row is the title as link text with the route template beside it in `<code>`. `data-scenario="{{ route }}"` stays — `tests/gallery/test_serve.py:93` reads it.

## Chosen test seam

Two tiers, no overlap. The Python tier keeps driving `TestClient` and keeps owning the sweep — re-checking 38 routes for 200 in a browser buys nothing. The browser tier drives the gallery over HTTP and owns only what a `TestClient` structurally cannot see: console errors under CSP, htmx swaps landing in the right target, and the rendered page. `routes.json` is the contract between them.

Chromatic archives the DOM and renders it in its own browser, so the system font stacks at `style.css:39,119` don't have to match between macOS and `ubuntu-latest`. That is the whole reason not to use Playwright's own `toHaveScreenshot()` here.

## Slices

1. **`Scenario` and the gallery.** The model, the six call sites, the grouped index. Verified by the existing suite plus a leaf asserting every scenario carries a title and a group, and `mise run format-html`.
2. **The frozen clock.** Verified by a leaf that renders a page twice with `fmt.utcnow` moved a week between, and asserts the `ago` text is identical.
3. **Node, Playwright, and the page sweep.** `routes.json` and its staleness leaf, the toolchain, and `pages.spec.ts` asserting zero console errors on each full page. Verified by `mise run e2e` offline — no Chromatic yet.
4. **htmx interactions.** Popover on hover and on focus, a log row's View button opening an expansion, `?detail=` fetching the rest, children-log paging. This is where the 24 `/fragment/…` routes stop being proven only as strings.
5. **Chromatic and CI.** `@chromatic-com/playwright`, `e2e.yml`, the token. Verified by a real build appearing in the project.

## Decisions

- **Playwright, not Cypress.** Cypress needs `ELECTRON_EXTRA_LAUNCH_ARGS=--remote-debugging-port=9222` to expose CDP for archiving and locks archiving to Chrome; Playwright's `webServer` also owns the gallery's lifecycle for us.
- **TypeScript, not Python Playwright.** Not a speed judgement — `pytest-playwright` cannot reach Chromatic at all.
- **Mock the clock, don't inject it.** Rejected adding a `clock` parameter to `build_app`: `fmt.utcnow` is already a single module-attribute seam written for this, and injection would change a production signature to serve a test. Rejected `.chromatic-ignore` markers because they add bytes to the pinned NavTree row budget, spent 3,217 times on the worst page.
- **Freeze the gallery's clock always, no flag.** `tests/gallery/test_serve.py:77` asserts the module never calls `getenv`, and `parser()` exposes only `--port`. The fixture dates are static, so a human sees stable ages rather than slowly ticking arbitrary ones.
- **Snapshot the ~14 full pages, not the fragments.** A fragment is partial HTML with no `base.html`; Chromatic archives whole pages anyway, so fragments get covered mid-interaction in slice 4. 14 snapshots a build against a 5,000/month free tier leaves room for ~350 builds.
- **`--exit-zero-on-changes`.** Report, don't block, while baselines settle. Tightening later is deleting one flag.
- **A second workflow, not a step in `check.yml`.** That workflow's header promises no secret and no network; `e2e.yml` is where both live. It needs `fetch-depth: 0` for Chromatic's baseline lookup, and a guard — the repo is public, so a fork PR gets no token: `if: github.event.pull_request.head.repo.full_name == github.repository`.
- **`SCENARIOS`, renaming `ROUTES`.** The values stop being routes. Six call sites, all in tests, and the project prefers a clean break to a shim.

## Out of scope

- **Dark mode and the 900px breakpoint** (`style.css:31,249`). Both are real page shapes and both are worth snapshotting later; each doubles the snapshot count, and baselines should hold still at one viewport first. `playwright.config.ts` pins `colorScheme: 'light'` so the default is stated rather than inherited.
- **The dev-reload loop.** Saving a template and watching the page reload is witnessed manually today (`.claude/rules/viewer-ui.md:58-117`). Driving a file write from a spec is a different kind of test; the reload stream's `DEV_SHUTDOWN_SECONDS = 1` cap also makes it fragile under a runner.
- **Anything pointed at the real store.** The gallery cannot serve it — that property is what makes uploading page archives to a third party acceptable at all. `playwright.config.ts` names the gallery and nothing else.
- **TurboSnap.** It is git-diff-driven snapshot skipping for Storybook projects and does not apply.

## Open questions

- **Do the recorded Playwright traps carry to TypeScript?** `.claude/rules/viewer-ui.md:115` records that `wait_for_function` dies against this CSP, and `:78` that the driver's scroll-into-view measures the browser rather than the swap. Both were witnessed in Python Playwright. Slice 3 should confirm or retire them in JS before slice 4 depends on either.
- **Where does the token enter a local run?** `.env` is read by `load_dotenv()` in `cli.py:251`, which is Python and nowhere near the npm CLI. Simplest is the `e2e-chromatic` task sourcing `.env` itself and refusing to run on an empty token. Setting mise's `_.file` would instead put OTLP ingest keys into the environment of every task, which is worse.
- **Does `tests/tools/test_mise_tasks.py` need the new tasks' paths?** It reads the task list back against the tree; `format-ts` names a directory the way `format-html` does.
