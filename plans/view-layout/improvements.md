# Improvements noticed while reorganizing `view/`

The implementer's log for `design.md`'s cleanup policy: what a move exposed that the move didn't fix. One item per bullet with `file:line` (at the new path once moved), what is wrong, and the fix you would make. Mark each **done** with its commit, or leave it for the next pass.

The seed below was read from the tree on 2026-09-01, before any move. Verify each before acting.

## The byte baseline every slice diffs against

No leaf compares whole responses before and after a move (`testing_plan.md`, "design
findings"), so each slice re-renders every `tests/view/scenarios.py:SCENARIOS` URL and diffs
against slice 1's capture. The script is scratch and is not committed:

```
cd /Users/nob/repos/hyphae-wt/view-layout
env -u VIRTUAL_ENV PYTHONPATH=$PWD uv run python \
  /Users/nob/repos/hyphae/handoffs/view_layout_capture.py /tmp/view-layout-baseline slice<N>
diff -r /tmp/view-layout-baseline/slice1 /tmp/view-layout-baseline/slice<N>
```

It builds the enriched fixture store once into `/tmp/view-layout-baseline/traces.duckdb` and
reuses it, and freezes `format.utcnow` to the corpus's own present the way the gallery does —
without both, two captures differ on the trailing windows every list page prints. Slice 1's
capture is 39 files, all 200, and re-running it against the same tree gives an empty diff.
Slices 2, 3, 4 and 6 are byte-identical to slice 1's (slice 5 shares slice 4's capture:
the two were landed in one pass because `pages.py` and `pages/` cannot coexist).

## Seeded from the design pass

- `src/hyphae/view/knobs.py:23` — `checked` raises `HTTPException` from a presenter module; a route concern living below the routes. Design lifts it to `deps.py`. **Done** in `5c70faf`, with `viewed`, `Knobs.asked` and `KnobsDep`: see "Found during the move"
- `src/hyphae/view/nodes.py:23` — the shared node model imports `columns`, a children-log module, for the kind icons; the dependency points up. Design moves the icons into `nodes.GLYPHS`
- `src/hyphae/view/components/listing.py:191` — `Described` beside `enrichment.py`'s model of what a pass wrote; check whether both describe the same fact. **Closed at slice 4, no change**: they are not one fact. `enrichment.Enrichment` is what a pass wrote about a node — level, item, chars, friction, model, versions — and is read by the node page. `Described` is three strings a session-list row prints. It has no second reader, so under "view-models live in markup" it stays in `pages/sessions/markup.py:31`
- `src/hyphae/view/components/listing.py` (489 lines) — holds both list pages; the split into `projects/` and `sessions/` should leave no helper shared between them, or that helper belongs in `components/parts.py`. **Closed at slice 4**: the split left no helper shared. `LIST_URL`, `list_url` and `project_link` were the one thing both halves read, and they went to the new shared `view/links.py` (`83bc613`), not to `components/parts.py` — they mint a URL, which is not markup
- `src/hyphae/view/knobs.py:18-19` — a presenter importing `PresetChoice`, `Pager`, `Step` from markup modules; allowed under "view-models live in markup", but see the design's open question
- `src/hyphae/view/expansions.py` — the audit's S6 said it re-spells `header_bound`'s bindings inline; check whether that still stands once `header_bound` is in `store.py`. **Closed**: it does not. `expansions.py:281` already calls `header_bound`, and `9158c28` moved that function to `store.py`
- Audit items still open as of the last read (`plans/refactor-audit-2026-08-30/findings.md`): S2 one `Knobs` object, S3 `node_page.page()`'s 21 parameters, S12 double store open per enrichment fragment, S13 keyword-only `detail_of`, S14 `way` as a `StrEnum`. Record for each whether it still stands after the move. **None of the five still stands**, and all five were closed by the `Depends` work that landed before this branch rather than by it — read at slice 3, against the tree:
  - S2 — closed. `knobs.Knobs` is the NamedTuple, `deps.KnobsDep` the one dependency, and `browse.browse` takes one `Knobs` rather than four positionals
  - S3 — closed. `components/node_page.py:page()` takes eight keyword parameters, grouped into `Nav`, `Body`, `Bearings`, `Children`
  - S12 — closed. Every enrichment fragment takes `connection: Db`, and `enriched(connection)` reads the one the request opened
  - S13 — closed. `detail.detail_of` is keyword-only and `detail.details(*maybe)` is the collector every route calls
  - S14 — closed. `components/node_page.py:309` is a two-member `Way` StrEnum

## Found during the move

