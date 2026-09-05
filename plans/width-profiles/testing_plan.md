# Testing plan: name a surface's widths once

What the tests must show for `plans/width-profiles/design.md`: that a read binding
`bounds.<SURFACE>` runs at the same numbers, cites the same line and renders the same bytes as
the hand-spelled dict it replaces; that a profile short of a parameter crashes before a
connection is opened; and that no width can be read off `analyze/queries.py` again once it has
moved. Under slice 5, that the popover's three charge lines come off the label registry and a
typed read rather than off `Row` and a private word table.

Nothing is mocked. The evidence is the recorded fixture corpus (`tests/conftest.py:corpus_db`,
`enriched_db`) served through `build_app`, the scenario corpus `tests/view/scenarios.py`, and the
shipped query files. The only invented data is the planted `.sql` and planted profile in the
`store.bound` leaves, flagged where used.

## Ordering: this lands on `sql-self-manifest` and `detail-registry`

Plan against the tree after both. Consequences, in the order they bite:

- **`src/hyphae/view/manifest.py` does not exist.** The design's Problem paragraph ("every
  `view_*` parameter is required (`view/manifest.py`)") and its file-tree doc row
  ("`view/manifest.py` … docstrings") are **void**. So is the parenthetical in
  `tests/view/test_bounds.py:287`, which the sibling already has to rewrite
- **`bound()` cannot read `analyze.manifest.QUERIES[page].params`.** `QUERIES` is gone; the
  declared parameters come from `manifest.describe(page).params` (or `catalog()[page]`).
  `describe` reads the statement on demand, which is what makes the planted-query leaf in
  `tests/view/test_store.py` reachable
- **`tests/view/test_bounds.py:test_no_viewer_query_declares_a_default` is reshaped or gone.**
  It iterates `QUERIES` today. Whatever the sibling leaves, this design must not lean on it: the
  obligation "a width is the surface's, not the query's" is carried here by the profiles
- **`tests/view/test_app.py:test_a_fragment_cites_the_query_that_fetched_it`** is already
  reparametrized over `DETAILS` by `detail-registry`, and its `head_chars={queries.HEADER_CHARS}`
  pin becomes `bounds.HEADER.head_chars` here. Two refactors edit one assertion; this one edits
  it second
- **`routes/enrichment.py` is deleted and `routes/details.py` keeps one `fetch()`.** The
  design's file-tree row naming `detail.py` beside `nav_tree.py` resolves to the single
  `head_chars` binding inside `fetch`'s `NAMED_FILE` arm, not to ten handlers. `view/detail.py`
  (the registry) binds no width and is not touched
- `tests/analyze/test_queries.py` is touched by both siblings and **not** by this design: no
  query file's text changes here except `view_numbers.sql`'s alias in slice 5

Leaves below carry **[after sql-self-manifest]** or **[after detail-registry]** where they
cannot be written first.

## Verification of the design's claims

Re-run against the working tree at `718a2b8`.

