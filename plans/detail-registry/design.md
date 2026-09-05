# Detail registry: declare each previewable value once

Sixteen values reach a reading pane as a **Detail**: a cut head on the node page and the whole a fetch away. Each is declared today in six places that agree only by string equality. This design puts each in one entry, `view/detail.py:DETAILS`, and has both the preview and the fetch read from it. Audit: S13 (`plans/refactor-audit-2026-08-30/findings.md:59`) landed the keyword-only `detail_of` and the `details()` collector in 0e0e03d; this is the other half. S2 (`:35`) is the sibling knob work and is untouched.

## Problem

For one value — take a tool call's result — the reader of `pages.py:355` sees `detail_of(name="result", head=row["result_head"], chars=row["result_chars"], url=f"/fragment/result{at}", syntax=highlight.by_suffix(row["result_type"]) or highlight.Syntax.JSON, markdown=False)`. The same value is declared again as the route `/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}` in `routes/details.py`, whose handler re-derives the syntax as `highlight.by_suffix(row.get("result_type")) or highlight.Syntax.JSON` (note `.get`) and re-binds `head_chars`; as the header SQL pair `result_head`/`result_chars`; as `store.Value.TOOL_RESULT`; and as the label key `result`. The fetch for `input` picks `by_suffix` while the preview hardcodes JSON. Nothing but tests keeps the six in step, and the tests keep them by hand: `tests/view/pages/node/test_node__details.py` lists `columns = {"input": ..., "result": ...}`, `tests/view/test_app.py` lists nine URL→citation pairs, `tests/view/test_app__headers.py` regexes source for `detail_of(\s*name=`. The rule "a rendered value goes through one function" (`.claude/rules/viewer-ui.md`) is met by the components and unmet one layer up.

Population (verified by `grep -n "detail_of(" src/hyphae/view/pages/node/routes/pages.py` → 10 and `grep -c "@router.get" .../routes/details.py .../routes/enrichment.py` → 11 + 6): turn `prompt`, `command_args`; run `brief`, `prompt`, `result`; call `text`, `thinking`; tool `command`, `input`, `result`; and `description`/`friction` at turn, run, and session. `/fragment/record` is the eleventh details.py route and is not a Detail: it serves a record row whole with no preview, and stays.

## Call paths

Current, per value:

```
pages.py:turn_page ─ detail_of(name, head, chars, url=f"…{at}", syntax, markdown) ─▶ Detail ─▶ Seen.details ─▶ parts.detail
routes/details.py:turn_prompt ─ prose(viewer, connection, Value.TURN_PROMPT, keyed, "value", "prompt") ─▶ values.prose
routes/enrichment.py:turn_description ─ enrichment_line(…, Value.TURN_SAID, keyed, "description") ─▶ values.enrichment_line
```

Proposed:

```
pages.py:turn_page ─ detail.preview(TURN_PROMPT, rows[0], size=knobs.detail, session_id=…, source=…, turn_id=…) ─▶ Detail ─▶ (unchanged)
routes/details.py:fetch(spec, request, viewer, connection) ─ fetched(connection, spec.whole, keyed, "value") ─▶ values.prose | values.code | values.enrichment_line
router.add_api_route(spec.route, fetch(spec)) for spec in DETAILS
```

## File-tree diff

```
src/hyphae/view/detail.py                          + Written, Spec, DETAILS and the sixteen entries; preview(); enrichment_lines() reads its two entries
src/hyphae/view/pages/node/routes/details.py       − ten handlers, prose(), code(); + fetch() and the registration loop; keeps record_value and fetched()
src/hyphae/view/pages/node/routes/enrichment.py    − deleted
src/hyphae/view/pages/node/routes/pages.py         ten detail_of(...) → preview(...)
src/hyphae/analyze/queries/view_call_header.sql    text_head → text
src/hyphae/analyze/queries/view_tool_header.sql    result_head → result
src/hyphae/analyze/queries/view_{turn,run,session}_said.sql   − ; + view_{turn,run,session}_{description,friction}.sql (six, each `AS value`)
src/hyphae/view/store.py                           Value: TURN_SAID, RUN_SAID, SESSION_SAID → six members
src/hyphae/view/manifest.py                        the same rename
tests/view/pages/node/test_node__details.py        the hand-listed columns dict → a loop over DETAILS
tests/view/test_app.py                             nine citation pairs → one parametrized leaf over DETAILS
tests/view/test_app__headers.py                    the detail_of regex → {spec.name for spec in DETAILS}
tests/analyze/test_queries.py                      the three view_*_said bindings → six
```