- `src/hyphae/view/components/parts.py:11` — shared markup imports `Detail` and
  `EnrichmentLines` from `view/detail.py`, which the design puts in `pages/node/`. Rule 3 reds
  on that edge at slice 6: `components/` is under every page and cannot read one page's
  presenter. Fix: lift the two view-models out of `detail.py` — `EnrichmentLines` beside
  `enrichment.py`, `Detail` with it — and leave the reading of a fat value in `pages/node/`.
  Found at slice 1 while writing `test_layout.py`; nothing in slices 1–2 touches it.
  **Closed at slice 3 without a code change, by reading what `detail.py` imports**: `queries`,
  `enrich.items.Level`, `nodes`, `enrichment.Enrichment`, `text/`. Nothing node-page-specific,
  so the module is shared and the design's file tree is wrong to list it under `pages/node/`.
  `test_layout.py:LAYERED` now names it `SHARED`; slice 6 leaves `detail.py` where it is
- `src/hyphae/view/errors.py:26-27` — the module the design renames to the shared
  `failures.py` imports `builders.tool_node` and `nav_tree.Ran`, both of which the design puts
  in `pages/node/`. Same red at slice 6, and the lift of `failures.py` into the shared layer is
  what causes it. Fix: `Ran` is a list of the queries a page ran, so it belongs in
  `citation.py`; `tool_node` builds a `Node` from a store row, so `builders.py` reads like a
  shared module rather than a node-page presenter. Decide both before slice 3 renames the file.
  **Closed at slice 3, both as proposed.** `Ran` is now `citation.py:23`, beside the `cited()`
  that consumes it. `builders.py` stays where it is and `test_layout.py:LAYERED` names it
  `SHARED`: it turns a store row into a `Node`, reads only `nodes`, `enrichment`, `store` and
  `text/`, and has five readers on both sides of the page boundary. So the design's file tree
  is wrong twice — `detail.py` and `builders.py` are both shared, not `pages/node/` presenters.
  This adds no layer: both were already inside the design's shared layer, only mis-placed
- `tests/view/text/test_render.py:228` — a comment explaining the suppression under it opened
  with `noqa:`, so ruff read it as a directive and warned on every lint run. **Done** in
  `e7a6fc9`, as the cleanup commit after slice 2's move
- `src/hyphae/view/browse.py:25` — the last node-page presenter that reaches a web framework:
  it takes a `deps.Viewer` and returns `viewer.html(...)`, so rule 1's probe will red on
  `pages/node/browse.py` at slice 6. Fix: have `browse` return `Html` and let each node route
  wrap it, which is the same seam every other presenter already sits on.
  **The recorded fix was incomplete, and slice 6 took the other one.** `browse` also raises
  `HTTPException` three times (a page below the first is a 400; a session or a log page the
  store does not hold is a 404), so returning `Html` leaves it importing a framework. It
  decides a status and builds a response, which is route work, so it moved to
  `pages/node/routes/browse.py` and rule 1 is satisfied with no line changed inside a function.
  The alternative, left for a later pass: a framework-free refusal in the shared layer — one
  `Refused(status, message)` that `browse`, `checked`, `viewed` and `routes/details.fetched`
  raise and one handler in `app.py` translates. That would let `browse` be a presenter, and is
  the same change four call sites want; it is bigger than a slice-6 cleanup and touches the
  error contract, so it is a decision rather than a tidy-up
- `src/hyphae/view/failures.py:49` — `failures.failures(connection, session_id)` at the two
  call sites, a stutter the rename made. The design pinned the module's names, so this is left:
  the fix is a verb for the function, not another word for the module

- `src/hyphae/view/pages/node/routes/enrichment.py:22` — reaches sideways for
  `routes/details.fetched`, the "one row a per-value fragment is for, or a 404". Two route
  modules of one page sharing a helper is legal and cheap, but `fetched` reads as neither an
  enrichment line nor a detail. Fix, if a third caller arrives: it belongs with the `Refused`
  above, since all it does is raise one
- `tests/view/test_app__headers.py:110` — the label registry's `asked` scan globbed
  `components/` alone, so it went silent the moment a `fact()` call moved into a page's own
  markup. Fixed in the slice-6 move by walking the whole view package, which is what its own
  docstring and its companion assertion already promised. The other half of the scan
  (`previewed`) had been widened for exactly this reason and this half was missed
- `tests/view/test_layout.py` — two scans widened with the tree in slice 6, both noted here
  because each is a rule reading less than it did: page discovery is now `pages/*/` rather than
  every package under `pages/`, so the node page's own `routes/` and `markup/` are kinds rather
  than pages; and a `markup/` package's `__init__.py` is left out of `markup_modules()` the way
  `test_components.py:MODULES` leaves the components package's out
- `src/hyphae/view/bounds.py` — named a leaf beside `text/` rather than a layer above it in
  `test_layout.py:LAYERED`. `bounds` imports nothing inside the viewer and `highlight` and
  `inline_markdown` read their cuts from it, which is what the design says a size is; the
  alternative was an exemption inside the rule-3 loop, which would have hidden a real edge