| Claim | Verdict |
| --- | --- |
| the grep finds ~50 lines in twelve modules | verified: 51 lines, 12 modules |
| `chip_chars` bound at four values in four modules (110/300/100/60) | **understated**: four *values*, five *sites* — `routes/expansions.py:287` binds `chip_chars=queries.NAV_CHARS` for the runs read behind an expansion, and the design's `EXPANSION` profile (head_chars, detail_chars) has no field for it |
| `test_the_pages_run_at_the_production_sizes` is ~20 hand assertions; a fragment carries none | verified (25 `assert` statements); verified — `CITING` excludes `/fragment/` by construction (`tests/view/pages/query/test_query.py:36`) |
| `store.header_bound`, `store.list_bound`, `store.DESCRIBED_BOUND` are the three local fixes | verified |
| `nav_tree.py:_timeline` binds `log_chars` at 300 | verified (`nav_tree.py:189`) |
| the citation must come from the same mapping the query bound | verified (`view/citation.py` reads the bound dict; `tests/view/test_app.py:50` pins whole citation strings) |
| `tests/view/test_store.py` is new | verified: no such file |
| `test_app__headers.py` closes `LABELS` over literal `label(` calls | verified — the regex is `(?:fact|label)(?:led)?\(\s*(?:name=)?["']([a-z_]+)` over every `.py` under the view package, and the assertion is set **equality**, so `new_input_tokens` must be written literally or the entry reds |
| `labels.py` lacks `new_input_tokens` | verified; `cache_read_tokens` ("Cache read") and `output_tokens` ("Output tokens") are already there |
| `view_numbers.sql` aliases `cache_read_tokens AS cached_tokens` | verified (`:62`, read back at `:146`) |
| `numbers.charges` reads `row[f"{field}_tokens"]` off `_LINES` | verified (`numbers.py:75`) |
| `PROJECTS`, `ERRORS`, `RECORDS`, `LOG` are free names in `bounds.py` | **false.** All four are existing `Bound` constants in that module, cited by name in `tools/gen_bounds.py` (`bounds.PROJECTS.default`, `bounds.ERRORS.default`, `bounds.RECORDS.default`) and printed in `docs/viewer-bounds.md`. Four of the ten profile names collide. See the report at the foot |
| `tools/gen_bounds.py` and `tests/view/budgets.py` read the profiles | reachable, but the ratchet does not follow: `gen_bounds.declared()` filters `isinstance(value, bounds.Bound \| int)`, so ten `NamedTuple` instances are invisible to `test_every_bound_is_cited_by_a_table_or_named_as_uncited`, and `bounds.LOG_CHARS` drops out of it when it becomes a profile field |
| slice 1–4 verified by "identical page bytes" via `render_pages` | partly: `render_pages` sweeps **pages** only (`conftest.py:70` `pages(connection)`), so the popover, the details fragments, the enrichment block and the expansions — four of the surfaces being moved — are outside that capture |
| the e2e tier reads no charge field | verified: `tests/e2e/specs/htmx.spec.ts` touches `.popover` placement and visibility, never a charge's `data-field` |

Decisions taken as given, per the brief: `NavTree` gets a `log_chars` field at 110 (the cited
number moves 300 → 110 for `session_timeline` and `run_timeline` as the NavTree reads them, and
no byte moves); the popover's lowercase is CSS's.

## Obligations

- **`store.bound` directly — `tests/view/test_store.py` (new), no client, no store connection**;
  the shipped query files are the world, read through `manifest.describe`
  - **A filled read equals the dict it replaces.** *Evidence:* a new leaf asserting
    `store.bound(Page.TURN_HEADER, bounds.HEADER, session_id=SPINE, source=MAIN, turn_id=SLASH_TURN, detail_chars=bounds.DETAIL.default)`
    equals the literal mapping `routes/pages.py:turn_page` spells today, key for key — the
    equality is what makes the citation under the page unchanged. Bolded: every other leaf in
    this plan is downstream of this one. **[after sql-self-manifest]** for the `describe` read
  - **A parameter neither the keys nor the profile carries raises, naming the page, the
    parameter and the surface.** *Evidence:* the same file, calling `bound(Page.SESSION_HEADER,
    bounds.POPOVER, session_id=SPINE)`; `pytest.raises` matched against all three words. Bolded:
    this is the whole reason `bound` exists rather than `**profile._asdict()`, and the design's
    own decision says DuckDB's own refusal names neither
  - A key that is also a profile field raises rather than overriding it. *Evidence:* the same
    file — `bound(Page.TURN_HEADER, bounds.HEADER, head_chars=10, …)` raises naming `head_chars`;
    the message is what sends a reader to declare a second surface
  - An excess key the statement does not bind raises before a connection is touched. *Evidence:*
    a planted one-parameter `.sql` under a `monkeypatch`ed `queries.QUERY_DIR` plus a planted
    single-field profile (invented — the point is a shape no shipped query has), asserting the
    raise happens with no `duckdb` connection in the test at all
