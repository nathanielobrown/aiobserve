# Testing plan: the detail registry

What the tests must show for `plans/detail-registry/design.md`: that sixteen Details declared once in
`view/detail.py:DETAILS` still preview, fetch, cite and 404 exactly as sixteen hand-written pairs did,
and that the registry — not a hand-kept list in a test — is what every sweep is parametrized over.

Nothing is mocked. The evidence is the recorded fixture corpus (`tests/conftest.py`, the store the
viewer tier opens through `build_app`) and the scenario corpus `tests/view/scenarios.py`, which already
pins one working URL for each of the seventeen `/fragment/…` routes. Invented data is flagged on the
leaf that uses it: the planted long values in `test_node__details.py`, and the four model-written
fields of `enriched_db` (`tests/conftest.py` says so itself — no fixture records a model's answer).

## Ordering: this lands on `sql-self-manifest`

Plan against the tree after `plans/sql-self-manifest/design.md`. Three consequences:

- **`src/hyphae/view/manifest.py` does not exist.** The design's file-tree row "the same rename" is
  void — there is nothing to rename. The six new `view_*_{description,friction}.sql` need no entry
  at all: their parameters (`session_id`, `source`, `turn_id`, `run_id`) are already in
  `queries.PARAM_TYPES`, and their scope derives from `relations`. Slice 3 shrinks accordingly
- **`tests/analyze/test_queries.py`'s two mirror leaves are already gone.** What this design touches
  there is `FIXTURE_BINDINGS` alone (lines 220–222 today), which the sibling leaves untouched
- **`tests/view/test_bounds.py` reads `manifest.catalog()`.** The leaf
  `test_every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value` is where the six new names
  must land in `store.Value`, and `test_the_listing_names_every_query_with_its_scope_and_what_it_needs_bound`
  (`tests/analyze/test_query.py:167`) counts `catalog()` rather than a pinned 66, so 66 → 69 re-pins
  nothing

Leaves marked **[after sql-self-manifest]** below cannot be written before it lands.

## Verification of the design's claims

Re-run against the working tree at `718a2b8`.

| Claim | Verdict |
| --- | --- |
| ten `detail_of(` in `routes/pages.py`; 11 + 6 `@router.get` in `details.py`/`enrichment.py` | verified |
| sixteen Details; `/fragment/record` is the eleventh route and not one | verified |
| `tests/view/test_app.py` lists nine URL→citation pairs | verified — but one is `/fragment/record`, so the list covers eight Details, not nine; the record pair must survive the parametrization |
| `test_node__details.py` hand-lists `columns = {"input": …, "result": …}` | understated: `test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url` lists five columns plus a brief appended below the loop |
| `test_app__headers.py` regexes source for `detail_of(\s*name=` | verified, line ~127 (`previewed`) |
| `scenarios.py` and `tests/e2e/routes.json` untouched | verified: all sixteen fragment routes plus `/fragment/record` are already scenarios, and `test_bounds.py:551` equates the app's route set to `set(SCENARIOS)` |
| `tests/view/test_layout.py` enforces `detail.py` at SHARED | verified (`LAYERED["detail"] = SHARED`) |
| `test_bounds__node.py` prices `PRICED_ROWS["detail"]` against `budgets.MEASURED_DETAIL_MARKUP` under `HYPHAE_PIN_EXACT=1` | verified |
| slice 4: docs name `detail_of` or a `_said` query | **false.** `grep -rn "detail_of\|_said" docs CONTEXT.md` → 0 matches. Slice 4 is the glossary entry and nothing else |
| open question: any report cites a `_said` query? | **no.** `grep -rln "_said" reports` → 0 matches. The rename breaks no committed citation |
| "Not touched: … `browse.py:Seen`"; "rendered bytes are unchanged" | **contradicted.** `src/hyphae/view/builders.py:214` reads `row.get("text_head")`, and `call_node` is fed a `view_call_header` row from `routes/browse.py:86` (`TITLED`) and `routes/expansions.py:89`. Renaming `text_head` → `text` in that header silently retitles every api call pane and expansion from `❝ <what it said>` to the model name. `builders.py` is missing from the file-tree diff |
| the log and NavTree keep `text_head` | verified against the working tree — but **amended at implementation**: they renamed too. `view_turn_calls.sql:29` and `view_nav_tree_calls.sql:15` now cut into `text` at their own widths, read by `reads.py:80` and `logs.py:224` |
| `result_head` has no reader but `pages.py:355` | verified |
| `routes/enrichment.py` is deleted with no other edit | incomplete: `routes/__init__.py:12` imports it and lists it in the extend loop |

Decisions taken as given, per the brief: `Written.LINE` stays one member; `Spec.header` is checked by
a test leaf, not at import.

## Obligations

- **Registry shape — static reads of `DETAILS` beside the app's route table and `SCENARIOS`; no store, no client** (in `tests/view/pages/node/test_node__details.py`, beside the leaves that already read details)
  - **Every spec's `route` is a route the app exposes and a scenario pins, and the sixteen cover every
    `/fragment/` route but `/fragment/record`.** *Evidence:* a new leaf asserting
    `{spec.route for spec in DETAILS} | {RECORD_ROUTE} == {path for path in SCENARIOS if path.startswith("/fragment/")} - {body, kin, numbers routes}`;
    bolded because this is what replaces every hand-kept list at once — `test_bounds.py:551`
    (`exposed == set(SCENARIOS)`) then proves the registration loop minted the same URLs, so no public
    URL can move without one of the two going red
  - No two specs share a `name` on the same route, and `name` is a key the label registry holds.
    *Evidence:* `tests/view/test_app__headers.py:test_every_fact_a_header_asks_for_has_a_label`,
    updated — `previewed` becomes `{spec.name for spec in DETAILS}` in place of the `detail_of` regex,
    and its "half this check has no subject" assertion keeps a registry that emptied itself from
    passing
  - `view/detail.py` imports no web framework and sits at SHARED. *Evidence:*
    `tests/view/test_layout.py`, unchanged — `LAYERED["detail"] = SHARED` plus the fresh-interpreter
    probe already fail if `fetch` drags fastapi down a layer
- **Header and whole queries — `tests/analyze/test_queries.py`, parametrized over the shipped files** — discovery over the query directory, so a spec pointing at a query that stopped answering fails here first
  - **Every spec's `header` query answers a column named `spec.name` and one named `spec.name_chars`,
    and its `whole` query answers exactly one column named `value`.** *Evidence:* a new leaf in
    `tests/analyze/test_queries.py` running each spec's two queries under the existing
    `FIXTURE_BINDINGS`/`VIEW_SIZES` machinery and comparing column names; bolded because it is the
    only thing standing where six places used to agree by string equality, and it is what the design
    chose over an import-time check. It is also what catches the `text_head` → `text` rename landing
    in one of the two queries and not the other
  - The six new `view_*_{description,friction}.sql` run against the fixture corpus. *Evidence:*
    `test_every_query_runs`, with `FIXTURE_BINDINGS` lines 220–222 (`view_turn_said`, `view_run_said`,
    `view_session_said`) becoming six entries over the same recorded ids — `SPINE`/`MAIN`/`SLASH_TURN`,
    `SPINE`/`SPINE_RUN`, `SPINE`
  - Each of the six is a declared per-value query that selects its fat column whole. *Evidence:*
    `tests/view/test_bounds.py:test_a_per_value_query_returns_the_one_value_it_is_named_for`,
    parametrized over `store.Value` — unchanged code, six new cases — and
    `test_every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value`, which fails if a new file
    ships under `view_` and lands in no enum. **[after sql-self-manifest]** for the `catalog()` read
  - `hp query --list` names sixty-nine queries, the three `_said` names gone. *Evidence:*
    `tests/analyze/test_query.py:test_the_listing_names_every_query_with_its_scope_and_what_it_needs_bound`,
    which counts `manifest.catalog()` rather than a literal, so it needs no re-pin; the answer to the
    design's third open question is recorded above — no report cites a `_said` query. **[after sql-self-manifest]**
- **Preview and fetch — `TestClient` over the fixture store and `enriched_client` over the described one, parametrized over `DETAILS`** — real recorded rows; the scenario URL is the per-spec datum, because every one of the seventeen already answers 200 under `test_bounds.py:test_no_route_serves_more_than_the_page_ceiling`
  - **A pane previews the value under `spec.name`, and the URL it minted answers with every character
    the store holds, filed under the same name.** *Evidence:*
    `test_node__details.py:test_every_value_a_pane_previews_is_fetchable_whole_from_its_own_url`,
    generalized — the five-entry `columns` dict and the appended brief become a loop over `DETAILS`,
    each spec's node URL derived from the keys its scenario URL carries and the length read from the
    store as it is today. Bolded: this is the leaf the design's whole seam rests on, and it grows from
    six values to sixteen, first covering a run's `prompt`/`result` and the six enrichment lines
  - **Every fragment cites the query and the keys it was fetched by.** *Evidence:*
    `tests/view/test_app.py:test_a_fragment_cites_the_query_that_fetched_it`, its nine hand-written
    pairs becoming a parametrization over `DETAILS` that builds the expected line from `spec.whole`
    and the scenario's own path params — plus the `/fragment/record` pair kept as it is, since the
    record route is not a Detail. Bolded because this is where an enrichment line's citation changes
    from `view_turn_said` to `view_turn_description`, and where a spec pointing at the wrong `whole`
    query is visible
  - `Written.NAMED_FILE` is the only arm that binds `head_chars`. *Evidence:* the same citation leaf —
    `view_tool_result.sql` cites `head_chars={queries.HEADER_CHARS}` and no other fragment cites it;
    the pinned string is what fails if the arm is bound for every spec
  - A value the store holds nothing under is a 404 rather than an empty block. *Evidence:*
    `tests/view/test_app.py:test_a_fragment_naming_nothing_is_a_404` and
    `test_node__details.py:test_a_run_nobody_asked_in_words_shows_no_ask_and_serves_none`, both
    unchanged — the second reads two recorded run shapes (a tool that takes no prompt, a replayed run
    with no spawning call) and asserts the pane shows no link beside the 404
  - **A `LINE` spec 404s where no pass has written, and answers where one has.** *Evidence:*
    `tests/view/test_enrichment.py` over `ENRICHMENT_URLS`, unchanged — it reads those six URLs off
    `SCENARIOS` rather than a list, and its three absence stores (no tables, `EMPTIED`, partly
    described) are what hold the `enriched(connection)` gate the design folds into `Written.LINE`.
    Bolded: the gate is one branch on a closed enum, and losing it turns a store no pass has touched
    into a 500
  - What a preview is marked up as is what its fetch is marked up as, per `Written`. *Evidence:* the
    four existing syntax leaves in `test_node__details.py`, unchanged — the `Bash` command as shell,
    a `Read` of `.md` as its source and of `.bin` as stored, an `Edit`'s sentence unmarked, and a
    result parsed as JSON or printed whole. Each already asserts the page and the fragment agree, so
    they are the red check on `syntax_of` collapsing `NAMED_FILE` into `JSON`. The planted commands
    and results are invented and say so on the leaf — redaction flattened every recorded one
  - Prose written as markdown renders as markdown on both surfaces. *Evidence:*
    `test_node__details.py:test_a_pane_reads_what_a_person_or_a_model_wrote_as_the_markdown_it_was_written_in`
    and `test_the_pane_walls_what_a_session_wrote_as_a_quote_and_leaves_a_payload_as_code`, unchanged
    — the second is a partition over three panes, so a `Written` arm that flipped one value from
    `MARKDOWN` to `JSON` lands there rather than passing by not being named
  - Text out of a transcript is escaped wherever a fragment prints it. *Evidence:*
    `tests/view/test_app__safety.py`, unchanged — it reads six of the sixteen fetch URLs directly
- **The two header renames — the same client tier, on the surfaces that read the renamed columns**
  - **An api call's pane and its expansion still head themselves with what the model said.**
    *Evidence:* `tests/view/pages/node/test_node__titles.py:394` — `fields(page, "data-body", "call")["title"] == cut(said, HEADER_CHARS)`
    against a recorded call that both spoke and ran tools. Bolded and listed first among the renames:
    `builders.call_node` reads `row.get("text_head")` off the `view_call_header` row, so `text_head` →
    `text` retitles the pane *silently* unless `builders.py:214` moves with the query, and the design
    omits that file. The same leaf pins the NavTree title at `NAV_CHARS`, which reads
    the column `view_nav_tree_calls` cuts — `text_head` at planning time, `text` as it landed
  - A children log and a NavTree row still cut a call's words at their own widths. *Evidence:*
    `tests/view/pages/node/test_node__logs.py` and `test_nav_tree__names.py`, unchanged — they read
    `view_turn_calls`/`view_nav_tree_calls`. *Amended at implementation:* the rename reached them
    as well, and these are the leaves that said so — they read the head under its new name, `text`
  - The tool pane still previews its result. *Evidence:* the round-trip leaf above, `result` case —
    `result_head` has exactly one reader (`pages.py:355`), which the rename to `result` moves
- **Bytes — `tests/view/test_bounds__node.py` under `HYPHAE_PIN_EXACT=1`** — the measurement is the pin
  - The markup around one previewed value costs what it cost before. *Evidence:*
    `PRICED_ROWS["detail"]` measured against `budgets.MEASURED_DETAIL_MARKUP` (550) and
    `PANE_DETAILS` (3); run under `HYPHAE_PIN_EXACT=1`, where a pin the page no longer reaches fails
    rather than passes. This is the design's claim that `parts.detail` and `values.*` are untouched
  - Every route still answers under the page ceiling. *Evidence:*
    `tests/view/test_bounds.py:test_no_route_serves_more_than_the_page_ceiling` over the described
    store, and `tests/view/test_bounds__values.py`, unchanged — fragments are held to `PAGE_BYTES`
    only, which is why the enrichment citations may change length
- **Gate — `mise run check`**
  - No surviving path names `routes/enrichment.py`. *Evidence:* `check-fast` reports a path that does
    not resolve; `src/hyphae/view/pages/node/routes/__init__.py:12` and its module docstring ("Five
    modules carry routes … an enrichment line") are what it catches
  - `CONTEXT.md` gains **Detail spec** under Node-page anatomy. *Evidence:* the glossary entry the
    design writes; nothing else in `docs/` names `detail_of` or a `_said` query, so slice 4 is that
    line alone
- **Mutation — `mise run mutate 'hyphae.view.detail.*'`, cold and serial**
  - The registry's own branches are claimed by a leaf. *Evidence:* survivors read through
    `uv run mutmut browse`; the two to check are `syntax_of`'s `NAMED_FILE` arm and `preview`'s
    `len(head) > size` cut arithmetic, each a single expression the URL sweeps could pass over. A
    survivor on the `Written` match is a claim no leaf makes about one of the sixteen

## Not covered, and why

- **Which of the sixteen the gallery shows and how it looks.** The browser tier drives
  `tests/e2e/routes.json`, which is generated from `SCENARIOS` (`tests/tools/test_gen_e2e_routes.py`)
  and does not move: the routes are unchanged by decision. `[data-whole="input"]` still exists, per
  the design's out-of-scope list
- **Citation binding order inside one fragment.** Inherited from `sql-self-manifest`, which changes
  the order of `k=v` pairs; the citation leaf above compares whole strings per spec, so it re-pins
  whatever that refactor left. Nothing here adds an ordering claim
- **That `DETAILS` is exactly sixteen.** A count assertion pins the population, not the contract; the
  route/scenario equality leaf already forbids both a missing entry and an extra one, and it says
  which
- **The 4,000-character cut and the `Knobs` shape.** Out of scope by the design (S2), and pinned where
  they are today by `bounds.DETAIL.ceiling` in `test_node__details.py` and `test_bounds__node.py`

## Unreachable through the design's seam

One, and it is small. **`syntax_of` raising `KeyError` when the row has no `result_type`** cannot be
reached from a URL: both surfaces of the only `NAMED_FILE` spec select `result_type`
(`view_tool_header.sql:60`, `view_tool_result.sql`), so no request can produce a row without it. The
obligation is real — it is the design's stated fail-fast contract, and the current fetch's `row.get`
is exactly the silent arm the rename removes — so it is discharged one level down, by a direct call
on `detail.syntax_of` with a row lacking the key, in `test_node__details.py`. Flagged rather than
dropped: it is the one leaf in this plan that does not go through `build_app`.

The design contradiction found above — `builders.py:214` reading `text_head` off the renamed header —
is reachable and covered (`test_node__titles.py:394`), but the implementer must add `builders.py` to
the file-tree diff or the rename ships a retitled api call pane.
