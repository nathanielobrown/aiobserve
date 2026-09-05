# Design: name a surface's widths once

Each viewer surface declares the widths it prints at as one object in `view/bounds.py`; a read names the surface and the store binds the numbers. Under it, the popover's charge lines come out of `reads.py` typed and labelled by the one registry. Audit items S34 (the width a surface runs is nowhere declared), C34 (viewer widths live in `analyze/queries.py`) and S9 (a citation rebuilt beside its query) in `plans/refactor-audit-2026-08-30/findings.md`.

## Problem

Every `view_*` parameter is required (`view/manifest.py`), so every read spells its widths by hand: `{"head_chars": queries.HEADER_CHARS, "detail_chars": knobs.detail, …}`. This grep finds the sites — about fifty lines in twelve modules today:

```sh
rg -n '(chars|items|kinds|projects|errors|records|categories|days)"?\s*[:=]\s*(queries|bounds)\.' src/hyphae/view
```

The pairing of parameter to width lives only in those lines. `chip_chars` is bound at four values in four modules — 110 in `pages/node/nav_tree.py`, 300 in `routes/browse.py`, 100 in `routes/pages.py` (the compaction page), 60 in `routes/popovers.py` — and each is right for its surface, but nothing says so. A swapped constant renders a page that looks fine and breaks the byte arithmetic `tests/view/budgets.py` does over `bounds.py`. The guard is `tests/view/test_bounds.py:test_the_pages_run_at_the_production_sizes`, twenty hand-written assertions over the footers of the pages that carry one; a fragment carries none. Three local fixes for the same problem already exist — `store.header_bound`, `store.list_bound`, `store.DESCRIBED_BOUND` — each sharing one query's pairing between its readers.

The constraint that decides the shape: the citation under a page must keep quoting the exact numbers the query ran at, from the same mapping the query bound (`view/citation.py`). So the profile has to produce that mapping, not stand beside it.

The second half: `pages/node/numbers.py:charges` reads `row[f"{field}_tokens"]` off the `view_numbers` row, past the `reads.py` seam whose docstring says a body or popover reads named fields of a type past it, and names its lines from a private `_LINES` table ("cache read", "new input", "output") beside `text/labels.py`.

## Call paths, current → proposed

A header read today (`routes/pages.py:turn_page.read`):

```
bound = {"session_id": …, "turn_id": …, "head_chars": queries.HEADER_CHARS, "detail_chars": knobs.detail}
rows  = store.page_rows(connection, Page.TURN_HEADER, **bound)
Seen.ran → browse.browse → cited(Page.TURN_HEADER, bound) → footer
```

Proposed:

```
bound = store.bound(Page.TURN_HEADER, bounds.HEADER, session_id=…, turn_id=…, detail_chars=knobs.detail)
rows  = store.page_rows(connection, Page.TURN_HEADER, **bound)        # unchanged
Seen.ran → browse.browse → cited(Page.TURN_HEADER, bound) → footer   # unchanged
```

`bound` reads the query's declared parameters off `analyze.manifest.QUERIES[page].params`, takes each from the keys if given, else from the profile's field of that name, and raises naming the page, the parameter and the surface when neither has it. A key that is also a profile field raises too. The footer sees the same dict it sees today.

The popover today (`routes/popovers.py:counted`): `page_rows(Fragment.NUMBERS)` → `rows[0]["session_usd"]`, `numbers.charges(rows[0], numbers.spend(rows[0]["spent"]), whole)`. Proposed: `read = reads.node_numbers(rows[0])` → `numbers.charges(read, numbers.spend(read.spent), read.session_usd)`, `breakout(read.cost_usd, read.subtree_usd, read.session_usd)`. No `Row` reaches `numbers.py` or the route.

## File-tree diff