- **Widths as declarations — source scans, no store** (`tests/view/test_layout.py`, beside the
  import-graph leaves it already holds)
  - **No module of `view/` but `bounds.py` reads a width off `analyze.queries`.** *Evidence:* a
    new leaf globbing the view package for `queries.<NAME>` where `<NAME>` is one of the moved
    constants, asserting the only hit is `bounds.py`; the same scan must still allow
    `queries.load`, `queries.citation`, `queries.ParamType`, `queries.FIRST_PAGE` and
    `queries.LOG_CHARS`, which stay (`store.py:335`, `records/routes.py:32`). Bolded: this is the
    design's own deletion test, and it is the only thing that stops slice 4 from leaving half the
    widths behind
  - `text/` still reaches nothing above itself but `bounds`. *Evidence:*
    `test_text_reaches_nothing_in_the_viewer_but_itself_and_the_sizes_it_cuts_to`, unchanged in
    code — `text/cuts.py` swapping `queries.LOG_CHARS` for `bounds.LOG.log_chars` keeps the one
    permitted import and drops an `analyze` one, so this leaf goes greener, not redder. Its
    docstring's "`bounds` is the one exception" becomes literally true
- **Pages through `build_app` over the fixture corpus — `tests/view/test_bounds.py` and
  `tests/view/test_app.py`, a `TestClient` reading the footers the pages printed**
  - **Every page still runs at the production sizes, and each pinned number is read off a
    profile.** *Evidence:* `tests/view/test_bounds.py:test_the_pages_run_at_the_production_sizes`,
    updated in place — the 25 assertions keep their subjects and their comments, and each
    literal becomes the profile field the surface binds
    (`ran["view_compactions"]["chip_chars"] == {bounds.NAV_TREE.chip_chars}`,
    `ran["view_runs"]["chip_chars"] == {bounds.LOG.chip_chars}`,
    `ran[header]["head_chars"] == {bounds.HEADER.head_chars}`). Bolded: it is the one leaf that
    reads what production bound rather than what a module declares, so it is where a swapped
    profile surfaces. Keep at least the three anchors as literals — 100, 110, 300 — or the leaf
    compares the profile with itself and pins nothing
  - The NavTree's timeline read cites 110 where it cited 300. *Evidence:* the same leaf, plus
    `tests/view/test_app.py:test_a_node_page_cites_every_query_it_ran`, whose `session_timeline`
    line moves from `log_chars={queries.LOG_CHARS}` to `log_chars={bounds.NAV_TREE.log_chars}`.
    This is the one deliberate citation change in slices 1–4, and it is the evidence for the
    open question the brief settled
  - A citation still quotes every binding its query takes, and every citation link answers.
    *Evidence:* `tests/view/pages/query/test_query.py:test_a_citation_quotes_every_binding_its_query_takes`
    and `test_every_citation_a_page_carries_links_to_the_query_it_names`, both unchanged and both
    parametrized over `CITING` — a profile that dropped a field fails here as a missing binding
    before anything renders
  - **No page's bytes move across slices 1–4.** *Evidence:* `tests/view/conftest.py:render_pages`
    over `corpus_db` captured before the branch and after each slice, compared as an exact map;
    the durable form is `test_a_node_page_at_the_sizes_a_reader_gets_costs_what_the_ceiling_budgets`
    and its neighbours in `tests/view/test_bounds__node.py` under `HYPHAE_PIN_EXACT=1`, which red
    on a page that grew or shrank. Bolded, with the caveat that the before/after diff is a
    one-time implementer procedure and not a committed leaf — what is committed is the exact pin
  - The session header read is bound identically for the node page and the errors page.
    *Evidence:* `header_bound`'s two callers (`routes/browse.py`, `routes/expansions.py`,
    `pages/errors/routes.py`) go through `bound(Page.SESSION_HEADER, bounds.HEADER, …)`, and
    `tests/view/test_app.py:test_a_node_page_cites_every_query_it_ran` pins the session-header
    citation string whole (`head_chars=100 item_chars=60 head_items=5`), with the errors page's
    404 wording held by its own leaf in `tests/view/test_bounds__lists.py`
