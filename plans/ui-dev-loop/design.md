# Design: the interactive UI development loop

Two additions that make viewer UI work iterative: a dev-only live reload (watchfiles → SSE → a static client script), and a gallery that serves the test suite's fixture corpus in a browser — so every scenario the tests pin is a page you can open, edit a template against, and watch refresh on save.

## Problem

Editing a template today means saving, switching to the browser, refreshing by hand, and navigating back. The server half of the loop is already live — Starlette's `Jinja2Templates` leaves Jinja's `auto_reload` on (`view/app.py:497`), so an edited template re-renders on the next request — but nothing tells the browser a request is worth making. And the only browser view of a component state is `aiobserve view` over the canonical store: whatever one's own data happens to hold, not the states the tests pin.

Three constraints decide the shape:

- `CSP = "default-src 'self'"` (`view/app.py:96`) stays byte-for-byte. No inline scripts, so the dev client is a static file; SSE is a same-origin GET the policy already allows, where a WebSocket would raise a `connect-src` question
- Session data is private. A dev tool that serves a store must be unable to serve the canonical one — the gallery builds its own store from the redacted fixtures and accepts no store path
- The tests already own the scenario registry: `ROUTES` in `tests/view/conftest.py` maps every route to one real URL, and a completeness leaf fails when a route lacks an entry. The gallery renders that registry rather than a second one that would drift

## Call paths, current → proposed

Current: `aiobserve view` → `build_app(store)` → `uvicorn.run(app, …)` (`view/app.py:2114`). Template edits appear on the next manual refresh.

Proposed:

- `aiobserve view --dev` → `build_app(db_path, dev=True)`, which mounts `view/dev.py`'s router — `GET /dev/reload`, an SSE stream fed by `watchfiles.awatch` over the templates and static directories (watch paths are a router argument defaulting to the package's template/static constants, so a test can point them at a tmp dir) — and sets the template flag that makes `base.html` include `/static/dev-reload.js`. The `--dev` flag itself lands in `cli.py`, where the parser lives
- `dev-reload.js` opens an `EventSource`. An all-CSS event re-busts every stylesheet `<link>` in place — no reload, no state lost; any other event calls `location.reload()`. A dropped stream auto-reconnects, and the reconnect reloads onto whatever the restarted server now serves
- `mise run gallery` → `tests/gallery/serve.py` → builds a fixture store with the enriched-copy logic the `enriched_db` fixture uses (`tests/conftest.py:310` currently inlines it, so slice 3 first extracts a shared builder the fixture and the gallery both call), wraps `build_app(fixture_store, dev=True)` with an index route at `/gallery` (`/` is already the projects page, a `ROUTES` entry) listing `ROUTES` by name, and serves on a fixed port distinct from the live viewer's

Reload is lossless here because every reader state but tree width rides the URL. That architectural fact is what makes a full reload equivalent to hot module replacement, and morphing unnecessary for now.

## File-tree diff

```
src/aiobserve/view/dev.py                 +  SSE reload router; imports watchfiles; imported only when dev
src/aiobserve/view/static/dev-reload.js   +  the client, a static file
src/aiobserve/view/templates/base.html    ~  dev-gated script tag
src/aiobserve/view/app.py                 ~  build_app(db_path, dev=…); serve() forwards dev
src/aiobserve/cli.py                      ~  --dev flag on the view command
tests/test_cli.py                         ~  --dev parsing and forwarding
tests/view/scenarios.py                   +  ROUTES and the ids it names, extracted from conftest
tests/view/conftest.py                    ~  imports scenarios
tests/conftest.py                         ~  enriched-store build extracted to a shared builder
tests/view/test_dev.py                    +  dev-mode leaves
tests/gallery/serve.py                    +  fixture-store build + index route + uvicorn (index template beside it)
mise.toml                                 ~  [tasks.gallery]
pyproject.toml                            ~  watchfiles (dev group)
docs/ui-development.md                    +  the guide to this flow
docs/viewer.md, README.md, CLAUDE.md,
.claude/rules/viewer-ui.md                ~  links, Layout entry, and the "two scripts" line
```