```
src/hyphae/view/bounds.py                 changed: one NamedTuple class and instance per surface; the viewer-only
                                          widths move in from analyze/queries.py with their comments
src/hyphae/view/store.py                  changed: + bound(); − header_bound, DESCRIBED_BOUND; list_bound and
                                          SHOWN's composition read bounds.LIST by attribute
src/hyphae/analyze/queries.py             changed: viewer-only widths leave (C34); LOG_CHARS, LOG_CHARS_PARAM stay
src/hyphae/view/pages/node/routes/        changed: pages, browse, expansions, details, popovers call bound()
src/hyphae/view/pages/node/nav_tree.py    changed, with failures.py, enrichment.py, detail.py
src/hyphae/view/pages/{sessions,projects,records}/routes.py   changed
src/hyphae/view/text/cuts.py, nodes.py    changed: a Python-side cut reads bounds.<SURFACE>.<parameter>
src/hyphae/view/pages/node/reads.py       changed: node_numbers replaces window_numbers
src/hyphae/view/pages/node/numbers.py     changed: Numbers; charges over it; spend over typed pairs; _LINES and Row gone
src/hyphae/view/pages/node/markup/numbers.py  changed: Charge fields
src/hyphae/view/text/labels.py            changed: + new_input_tokens
src/hyphae/analyze/queries/view_numbers.sql   changed: cached_tokens → cache_read_tokens
src/hyphae/view/static/nav-tree.css       changed: .popover dt lowercases the registry's word
tools/gen_bounds.py, tests/view/budgets.py    changed: read the profiles
tests/view/test_store.py                  new: bound()'s contract
tests/view/test_layout.py                 changed: + no width read off analyze.queries outside bounds.py
tests/view/test_bounds.py                 changed: the production-sizes pin reads profiles
tests/view/pages/node/test_numbers.py     changed: the charge fields' names
docs (doc-sync): viewer-bounds.md, view/manifest.py and analyze/queries.py docstrings, .claude/rules/viewer-ui.md
```

## Key contracts

A surface's widths, in `view/bounds.py`. Field names are the query parameters the surface binds; values are the surface's. One class per surface, one instance, no defaults:

```python
class Header(NamedTuple):
    """The pane's header: every string a node's own row carries, and its two lists."""
    head_chars: int
    item_chars: int
    head_items: int
    chip_chars: int          # a compaction's trigger, when the compaction is the node

HEADER = Header(head_chars=100, item_chars=60, head_items=5, chip_chars=100)
```