- **Fragments — the same client tier, on the four surfaces the footer sweep cannot see**
  (a fragment carries no citation footer, so `ran_at` never reaches it)
  - **Every fragment still cites the query and the widths it was fetched by.** *Evidence:*
    `tests/view/test_app.py:test_a_fragment_cites_the_query_that_fetched_it` — after
    `detail-registry` it is parametrized over `DETAILS`, and the one width in it
    (`head_chars={queries.HEADER_CHARS}` on `view_tool_result.sql`) becomes
    `bounds.HEADER.head_chars`. Bolded and named here because this is the only committed evidence
    for the `NAMED_FILE` fetch's width; `render_pages` does not sweep fragments.
    **[after detail-registry]**
  - A compaction popover still cuts its trigger at the popover's width. *Evidence:*
    `tests/view/pages/node/test_numbers__compaction.py:66`, whose pinned citation
    `chip_chars=60` becomes `chip_chars={bounds.POPOVER.chip_chars}` — the fourth `chip_chars`
    value, and the only one no page footer quotes
  - The enrichment block still binds its three widths. *Evidence:*
    `tests/view/test_enrichment.py`, which reads `queries.NAV_CHARS` today and reads
    `bounds.ENRICHMENT`/`bounds.NAV_TREE` after; the block is a fragment, so its widths are held
    only here and by the tuple assertion at the foot of `test_the_pages_run_at_the_production_sizes`
    (`(queries.ENRICHMENT_CHARS, queries.TAG_CHARS) == (200, 20)`), which moves to the profile
  - An expansion's runs read binds a width the design gives no profile. *Evidence:*
    `routes/expansions.py:287` (`chip_chars=queries.NAV_CHARS`) must name a surface, and
    `tests/view/test_bounds__node.py:test_an_expansion_weighs_a_body_and_the_one_page_of_rows_it_lists`
    is what prices the result. Listed as an obligation because the design's `EXPANSION` profile
    has no `chip_chars` field — see the report
- **Lists, projects, records and errors — `tests/view/test_bounds__lists.py` and
  `tests/view/test_app__list.py`, real pages over the fixture corpus**
  - A session list row, a described row and the filter suggestions all cut at one profile.
    *Evidence:* `tests/view/test_bounds__lists.py:test_a_session_list_of_nothing_but_escapes_costs_what_the_ceiling_budgets`,
    updated to read `bounds.LIST` where it reads `queries.LIST_CHARS`, `LIST_ITEMS` and
    `TAG_CHARS` today; `list_bound`, `DESCRIBED_BOUND` and the `SHOWN` composition are the three
    readers it covers at once, and the page's citation is what proves the composition and the
    query agree
  - The projects page and the errors page keep their two non-width sizes apart from their widths.
    *Evidence:* `tests/view/pages/projects/test_projects.py` (which pins `recent_days`/`window_days` against what
    the page cites) and
    `test_an_errors_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets`; `NAV_CHARS`
    stays a module constant that `bounds.NAV_TREE` and `bounds.ERRORS` both read, so the errors
    row and the NavTree row cannot drift apart
  - The records browser still previews at 160. *Evidence:*
    `ran["view_records"]["preview_chars"] == {bounds.RECORDS_SURFACE.preview_chars}` in the
    production-sizes leaf (name pending the collision below)
- **The generated bounds tables — `tests/tools/test_gen_bounds.py` and `mise run cogs`**
  - **Every number the four tables print is still the constant cited beside it.** *Evidence:*
    `test_every_number_a_row_prints_is_the_constant_it_cites_in_that_position`, unchanged in
    code — the `cites` tuples move from `queries.LIST_CHARS` to `bounds.LIST.head_chars`, and
    `gen_bounds.valued` already walks a dotted name, so a two-segment cite resolves. Bolded:
    this is the leaf that makes `mise run cogs` regenerating the same numbers a check rather
    than an eyeball
  - **A width declared in `bounds.py` is cited by a table or named as uncited.** *Evidence:*
    `test_every_bound_is_cited_by_a_table_or_named_as_uncited` and
    `test_nothing_is_named_uncited_that_bounds_no_longer_declares`, with `gen_bounds.declared()`
    widened to walk each profile instance's fields — as written it filters `Bound | int` and
    would let all ten profiles through in silence. Bolded: the ratchet is the only thing that
    notices a width nobody documents, and this refactor is exactly the change that would slip
    past it
  - `docs/viewer-bounds.md` still resolves every path and symbol it names. *Evidence:*
    `mise run check-fast`, which reports a link or path that does not resolve, over the doc rows
    that name `analyze/queries.py` as the declaring module today