Not touched: `components/parts.py`, `markup/values.py`, `markup/page.py`, `browse.py:Seen`, `bounds.py`, `labels.py`, `tests/e2e/routes.json`. Public URLs and rendered bytes are unchanged.
*Amended at implementation:* `scenarios.py` came off that list. It gained `path_params()`, which reads one URL's keys back out of the route template that minted it, and `path_pattern()` under it, which matches every URL of one route shape. The registry sweeps read both; no scenario changed.

## Key contracts

```python
class Written(StrEnum):
    """How a Detail was written, which decides how both surfaces render it."""
    MARKDOWN = "markdown"    # a person or model wrote it: prose, .quoted
    BASH = "bash"            # fixed syntax
    JSON = "json"
    NAMED_FILE = "file"      # the suffix the row's result_type names, else JSON; binds head_chars
    LINE = "line"            # an enrichment line: a span, gated on enriched()

class Spec(NamedTuple):
    name: str        # the label key, the pane's data-detail, and the header column; f"{name}_chars" beside it
    route: str       # "/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}"; also the preview URL's template
    header: Page     # the query whose row previews it
    whole: Value     # the query that serves it, answering one column named value
    written: Written

DETAILS: tuple[Spec, ...]   # the sixteen; the only place a Detail is declared

def preview(spec: Spec, row: Mapping[str, object], *, size: int, **keys: str) -> Detail | None
def syntax_of(written: Written, row: Mapping[str, object]) -> Syntax | None   # the one syntax rule; KeyError if result_type is missing
```

`preview` replaces `detail_of`: it reads `row[spec.name]` and `row[f"{spec.name}_chars"]`, mints the URL as `spec.route.format(**keys)`, and builds the existing `Detail`, so components are untouched. Enrichment lines pass `about._asdict()` for `row`. The fetch is one closure per spec over one body, matching on `written` with `assert_never`; it reads `request.path_params` as `keyed`, adds `head_chars=queries.HEADER_CHARS` for `NAMED_FILE`, and 404s a `LINE` when `enriched(connection)` is false. Layering holds: `detail.py` (SHARED) imports `store` (BASE) and `text/highlight` (LEAF), never fastapi; `tests/view/test_layout.py` enforces it.

## Chosen test seam

URLs served by `build_app` over the fixture stores, as every viewer test does. The registry becomes the parametrization: for each `Spec`, the fixture scenario at `spec.header` returns `name` and `name_chars`; the app has a route at `spec.route`; the page's `data-detail` section and the fetched fragment agree on `data-value`, prose-vs-`<pre>` class, and `data-query`. This generalizes `test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url` from two hand-picked values to sixteen and replaces the citation list in `test_app.py`. No pinned byte moves: `parts.detail` and `values.*` are unchanged, and `tests/view/test_bounds__node.py` (`PRICED_ROWS["detail"]` against `budgets.MEASURED_DETAIL_MARKUP`) under `HYPHAE_PIN_EXACT=1` proves it. Fragment citations for enrichment lines change from `view_turn_said` to `view_turn_description`; fragments are held only to `PAGE_BYTES`.

## Slices