The set, with what each binds today: `NAV_TREE` (nav_chars, chip_chars, log_chars — all 110), `HEADER` as above, `LOG` (log_chars 300, chip_chars 300: a run row and a call row), `EXPANSION` (head_chars 100, detail_chars 100 — a body previews nothing at the reader's `?detail=`), `POPOVER` (model_chars 60, chip_chars 60, item_chars 60, head_items 5), `LIST` (head_chars 100, item_chars 20, head_items 4, tag_chars 20, kind_chars 20, head_kinds 3, head_projects 10), `PROJECTS` (head_chars 100, projects, recent_days 7, window_days 30), `ERRORS` (nav_chars 110, errors), `RECORDS` (preview_chars 160), `ENRICHMENT` (description_chars 200, tag_chars 20, head_chars 100). `Widths` is the union of the ten classes. A number two surfaces share for a reason stays a module constant they both read (`NAV_CHARS`: an errors row leads to a node and is titled as its NavTree row is); a number they merely share stays two literals, as `LIST_CHARS` and `HEADER_CHARS` are two today. `Bound` and the knob ceilings are untouched: a knob is the reader's, a width is the surface's.

```python
def bound(page: Library, widths: Widths, **keys: ParamValue) -> dict[str, ParamValue]:
    """What one read binds: its keys and request sizes, and the surface's widths for the rest.

    Every parameter the manifest declares for `page` is filled or this raises, naming the surface;
    a key that names a profile field raises too — an override is a second surface, so declare one.
    """
```

A read that serves two surfaces names the wider (`browse.py` reads `view_runs` once for the NavTree and the runs log and binds `bounds.LOG`, as its comment already says). The offload page is the one read with no fixed width — both its sizes are the URL's — and binds `page_rows` directly.

The popover's read, in `pages/node/numbers.py`, filled by `reads.node_numbers(row)`:

```python
class Numbers(NamedTuple):
    window: markup.Window                     # what the popover prints of the window
    cache_read_tokens: int | None
    new_input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    subtree_usd: float | None
    session_usd: float | None
    spent: tuple[tuple[str, TokenUsage], ...]  # one (model, usage) per group, for split_cost
```

`charges(read: Numbers, split, whole)` spells its three lines once, each with `label("cache_read_tokens")`, `label("new_input_tokens")`, `label("output_tokens")` written literally — `tests/view/test_app__headers.py` closes `LABELS` over literal `label(` calls, so the new entry `"new_input_tokens": "New input"` is asked for by name. `Charge` carries the tokens field, the dollar field (`cache_read_usd`, `new_input_usd`, `output_usd`, beside the store's `cost_usd`) and the label; `data-field` on the popover's `dd`s takes those names. `view_numbers.sql` aliases `cached_tokens` as `cache_read_tokens`, which is what it is.

## Chosen test seam

Three levels, none new in kind:

- `store.bound` directly, in `tests/view/test_store.py`: a filled read equals the hand-spelled dict it replaces; a profile short of a parameter raises naming page, parameter and surface; a key that is also a width raises
- URLs through `build_app` over the fixture store, as every viewer test runs. `test_the_pages_run_at_the_production_sizes` keeps pinning each cited query to its surface — `ran["view_compactions"]["chip_chars"] == {bounds.NAV_TREE.chip_chars}` — and the literal numbers move onto the profiles. The popover's tests in `tests/view/pages/node/test_numbers.py` read the new field names. Slices 1–4 capture every fixture page's bytes with `tests/view/conftest.py:render_pages` before and after and require an empty diff; slice 5 changes the popover's bytes on purpose
- A source scan in `tests/view/test_layout.py`: no module of `view/` but `bounds.py` reads a width off `analyze.queries` — the deletion test as a leaf

## Slices

Each is one commit and passes `mise run check` alone.

1. **Profiles and the header.** `bounds.py` gains the ten classes and instances, `store.bound` lands with its tests, and the pane's header reads move onto `bounds.HEADER` and `bounds.EXPANSION`: `routes/pages.py`, `browse.py`, `expansions.py`, `details.py`; `header_bound` is deleted. Verified by the new tests, the unchanged production-sizes pin, and identical page bytes
2. **NavTree, popover, enrichment.** `nav_tree.py`, `failures.py`, `popovers.py`, `enrichment.py`, `detail.py` name their surfaces; `chip_chars` now reads off four profiles. Same verification
3. **Lists.** `sorted_sessions`, `list_bound`, the `SHOWN` composition and `DESCRIBED_BOUND` read `bounds.LIST`; the projects and records routes name theirs. `tests/view/test_bounds__lists.py` reads the profile
4. **Retire the constants.** The Python-side cuts (`text/cuts.py`, `nodes.py`), `budgets.py`, `tools/gen_bounds.py` and the tests read profiles; the viewer-only widths leave `analyze/queries.py`; the layout leaf lands. `mise run cogs` regenerates the bounds tables to the same numbers, which is the check that nothing moved
5. **Type the popover's read.** `reads.node_numbers`, `Numbers`, `charges` and `spend` over typed values, the SQL alias, the label, the stylesheet line. Independent of 1–4; ordered last so the byte captures above stay clean

## Decisions

- **One NamedTuple class per surface, fields named for the parameters.** Rejected a `Mapping[str, int]` per surface: the Python-side cuts would read `bounds.HEADER["head_chars"]`, unchecked by pyrefly. Rejected one class with every parameter: most fields would need a default, which is the thing S34 removed
- **The profile speaks the query's parameter names.** Rejected renaming parameters into one vocabulary (`head_chars` → `header_chars`): every `view_*.sql`, `hp query --list` and every saved citation would change for nothing a reader gains
- **`bound()` returns the mapping; `page_rows`, `window`, `Levels.rows` keep their signatures.** Rejected `page_rows(page, widths, **keys)` returning rows and bindings: `window` and `cursorless_rows` add their own composition parameters after the fact, and every `ran` list would change shape for the same guarantee
- **Completeness is checked against the manifest, in the store.** DuckDB refuses a missing or excess named parameter already (probed: `InvalidInputException` both ways), but its message names neither the surface nor the query; `bound` does, before a connection is touched
- **A shared read binds the wider surface.** Kept from `browse.py`'s runs read. Rejected an override key on `bound()`: two widths for one read is two surfaces, and the second belongs in `bounds.py`
- **The viewer-only widths leave `analyze/queries.py`; `LOG_CHARS` stays.** `LOG_CHARS_PARAM` is the analysis default of `session_timeline` and `run_timeline`, which `hp query` runs bare. Rejected moving it: `analyze/manifest.py` would import `view.bounds` for a default of its own
- **The charge lines take the registry's word and the popover lowercases every `dt` in CSS.** Its terms are lowercase today ("context used", "asked") and the registry's are not ("Cache read"). Rejected wording the other popover terms into `LABELS` too: "asked" and "returned" are deliberate popover words for columns the pane labels differently, and that is a second question
- **`cached_tokens` becomes `cache_read_tokens` in SQL.** Rejected mapping the alias in Python: that is the second vocabulary in another coat
- **Relation to `plans/deepen-viewer-reads/design.md`: compatible; land this first.** That plan moves query names and binding maps out of endpoints into read modules but leaves each read spelling its widths; its rule that a finished endpoint imports nothing from `hyphae.analyze.queries` is met for widths the moment they come off `bounds`. Landing first means it moves `bound(…)` calls instead of dicts. Slice 5 here does the row-typing its own slice 5 ("hide the raw row behind a dependency") would need anyway; its "do not change SQL" applies to its scope, not this one

## Out of scope

- C34's non-width constants (`FIRST_PAGE`, `UNATTRIBUTED`, `VIEW_PREFIX`) — sentinels, not sizes. C35 is already done in the tree: `view/manifest.py` declares one `SIZE`
- S9's remaining divergence: the list cites `limit` where the query binds `limit + PAGER_PROBE`, on purpose
- S35: `view_compactions` and `view_numbers_compaction` cut the trigger with a bare `substr`; the hand-cut list in `tests/analyze/test_queries.py` keeps them
- The footer keys citations by query name, so a page that ran one query twice cites the last run. Pre-existing; a profile doesn't change it
- Every other `Row` reader — `builders.py`, `nav_tree.py`, `browse.py` — is the deepen plan's

## Open questions

- The NavTree's bucket read (`nav_tree.py:_timeline`) binds the timeline's `log_chars` at 300, a width it never prints: the bucket row takes its title from the thread (`nodes.py:UNATTRIBUTED`) and reads only counts. I give `NavTree` a `log_chars` field at 110 so every NavTree read is one width; the cited number changes, no byte does. Naming `bounds.LOG` there instead keeps the citation and is a one-line swap. Nathaniel's call
- Whether the popover's lowercase is CSS's or Python's (`label(...).lower()` at the one `_charge` call). Both are one line; the rule in `.claude/rules/viewer-ui.md` is about SQL, not stylesheets

## Glossary changes

Add under "Viewer pages" in `CONTEXT.md`:

- **Surface** — one place a page prints store text at widths of its own: the NavTree, a header, a children log, an expansion, a popover, a list row; `view/bounds.py` declares each
- **Widths** — a surface's fixed sizes, one field per query parameter it binds; a read names the surface, the store fills the mapping, the footer quotes it