- **The popover's typed read (slice 5) — `TestClient` over the fixture corpus, the three popover
  scenarios in `tests/view/scenarios.py` as the data**
  - **The three charge lines print the counts and dollars they printed before, under new field
    names.** *Evidence:*
    `tests/view/pages/node/test_numbers.py:test_the_popovers_two_columns_come_to_the_totals_under_them`
    and `test_a_call_says_the_cache_it_read_apart_from_the_context_it_sent`, updated —
    `CHARGES = ("cost_cached", "cost_new_input", "cost_output")` becomes the design's
    `cache_read_usd`, `new_input_usd`, `output_usd`, and the token `data-field`s become
    `cache_read_tokens`, `new_input_tokens`, `output_tokens`. The arithmetic assertions (the
    three counts summing to `fill`, the three dollars to `cost_usd`) are unchanged and are what
    prove the rename moved names and not numbers. Bolded: it is the one slice that changes bytes
    on purpose, so nothing else can tell a rename from a re-wiring
  - **`LABELS` stays closed, with `new_input_tokens` asked for by name.** *Evidence:*
    `tests/view/test_app__headers.py:test_every_fact_a_header_asks_for_has_a_label`, unchanged in
    code — its `asked` regex scans the whole view package for `label("…")`, and its assertion is
    set equality, so the new entry passes only if `charges` writes the three calls literally and
    fails if it builds them from a table. Bolded: this is precisely the seam the design leans on,
    and it is load-bearing in both directions
  - No `Row` reaches `numbers.py` or the popover route. *Evidence:*
    `reads.node_numbers` returns `Numbers` and `charges(read: Numbers, …)` takes it, checked by
    `mise run check`'s pyrefly pass — a `row["…"]` left behind in `numbers.py` is a type error
    on a `NamedTuple`, not a runtime surprise. The `store.Row` import leaving
    `pages/node/numbers.py` is the visible artifact
  - `view_numbers.sql`'s renamed column still answers every reader. *Evidence:*
    `tests/analyze/test_queries.py:test_every_query_runs` over the fixture corpus (the alias is
    at `:62`, read back at `:146`), plus the popover leaves above; nothing outside
    `reads.node_numbers` reads `cached_tokens` — grep confirms `numbers.py` and the tests are the
    only readers
  - The popover's terms read in lowercase beside the registry's capitals. *Evidence:* a `.popover
    dt` assertion through the `viewer_css` fixture, as
    `test_a_popover_is_hidden_until_its_row_is_pointed_at_or_tabbed_into` already asserts CSS
    declarations by regex. This is a rendering claim a Python test can only make about the rule,
    not about the pixels — see **not covered**
  - The stylesheet still paints only fields the markup carries. *Evidence:*
    `tests/view/test_app.py:257` (`painted <= page data-fields`), unchanged — the popover's
    `.sum [data-field="cost_usd"]` selector must keep naming the store's column and not one of
    the three new dollar names
- **Gate — `mise run check`**
  - No surviving path names a moved constant or a deleted helper. *Evidence:* `check-fast`
    reports a link or path that does not resolve; the docstrings in `view/bounds.py`,
    `analyze/queries.py`, `docs/viewer-bounds.md` and `.claude/rules/viewer-ui.md` are what it
    catches, and `store.header_bound`/`DESCRIBED_BOUND` disappearing is what makes it look
  - `CONTEXT.md` gains **Surface** and **Widths** under "Viewer pages". *Evidence:* the two
    glossary lines the design writes, and `mise run cogs` leaving the Layout tree unchanged
