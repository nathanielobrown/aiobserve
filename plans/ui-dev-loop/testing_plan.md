# Testing plan: the interactive UI development loop

Obligations for `plans/ui-dev-loop/design.md`, grouped by the design's slices so each is due with its code. Every leaf is an obligation; the *Evidence:* clause names the artifact that discharges it, and an auditor traces each leaf to that artifact.

Three rules shape everything below.

- **The seam is served bytes.** Like the rest of the viewer tier, assertions are on HTML, headers, and status codes over `TestClient` — nothing mocked (`tests/view/conftest.py` docstring). `event_for` is the one pure function, and it tests as one
- **What only a browser can show is witnessed, not asserted.** A reload happening, a stylesheet swapping without one, a click-through working: run it in a real Chromium and record the date, the viewport, and what was seen in `.claude/rules/viewer-ui.md`, the protocol that rule already uses for the scroller
- **watchfiles change sets are invented data**, and are labeled as such at every call site. There is no recorded corpus of them. One slow leaf drives the real `awatch` over `tmp_path` so the invented tuples are checked against the shape watchfiles actually yields

## Levels

- **unit (classification)** — `tests/view/test_dev.py`. `event_for(changes)` in, `"css"` or `"page"` out. No filesystem, no app
- **integration (watcher)** — `tests/view/test_dev.py`, one slow leaf. A real `watchfiles.awatch` over `tmp_path` with a deadline, to prove the invented change sets above have the right shape
- **served HTML (dev vs prod)** — `tests/view/test_dev.py` over `TestClient(build_app(corpus_db, dev=…))`. Two apps over the same session-scoped fixture store, compared byte for byte
- **served HTML (gallery)** — `tests/gallery/test_serve.py` over `TestClient` on the gallery's app object. The index, and what the process can and cannot be pointed at
- **CLI surface** — `tests/test_cli.py`, the existing parametrized defaults table (`:76`) and the `serve` forwarding leaf (`:145`), extended rather than duplicated
- **witnessed (Chromium)** — no automated test. A dated paragraph in `.claude/rules/viewer-ui.md`, per the protocol that file already uses

Every stream read carries a deadline: a `GET /dev/reload` that is read to exhaustion never ends. Use `client.stream(...)` and close, never a bare `client.get`.

---

## Slice 1 — extract `ROUTES` into `tests/view/scenarios.py`

Behavior-preserving. Its obligations are that nothing moved but the text.

- The completeness leaf keeps failing when a route has no entry. *Evidence:* `test_every_route_the_viewer_exposes_is_in_the_payload_sweep` (`tests/view/test_bounds.py:903`) passes with its body unchanged; the diff of that function is empty.
- **The registry's 35 entries survive the move intact.** *Evidence:* `uv run pytest tests/view --collect-only -q` before and after the extraction produces identical test-id lists — three of the tier's files parametrize over `ROUTES` (`test_bounds.py:917`, `test_query.py:36`, `test_enrichment.py:39`), so a dropped or renamed entry changes an id. Bolded: this is the only slice whose whole obligation is that nothing changed, and a diff no test can see is exactly how an entry goes missing.
- The three importers plus `conftest` resolve through one definition, not two. *Evidence:* `grep` finds `ROUTES: dict[str, str] = {` in `tests/view/scenarios.py` and nowhere else; `mise run check` green.

---

## Slice 2 — `view/dev.py`, the `dev` flag, `--dev`, `dev-reload.js`

### unit (classification)

