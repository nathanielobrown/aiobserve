# Testing plan: the statement is the query's declaration

What the tests must show for `plans/sql-self-manifest/design.md`: that a `Query` derived from a `.sql` file binds, scopes and lists the same way the hand-written entry did, and that the two facts Python still holds — a parameter's type and a production default — fail loudly when a statement and the table disagree.

Nothing here is mocked. The evidence is the shipped query directory and the recorded fixture corpus `tests/analyze/conftest.py:corpus_db` extracts from `tests/fixtures/`; the only invented data is the planted `.sql` files, which are flagged on their leaves.

## Verification of the design's claims

Re-run against the working tree at `718a2b8`, since the design was written against 2026-09-03:

| Claim | Verdict |
| --- | --- |
| 66 files, 28 analysis (22 corpus, 6 keyed), 38 viewer | verified |
| scope by `relations` ∩ `CORPUS_RELATIONS` matches the declared scope | verified, 66 of 66 |
| the declared parameter set equals the statement's `$names` | verified, 66 of 66 |
| 69 parameter names, none typed two ways | verified |
| `DEFAULTS` is 17 queries, 43 values; 43 comment lines in `view/manifest.py` | verified |
| 6 of 56 parameterized files cast a parameter with `::` | verified |
| "binding 134 parameters between them" | 134 is the viewer's half; the library binds 189 parameter entries over 69 names |
| `--list` re-pins `view_runs` to `chip_chars session_id` | verified, and `records_slice`'s pin is unchanged — its required names hold statement order already |
| the file-tree diff's consumer list | incomplete: `tests/view/pages/query/test_query.py` (lines 146, 237), `tests/view/test_app__list.py` (273, 292) and `tests/analyze/test_select.py` (18) also read `manifest.QUERIES` |

The sibling refactors do not collide. `one-price-table`, `enrichment-stamp`, `trace-replay-rule` and `otlp-census-drive` touch `enrich/`, `extract/pricing.py`, `export/` and `model.py`; none names `analyze/manifest.py`, `analyze/queries.py`, `analyze/runner.py`, `view/manifest.py` or the three test files this plan reshapes. Two shared files need a rebase eye rather than a test: `src/hyphae/cli.py` (three refactors edit different functions; this one edits `_query_listing` and its import) and `CONTEXT.md` (`trace-replay-rule` also adds under **Pipeline**).

## Obligations