## Key contracts

- `build_app(db_path, dev: bool = False)` — dev mounts the reload router and includes the script tag; prod pages stay byte-identical to today
- `view/dev.py` — a router factory taking the watch paths (defaulting to the package's template/static dirs), plus a pure `event_for(changes) -> "css" | "page"` mapping a watchfiles change set to an event, so the classification tests without a filesystem. The SSE generator must cancel cleanly on shutdown or it holds uvicorn's graceful exit open
- `dev-reload.js` — CSS events swap stylesheets in place; anything else reloads; reconnect reloads
- `tests/view/scenarios.py` — `ROUTES: dict[str, str]`, imported by conftest (completeness leaf unchanged) and by the gallery
- watchfiles sits in the dev dependency group; `view/dev.py` imports it and is itself imported only under `dev=True`, so the shipped viewer gains no dependency and `--dev` without dev deps fails fast

## Chosen test seam

Served HTML and status codes over `TestClient`, like the rest of the tier: dev pages carry the script tag and `/dev/reload` answers `text/event-stream`; prod pages carry neither. `event_for` tests as a pure function. Browser behavior — the reload itself, the CSS swap without one — is witnessed in a real Chromium and recorded in the rule file, the protocol `viewer-ui.md` already uses for the scroller. The gallery index gets one leaf: a link per `ROUTES` entry.

## Slices

1. Extract `ROUTES` into `tests/view/scenarios.py`. Behavior unchanged; `mise run check` green
2. `view/dev.py`, the `dev` flag, `--dev`, and `dev-reload.js` with full reload only. Verified by `test_dev.py` plus a witnessed template edit reloading a real browser
3. `mise run gallery`: fixture-store build, index route, fixed port. Verified by the index leaf and a witnessed click-through
4. The CSS fast path in `dev-reload.js`. Witnessed: a `style.css` touch restyles the open page without a reload flash
5. Docs: `docs/ui-development.md` written per `docs/documentation.md` — the flow end to end: `mise run gallery`, picking a scenario, the edit-save-refresh loop, `--dev` against one's own store, and how `ROUTES` feeds both tests and gallery — plus the README pointer, `viewer-ui.md`'s two-scripts line, and the CLAUDE.md Layout entry. Landed with doc-sync at PR time

## Decisions

- SSE over WebSocket — `EventSource` auto-reconnect is the entire restart story, and the stream is a same-origin GET the CSP already allows; WS adds a `websockets` dependency and a CSP carve-out
- Hand-rolled over arel — arel injects an inline script the CSP forbids, rides WS, and only knows full reload; the upgrades that matter here (CSS swap, morph later) would mean forking it anyway
- Gallery as test tooling, not a package feature — it may import `tests/` freely, and privacy is structural: the process can only build and serve the redacted fixture corpus
- Reuse `ROUTES` over a new registry — the completeness leaf already forces coverage; a second list would drift
- Full reload first, CSS swap second, no morph — URL-carried state makes reload lossless; vendoring idiomorph is its own small change for when the flash starts to grate
- `--dev` on the live viewer too, not gallery-only — the loop is as useful over one's own store, and it costs a parameter

## Out of scope

- The Playwright/Chromatic visual layer. The gallery index is deliberately shaped to be its walk list, but snapshots, baselines, and interaction steps are a separate plan
- Auto-restart on Python edits. `uvicorn.run(app_object)` cannot reload (the reloader needs an import string), the SSE reconnect already refreshes the browser after a manual restart, and the loop this serves — template and CSS edits — needs no restart at all
- A macro-level harness. Fragments render whole at URLs of their own, so URL scenarios already give component-scoped pages

## Open questions

- Rebuild the fixture store on every gallery launch, or cache it under `data/`? Settled by timing one build at implementation
- Should the index also list the exhaustive `pages()` enumeration, collapsed beneath the curated `ROUTES`? Cheap to try
- The gallery's port — fixed, and distinct from the live viewer's default; confirm that default in `view/app.py` before picking
