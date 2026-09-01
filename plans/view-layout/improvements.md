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

## Seeded from the design pass

- `src/hyphae/view/knobs.py:23` — `checked` raises `HTTPException` from a presenter module; a route concern living below the routes. Design lifts it to `deps.py`
- `src/hyphae/view/nodes.py:23` — the shared node model imports `columns`, a children-log module, for the kind icons; the dependency points up. Design moves the icons into `nodes.GLYPHS`
- `src/hyphae/view/components/listing.py:191` — `Described` beside `enrichment.py`'s model of what a pass wrote; check whether both describe the same fact
- `src/hyphae/view/components/listing.py` (489 lines) — holds both list pages; the split into `projects/` and `sessions/` should leave no helper shared between them, or that helper belongs in `components/parts.py`
- `src/hyphae/view/knobs.py:18-19` — a presenter importing `PresetChoice`, `Pager`, `Step` from markup modules; allowed under "view-models live in markup", but see the design's open question
- `src/hyphae/view/expansions.py` — the audit's S6 said it re-spells `header_bound`'s bindings inline; check whether that still stands once `header_bound` is in `store.py`
- Audit items still open as of the last read (`plans/refactor-audit-2026-08-30/findings.md`): S2 one `Knobs` object, S3 `node_page.page()`'s 21 parameters, S12 double store open per enrichment fragment, S13 keyword-only `detail_of`, S14 `way` as a `StrEnum`. Record for each whether it still stands after the move

## Found during the move

- `src/hyphae/view/components/parts.py:11` — shared markup imports `Detail` and
  `EnrichmentLines` from `view/detail.py`, which the design puts in `pages/node/`. Rule 3 reds
  on that edge at slice 6: `components/` is under every page and cannot read one page's
  presenter. Fix: lift the two view-models out of `detail.py` — `EnrichmentLines` beside
  `enrichment.py`, `Detail` with it — and leave the reading of a fat value in `pages/node/`.
  Found at slice 1 while writing `test_layout.py`; nothing in slices 1–2 touches it
- `src/hyphae/view/errors.py:26-27` — the module the design renames to the shared
  `failures.py` imports `builders.tool_node` and `nav_tree.Ran`, both of which the design puts
  in `pages/node/`. Same red at slice 6, and the lift of `failures.py` into the shared layer is
  what causes it. Fix: `Ran` is a list of the queries a page ran, so it belongs in
  `citation.py`; `tool_node` builds a `Node` from a store row, so `builders.py` reads like a
  shared module rather than a node-page presenter. Decide both before slice 3 renames the file