- **CLI tier — `hp query` through `tests/analyze/conftest.py:QueryRunner`** — real `cli.main`, real DuckDB over the extracted fixture corpus, real query files on disk; the two streams read apart
  - A bare run binds the production defaults `DEFAULTS` holds, and one `--param` moves that binding and no other. *Evidence:* `tests/analyze/test_query.py:test_the_production_defaults_run_unless_a_param_overrides_one`, unchanged — `records_slice`'s `max_chars` still cites `queries.RAW_CHARS` and the overridden run returns a 50-character value
  - `--list` prints one line per shipped file, its derived scope, and its required parameters in statement order. *Evidence:* `tests/analyze/test_query.py:test_the_listing_names_every_query_with_its_scope_and_what_it_needs_bound`, updated — the count reads `manifest.catalog()`, `agent_types` still prints `corpus`, `view_runs` re-pins to `["keyed", "chip_chars", "session_id"]`, and `records_slice`'s existing four-name pin stands unchanged (its statement order already matches)
  - **A `$parameter` no `PARAM_TYPES` types is refused at `describe`, naming the parameter.** *Evidence:* a new leaf in `tests/analyze/test_query.py`, planting `SELECT $undeclared` as a `.sql` under a `monkeypatch`ed `QUERY_DIR` (invented statement — the point is a shape no shipped file has); `pytest.raises(SystemExit, match="undeclared")`. This is the leaf that replaces `test_every_query_file_has_a_manifest_entry`: it is the only thing left standing between a new query and a silent NULL binding
  - **A `DEFAULTS` key the statement does not bind is refused, naming the key.** *Evidence:* a new leaf beside it — a planted one-parameter file with a `monkeypatch.setitem` on `manifest.DEFAULTS` adding a second key; the message names the orphan. Bolded because it is the only guard on the half that stays in Python, and an orphan default is otherwise invisible
  - A `ParamType` the binder has no arm for is refused rather than bound to SQL NULL. *Evidence:* `tests/analyze/test_query.py:test_a_parameter_type_nothing_binds_is_refused_rather_than_bound_to_null`, reshaped — the plant becomes a `.sql` file plus a `monkeypatch.setitem` on `queries.PARAM_TYPES` (`"flag": cast(ParamType, "boolean")`) in place of the `manifest.QUERIES` entry; the refusal still matches `boolean`
  - A DDL statement cannot write to the store. *Evidence:* `tests/analyze/test_query.py:test_the_store_is_opened_read_only`, reshaped — the planted `CREATE TABLE` file is now its own declaration, so the `manifest.QUERIES` `setitem` goes and `_tables(corpus_db)` still compares equal
  - An unknown query name is refused with the library's names in the message. *Evidence:* `tests/analyze/test_query.py:test_an_unknown_query_or_parameter_names_what_it_did_not_recognize`, unchanged in assertion; the "Known queries" list now comes from `names()`
  - A statement that reads `project_sessions` needs `--project`, and one that does not refuses it. *Evidence:* a new leaf in `tests/analyze/test_query.py` — `run_query("agent_types")` with no `--project` and `run_query("session_overview", "--project", MYCELIA, ...)` both raise `SystemExit` naming the query. No leaf pins either refusal today, so the design's slice 1 ("the runner's `--project` refusals") has nothing to lean on until this lands
  - A citation of a run with several bindings is still paste-back-runnable. *Evidence:* `tests/analyze/test_query.py:test_the_citation_names_the_query_file_and_every_resolved_binding`, unchanged (`sessions` binds only the runner's four), plus `_bindings`, which compares as a dict. Deliberate: derivation changes the *order* of the bindings in 33 citations, and no leaf pins order for a query with parameters of its own — see **not covered**
- **Smoke tier — `tests/analyze/test_queries.py`, parametrized over `queries/*.sql`** — discovery, not enumeration, so a query added by any consumer is covered the day it lands
  - Every shipped file describes and runs. *Evidence:* `test_every_query_runs`, updated to read `manifest.describe(name)` in place of `QUERIES[name]`; still asserts more than the header row for all 66, with `FIXTURE_BINDINGS` and `VIEW_SIZES` untouched. This is where `describe()` is exercised over the whole library, and where a new file with an untyped parameter fails first
  - A corpus query reads the `corpus_*` views and one of the runner's relations, never a `live_*` view or a base table. *Evidence:* `test_a_cross_session_query_counts_through_the_corpus_views`, reshaped — the module-local `relations` and `statement` helpers go, the leaf imports them from `queries`, and the skip reads `describe(name).scope`. The rule is what the derived scope now means, so it carries both jobs
  - The parameter and scope mirrors are deleted, not rewritten. *Evidence:* `test_every_query_file_has_a_manifest_entry` and `test_the_manifest_declares_exactly_the_parameters_the_sql_uses` gone from `test_queries.py`; each would compare a derivation with itself. The obligation they carried moves to the two planted-file crash leaves above
  - The remaining static rules keep reading the same text. *Evidence:* `test_no_query_spells_the_one_past_the_width_cut_by_hand`, `test_every_cut_to_a_width_is_the_macro_or_a_named_exception`, `test_a_cost_is_never_reported_without_its_unpriced_count` and `test_no_query_reads_the_clock` pass unchanged over `queries.statement(name)`, with `CUT_SQL` built from the moved helper
- **Viewer tier — `tests/view/`, a `TestClient` over the fixture store** — the pages the derived catalog now feeds
  - Every page's payload bound is still computed over the whole viewer half. *Evidence:* `tests/view/test_bounds.py`, `catalog()` in place of the `QUERIES` import; `test_the_pages_run_at_the_production_sizes` still reads its numbers off the citations the pages printed, not off any manifest, so it is unaffected by where a `Query` comes from
  - Every citation a page carries quotes at least the parameters its query declares, and links to a page that serves. *Evidence:* `tests/view/pages/query/test_query.py:test_a_citation_quotes_every_binding_its_query_takes` (set comparison, order-blind) and `test_only_a_name_the_library_declares_is_served`, whose 404 now comes from `names()`; the two `for name in manifest.QUERIES` sweeps at lines 146 and 237 read `catalog()`
  - The session list still binds its one width at every parameter the query takes. *Evidence:* `tests/view/test_app__list.py` lines 273 and 292, `catalog()["view_sessions"].params` in place of `QUERIES[...]` — named here because the design's file-tree diff omits this file
- **Gate — `mise run check`** — the whole suite plus the doc and link checks
  - No surviving path names `view/manifest.py`. *Evidence:* `check-fast` reports a link or path that does not resolve; the 11 prose references found in `src/hyphae/view/bounds.py`, `src/hyphae/cli.py`, `tests/analyze/test_queries.py`, `tests/analyze/test_query.py`, `tests/analyze/test_timelines.py` and `tests/view/test_bounds.py` are what it will catch
  - `docs/analysis.md`'s sentence still names the module that holds a quoted default. *Evidence:* the line "`src/hyphae/analyze/manifest.py` defines the production defaults that committed reports quote" resolves against a module that still exports `DEFAULTS`
- **Mutation — `mise run mutate 'hyphae.analyze.manifest.*' 'hyphae.analyze.queries.*'`** — cold and serial, so the number reproduces
  - The derivation's own branches are claimed by a leaf. *Evidence:* survivors in `describe`, `relations` and `parameters` read against `uv run mutmut browse`; the scope arm and the `DEFAULTS.get(...)` fallback are the two to check, since both are single expressions the smoke tier could pass over. The `--project` refusal leaf is what claims the scope arm: the corpus-views leaf reads the derived scope to decide what to skip, so an inverted arm only moves which queries it skips

## Not covered, and why

- **Citation binding order for a query with several parameters.** Derivation changes the order of the `k=v` pairs in 33 of 66 citations, because `_resolve` iterates the declared mapping and statement order is not manifest order. Nothing pins it: `_bindings` parses to a dict, the viewer composes its own binding order in `view/store.py`, and every pinned viewer citation string comes from a page rather than from `_resolve`. Left uncovered deliberately — a citation is re-runnable in any order, and pinning one would be a new mirror of exactly the kind this refactor deletes. Flagged so the implementer is not surprised by a committed report whose citation reads differently on re-run
- **Which of the 43 comment lines move into `view_*.sql` headers.** The design leaves this to the implementer's diff, and no test can hold prose to a place. `check-fast` catches only a path that stops resolving
- **A library-wide type clashing with a future query.** `PARAM_TYPES` accepts one type per name by construction, so there is no state a test can put it in; the constraint surfaces as the untyped-parameter crash above when a new name arrives

## Unreachable through the design's seam

None. The two facts that leave the mirror — an untyped parameter and an orphan default — are both reachable at `hp query` through a planted `.sql` under a patched `QUERY_DIR`, because `describe` reads on demand rather than at import. The one obligation the design *claims* is already covered but is not — the runner's `--project` refusals — is reachable at the same seam and is listed as a new leaf above.