- A change set naming only `.css` files classifies as `"css"`. *Evidence:* invented change set (labeled — watchfiles emits `set[tuple[Change, str]]`, and no recording of one exists) holding `static/style.css` and `static/pygments.css`; assert `event_for(...) == "css"`.
- **A change set mixing a stylesheet with anything else classifies as `"page"`.** *Evidence:* invented set holding `static/style.css` and `templates/node.html`; assert `"page"`. Bolded: this is the branch that decides whether a template edit is silently swallowed by the CSS fast path, and it is the one a naive `any(...)` gets backwards.
- A `.js` edit is a page event, not a CSS one. *Evidence:* invented set holding `static/dev-reload.js`; assert `"page"` — the client script itself only takes effect on a load.
- Classification reads the path, not the kind of change. *Evidence:* parametrize one `.css` path over `Change.added`, `Change.modified`, `Change.deleted`; all three answer `"css"`.
- An empty change set is a broken assumption, not a classification. *Evidence:* `event_for(set())` raises with a message naming the empty set (`.claude/rules/python.md` fail-fast) — asserted with `pytest.raises`. If the implementer instead decides an empty set is `"page"`, the leaf becomes an assertion on that, and the choice gets a why-comment.
- No surviving mutant in the classifier. *Evidence:* `mise run mutate 'hyphae.view.dev.*'` reports zero survivors, or a survivor with a written reason.

### integration (watcher)

- **The tuples the unit leaves invent are the tuples watchfiles yields.** *Evidence:* `@pytest.mark.slow` leaf — `awatch` over `tmp_path` with a `stop_event` and a deadline, touch `x.css`, take the first change set, assert `event_for` on the real set returns `"css"`. Bolded: it is the only thing standing between a green classifier and a client that never reloads, because everything above it is data we wrote ourselves.

### served HTML (dev vs prod)

- **A prod page is byte-identical to a dev page minus the script tag.** *Evidence:* for every URL in `ROUTES`, `dev.content.replace(TAG, b"") == prod.content`, where `TAG` is the one `<script src="/static/dev-reload.js">` line; both apps built over `corpus_db`. Bolded: it proves the prod obligation and the dev obligation with one comparison, and it covers the fragment routes, which never extend `base.html` and must therefore come back identical outright.
- No prod page mentions the dev client at all. *Evidence:* the same sweep asserts `b"dev-reload"` and `b"/dev/"` appear in no prod response.
- A dev page carries the tag exactly once, and only on pages that extend `base.html`. *Evidence:* count of `TAG` per response — 1 for every non-`/fragment/` URL in `ROUTES`, 0 for the fragments.
- `GET /dev/reload` is a 404 under `dev=False`. *Evidence:* `client.get("/dev/reload").status_code == 404` on the prod app.
- **The prod app declares no route under `/dev`, so the completeness leaf never has to list one.** *Evidence:* the `APIRoute` path set read the way `test_bounds.py:908` reads it equals `set(ROUTES)` on the prod app, and equals `set(ROUTES) | {"/dev/reload"}` on the dev app. Bolded: the two halves together are what keeps `ROUTES` meaning "everything the shipped viewer serves".
- `GET /dev/reload` answers `200` with `content-type: text/event-stream`. *Evidence:* `with client.stream("GET", "/dev/reload") as response:` — assert status and header, then leave the block without draining the body.
- **The CSP header is the same string in both modes, on a page and on the stream.** *Evidence:* `response.headers["content-security-policy"] == CSP` asserted on a dev page, a prod page, and the `/dev/reload` stream — the imported constant, the way `tests/view/test_lifecycle.py:39` does it. Bolded: the whole shape of this design (SSE, a static client script) was chosen to keep this string untouched; an assertion that reads it back is what makes that claim checkable.
- `/static/dev-reload.js` is served in both modes and is the file on disk. *Evidence:* the served bytes equal `STATIC/"dev-reload.js"` read from disk. A static mount serves it either way; what matters is that only dev asks for it.
- **`--dev` with watchfiles absent fails at startup, not at the first save.** *Evidence:* hide `watchfiles` from imports (a `sys.meta_path` finder that raises for that name, plus `sys.modules` cleared of `hyphae.view.dev`), then assert `build_app(corpus_db, dev=True)` raises `ImportError` while `build_app(corpus_db)` still returns an app. Bolded: this is the leaf that pins the design's "the shipped viewer gains no dependency" claim — it fails the moment someone hoists `from . import dev` to the top of `app.py`.
- The SSE generator does not hold shutdown open. *Evidence:* open the stream, then exit the `TestClient` context inside a bounded wait; the context manager returns within the deadline. See **Findings** — the honest version of this leaf may need a uvicorn subprocess.

### CLI surface

