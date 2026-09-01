# Improvements noticed while reorganizing `view/`

The implementer's log for `design.md`'s cleanup policy: what a move exposed that the move didn't fix. One item per bullet with `file:line` (at the new path once moved), what is wrong, and the fix you would make. Mark each **done** with its commit, or leave it for the next pass.

The seed below was read from the tree on 2026-09-01, before any move. Verify each before acting.

## Seeded from the design pass

- `src/hyphae/view/knobs.py:23` — `checked` raises `HTTPException` from a presenter module; a route concern living below the routes. Design lifts it to `deps.py`
- `src/hyphae/view/nodes.py:23` — the shared node model imports `columns`, a children-log module, for the kind icons; the dependency points up. Design moves the icons into `nodes.GLYPHS`
- `src/hyphae/view/components/listing.py:191` — `Described` beside `enrichment.py`'s model of what a pass wrote; check whether both describe the same fact
- `src/hyphae/view/components/listing.py` (489 lines) — holds both list pages; the split into `projects/` and `sessions/` should leave no helper shared between them, or that helper belongs in `components/parts.py`
- `src/hyphae/view/knobs.py:18-19` — a presenter importing `PresetChoice`, `Pager`, `Step` from markup modules; allowed under "view-models live in markup", but see the design's open question
- `src/hyphae/view/expansions.py` — the audit's S6 said it re-spells `header_bound`'s bindings inline; check whether that still stands once `header_bound` is in `store.py`
- Audit items still open as of the last read (`plans/refactor-audit-2026-08-30/findings.md`): S2 one `Knobs` object, S3 `node_page.page()`'s 21 parameters, S12 double store open per enrichment fragment, S13 keyword-only `detail_of`, S14 `way` as a `StrEnum`. Record for each whether it still stands after the move

## Found during the move

(append here)