1. **Registry and preview** — `Written`, `Spec`, `DETAILS`, `preview`, `syntax_of`; the two header-column renames; `pages.py` calls `preview`; `detail_of` deleted; `test_app__headers.py` reads names from `DETAILS`. Verifiable: the existing details tests pass; the header test's `previewed` set is the registry's.
2. **One fetch** — `fetch(spec)` and the registration loop replace the ten handlers and `prose`/`code`; `test_app.py` citation leaf and `test_node__details.py` loop over `DETAILS`. Verifiable: `test_every_route_the_viewer_exposes_is_in_the_payload_sweep` still equates routes to `SCENARIOS`.
3. **Enrichment lines join** — six `view_*_{description,friction}.sql`, `Value` renamed, `routes/enrichment.py` deleted, `enrichment_lines()` reads its entries; `tests/analyze/test_queries.py` bindings follow. Verifiable: `tests/view/test_enrichment.py` over `ENRICHMENT_URLS` passes; `hp query --list` shows six.
4. **Docs** — `docs/viewer-bounds.md` and `docs/viewer.md` where they name `detail_of` or a `_said` query (`grep -rn "detail_of\|_said" docs`); `CONTEXT.md` per Glossary changes.

## Decisions

- **Routes are registered from the registry, not collapsed under a `{detail}` segment.** One handler body, sixteen `add_api_route` calls, public URLs unchanged. Rejected: `/fragment/{detail}/session/…`, which collides with `/fragment/numbers/session/{session_id}` and `/fragment/body/…/run/{run_id}` (Starlette resolves by order), drops a scenario per value from `SCENARIOS` and the gallery, and moves the unknown-name 404 into the handler.
- **`name` is the header column.** Two header columns rename (`text_head`, `result_head`). Rejected: a `column` field carried by sixteen entries for two exceptions.
  *Amended at implementation:* the rename reached every query that cuts a call's words, not the header alone. `view_turn_calls.sql` and `view_nav_tree_calls.sql` call theirs `text` too, and `thinking_head` renamed to `thinking` beside it — one name per value on every query that cuts it, whatever width it cut at. The design had them keeping `text_head` because they cut at other widths, which is a reason to keep two queries and not two names.
- **The said queries split into one per value.** `store.Value`'s docstring promises one whole value per query; `view_*_said` return two. Rejected: a `value_column` field on `Spec`, which would keep the irregularity and declare it in the registry.
- **Enrichment lines are entries.** They are already `Detail`s and their URLs are hand-minted in `enrichment_lines()`; a `LINE` arm costs one branch on a closed enum. Rejected: leaving them in `routes/enrichment.py` as a second, smaller copy of the problem.
- **The handler reads `request.path_params`.** Rejected: sixteen typed stubs each calling the shared body, which keeps FastAPI's signature per route but keeps sixteen declarations of the keys.
- **Relation to `plans/deepen-viewer-reads/`: compatible, land this first.** That plan's slice 5 asks that details and enrichment lines keep one dependency that hides the query, bindings, and raw row; `fetch(spec)` is that dependency, and `preview` is the typed read its slice 4 would move into `reads.py`. Its scope forbids SQL and URL changes and pins bytes before starting, so it should start after slices 1–3 land, not interleave.

## Out of scope

`/fragment/record` and `values.record` (no preview); the `Knobs`/`bounds` shape (S2); any change to `parts.detail`, `values.*`, or the 4,000-character cut; the deepen-viewer-reads endpoint adapters; the e2e tier (`[data-whole="input"]` still exists).

## Open questions

- `Written.LINE` folds "gate on `enriched()`" and "render as a span" into one member. If a third property diverges, `Written` splits into how-written and where-mounted; is that acceptable until then?
- Should `Spec.header` be checked at import (every `DETAILS` entry's header query names `name` and `name_chars`) or only by the test leaf? Import-time checks would need the query text, which `analyze/queries.load` gives cheaply.
- `hp query --list` loses `view_turn_said` and friends; is any report under `reports/` citing them (`grep -rln "_said" reports`)?

## Glossary changes

Add under Node-page anatomy, after **Detail**:

- **Detail spec** — the one declaration of a Detail: its name, fetch route, header and whole queries, and how it was written; the registry is `src/hyphae/view/detail.py:DETAILS`