- `hp view --dev` parses to `dev=True`, and the default is `False`. *Evidence:* the `"view"` row of the `SURFACES` table (`tests/test_cli.py:76`) gains `"dev": False`, discharged by the existing parametrized leaf; plus one parse of `["view", "--dev"]` asserting `dev is True`.
- The flag reaches `serve`. *Evidence:* `test_the_viewer_opens_a_browser_unless_the_run_says_not_to` (`tests/test_cli.py:145`) extended — its recorded tuple carries the `dev` value, and `cli.main("view", "--dev")` records `True`.

### witnessed (Chromium)

- **A template edit reloads the open page.** *Evidence:* a dated paragraph in `.claude/rules/viewer-ui.md`: viewport, the URL held open, the template touched, and that the page came back changed without a keystroke. Bolded: nothing above this line proves the loop actually closes — every automated leaf stops at the server.
- A restarted server refreshes the browser through `EventSource` reconnect. *Evidence:* recorded in the same paragraph — kill the server with a page open, restart, and the page reloads on its own.
- The reload costs the reader nothing but tree width. *Evidence:* recorded — the URL's node, view preset, and knobs come back; note the tree-width slider as the known exception, since `localStorage` carries it (`view/static/tree-width.js`).

---

## Slice 3 — `mise run gallery`

### served HTML (gallery)

- **The index offers one link per `ROUTES` entry, and no others.** *Evidence:* the `href` set scraped from the index equals `set(ROUTES.values())`. Bolded: this is what makes the gallery the tests' scenario list rather than a second registry that drifts.
- Each link is named by the route it stands for. *Evidence:* the index carries one row per entry whose label is the route path template, so `set(ROUTES)` is readable off the page — the walk list a later visual layer needs.
- **The gallery process structurally cannot serve the canonical store.** *Evidence:* `inspect.signature` of the gallery's entry point takes no store, db, or path parameter, and its argparse (if any) declares no such option; the store path it passes to `build_app` comes from the fixture builder's `tmp_path`. Bolded: privacy here is a property of the code's shape, and only a leaf that reads the shape keeps it.
- The gallery is a dev app: the script tag is on its pages and `/dev/reload` answers. *Evidence:* the two dev leaves above, re-run against the gallery's app object.
- The index does not displace a viewer page. *Evidence:* the gallery app's `APIRoute` path set equals `set(ROUTES) | {"/dev/reload", INDEX}` with `INDEX not in ROUTES` — see **Findings**, since `/` is already the projects page.
- The gallery and the `enriched_db` fixture build the same store. *Evidence:* the fixture body at `tests/conftest.py:310` is reduced to a call to the extracted builder, and the tier's enrichment leaves (`tests/view/test_enrichment.py`) stay green unchanged — they read the planted rows, so a builder that stopped planting them fails there.
- `mise run gallery` starts and serves. *Evidence:* the task exists in `mise.toml` with a why-comment and a fixed port distinct from `view.app.PORT` (8477); the port constant is asserted unequal in the gallery test rather than typed twice.

### witnessed (Chromium)

- **A click-through from the index reaches a rendered scenario page.** *Evidence:* dated paragraph in `.claude/rules/viewer-ui.md` — the index opened, a node scenario clicked, the page rendered with its tree and pane. Bolded: the index leaf proves the links exist, not that a person can use the thing.

---

## Slice 4 — the CSS fast path

- A CSS-only event re-busts every stylesheet `<link>` and issues no navigation. *Evidence:* witnessed — a dated paragraph recording a `style.css` touch against an open, scrolled page: the colour changed, the scroll position and any open `<details>` survived, and the network panel shows a stylesheet fetch and no document fetch.
- A non-CSS event still reloads after the fast path lands. *Evidence:* the slice-2 witnessed template-edit paragraph re-run and re-dated after this slice, so the fast path is shown not to have swallowed the general case.
- The classification leaves above are unchanged by this slice. *Evidence:* `tests/view/test_dev.py`'s classification section has an empty diff — the branch was written in slice 2 and only the client's handling of it is new here.

---

## Slice 5 — docs