- **Mutation — `mise run mutate 'hyphae.view.store.*'`, cold and serial**
  - `bound()`'s own branches are claimed by a leaf. *Evidence:* survivors read through
    `uv run mutmut browse`; the two to check are the "key wins over field" arm and the
    "neither has it" raise, each a single expression the URL sweeps pass over by never being
    short a parameter. A survivor on either is a claim only `tests/view/test_store.py` can make

## Not covered, and why

- **That the popover's terms *render* lowercase.** CSS `text-transform` is a paint, not a byte:
  the served markup carries "Cache read" whatever the rule says. The Python leaf holds the rule's
  presence; seeing it is a witnessed browser check against `mise run gallery` (never 8477), of the
  kind `.claude/rules/viewer-ui.md` already prescribes for colour. Adding a computed-style
  assertion to the browser tier for one `text-transform` would buy a Chromium run per edit
- **That a profile's number is *right* for its surface.** No test can say 110 suits a NavTree row;
  what the suite holds is that the number a surface runs is the number its profile declares and
  that the page fits under its ceiling at it (`tests/view/test_bounds__node.py`). The judgment
  stays in the comments the constants carry with them out of `analyze/queries.py`
- **The exact byte diff across slices 1–4 as a committed artifact.** It is a before/after capture,
  which has no stable subject once the branch lands; the durable form is the exact pins under
  `HYPHAE_PIN_EXACT=1`
- **`LOG_CHARS_PARAM` as an analysis default.** It stays in `analyze/manifest.py`'s `DEFAULTS`
  after the sibling, and `tests/analyze/test_query.py:test_the_production_defaults_run_unless_a_param_overrides_one`
  already covers it. Nothing here touches the bare `hp query` path
- **Which surface a shared read should have named.** `browse.py` binding `bounds.LOG` for a read
  two surfaces print is a decision, and the citation leaf pins the consequence; a test that
  preferred the narrower width would be the same decision written twice

## Unreachable through the design's seam, and design findings

No obligation is unreachable, but four claims need the implementer's hand before the leaves
above can be written as stated.

1. **Four profile names collide with existing constants.** `bounds.py` already declares `LOG`,
   `RECORDS`, `PROJECTS` and `ERRORS` as `Bound`s, and `tools/gen_bounds.py` cites three of them
   by name (`bounds.PROJECTS.default`, `bounds.ERRORS.default`, `bounds.RECORDS.default`), which
   `docs/viewer-bounds.md` prints. The design's set of ten reuses all four names. Verified by
   reading `src/hyphae/view/bounds.py` and `tools/gen_bounds.py` at `718a2b8`. One side must
   rename; the production-sizes and gen_bounds leaves above are written against whichever it is
2. **The gen_bounds ratchet does not see a profile.** `declared()` keeps values that are
   `Bound | int`; a `NamedTuple` instance is neither, so ten surfaces' widths would enter
   `bounds.py` outside the cited-or-excused rule, and `LOG_CHARS` would leave it. Widening
   `declared()` is an obligation above, not an option
3. **A fifth `chip_chars` site has no surface.** `routes/expansions.py:287` binds it at
   `NAV_CHARS` for the runs read behind an expansion, and the design's `EXPANSION` profile
   declares only `head_chars` and `detail_chars`. Either `EXPANSION` gains the field or that
   read names `bounds.NAV_TREE`; the citation it prints is unpinned today, so the choice is
   invisible until the production-sizes leaf is extended to it
4. **`bound()`'s manifest read is void as written** (`analyze.manifest.QUERIES[page].params`),
   per the ordering section. `describe(page).params` is the replacement, and it is what makes the
   planted-query leaf reachable

One consequence worth stating before slice 5 lands: `label("output_tokens")` is **"Output
tokens"**, so the popover's third term reads "output tokens" where it reads "output" today. That
is a wording change the design does not name, and the `LABELS` closure test will not object to
it — only a reader will.

Thirty obligations, across nine levels.