- `docs/ui-development.md` exists, is linked from the CLAUDE.md Layout tree with a one-line gloss, and every fact in it is defined once. *Evidence:* the `doc-sync` skill run over the branch at PR time, per `docs/documentation.md`.
- No document duplicates the port, the flag name, or the route path. *Evidence:* `grep` for `/dev/reload` and the gallery port across `docs/` finds one defining occurrence each, links elsewhere.

---

## Findings — obligations the design's seam does not reach

Three, none of them dropped.

1. **"A file edit produces an SSE event" is unreachable through `TestClient` as designed.** `view/dev.py`'s watcher runs over the package's own `TEMPLATES` and `STATIC` constants (`view/app.py:90-91`), so an end-to-end leaf would have to write into the real source tree while the suite runs. Fix at implementation: give the router its watch paths as an argument, defaulting to those constants at the one call site in `build_app`. Then a leaf can point a dev app at `tmp_path`, touch a file, and read the event off the stream within a deadline. **Without that parameter, this obligation lives only in the witnessed Chromium paragraph**, which is a weaker artifact than a bounded assertion — a human has to re-run it.
2. **Clean cancellation on shutdown may not be provable at the `TestClient` level.** `TestClient` drives the app through a portal thread, and its shutdown path is not uvicorn's graceful exit — a generator that would hang a real server can pass here. The bounded-context leaf above is worth having, but the design's actual claim ("holds uvicorn's graceful exit open") is only settled by a `@pytest.mark.slow` subprocess leaf: `serve(..., dev=True)` in a child, a stream opened against it, `SIGINT`, and the process reaped within a deadline. Decide at implementation whether the subprocess leaf is worth its seconds; if not, say so in the code.
3. **"Byte-identical to today" cannot be asserted against today.** No golden corpus of prod responses exists, and recording one would rot on the next template change. The comparison above (dev minus the tag equals prod) is the reachable form; what covers regression against *today* is the existing byte pins — `bounds.TREE_ROW_BYTES` and `PAGE_BYTES` in `tests/view/test_bounds.py`, which must appear in this branch's diff not at all.

## Deliberately not covered

- **The visual layer.** No screenshot, no baseline, no pixel diff. The design puts it out of scope and the index leaf above is shaped to be its walk list when it arrives
- **`EventSource` semantics.** Reconnect backoff, `retry:` handling, and last-event-id are the browser's, not ours; the witnessed restart paragraph is the whole of what we claim about them
- **Auto-restart on Python edits.** Out of scope in the design, so there is nothing to pin
- **Concurrent gallery and viewer.** Two processes on two ports share nothing but a read-only fixture store; the port-in-use path is already covered by `test_a_taken_port_names_itself_and_the_way_out` (`tests/view/test_lifecycle.py`)
- **The dev path under a locked or moved store.** `build_app` fails at startup either way (`tests/view/test_lifecycle.py`), and `dev` does not touch that path

## Design claims that did not verify

Checked against the code at `main`, 2026-08-25:

- **The `--dev` flag is not in `view/app.py`.** The parser lives in `src/hyphae/cli.py` — `_view` at `:103` and `_view_arguments` at `:108`; `serve()` is `view/app.py:2103`. The file-tree diff omits `src/hyphae/cli.py` and `tests/test_cli.py`, both of which this change must touch. Give `serve`'s new `dev` parameter no default: the caller decides (`CLAUDE.md`)
- `uvicorn.run(...)` is `view/app.py:2118`, not `:2114`. `CSP = "default-src 'self'"` at `:96` and `Jinja2Templates(directory=TEMPLATES)` at `:497` verified as written; `build_app` is `:487` and its parameter is named `db_path`, not `store`
- **There is no shared enriched-store builder.** `corpus_db` (`tests/conftest.py:284`) calls `build_store`, but `enriched_db` (`:310`) inlines the `EnrichmentStore.upsert` loop in the fixture body. Slice 3 must extract it before it can be reused, which is why the "same builder" obligation above is written as a refactor with the existing enrichment leaves as its evidence
- **The gallery index cannot live at `/`.** `/` is the projects page and a `ROUTES` entry; wrapping `build_app` with an index there would shadow a route the sweep covers. Pick a path outside `ROUTES` — `/gallery` — and pin it with the route-set leaf above
