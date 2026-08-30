# Refactor audit — 2026-08-30

Audit findings driving branch `refactor-audit-batch`; scope is Must + Should items (Could items only where an M/S item folds them in).

Findings from seven parallel audits of `src/hyphae` (view architecture, view rendering, extract, export, enrich, analyze + CLI, and a vulture-verified dead-code sweep), judged against *A Philosophy of Software Design* and the project's own rules. Every item carries the auditor's file:line refs; I spot-checked the Must items against the working tree. Treat refs as hypotheses to re-verify at implementation time.

Each item has an id (M/S/C + number) for handoff. Effort: S ≈ under an hour, M ≈ half a day, L ≈ a day or more.

---

## Must

Correctness bugs, one time bomb, and zero-risk deletions.

- **M1. Split Sonnet 5 pricing by effective date — the deadline is tomorrow** — `src/hyphae/extract/pricing.py:64-67`. The comment says introductory pricing ends 2026-08-31 and the table is flat, so from 2026-09-01 every extracted `claude-sonnet-5` call stores `cost_usd` 33% low, silently; re-extraction won't fix it without an `EXTRACTOR_VERSION` bump (`extract/claude_code.py:35`). Add an effective-date dimension to `PRICES` (or split the entry by date) and bump the version. Verified. **S/M**

- **M2. One replay rule for compactions** — `export/duckdb.py:193-201` sets `_COUNTED["compactions"] = False` under a comment claiming fork copies don't exist, so `live_compactions` is unfiltered; `export/otlp.py:456-486` (`copied_compaction`) drops exactly those copies, and `otlp_delivery.py:365-418` re-derives the rule a third time. `plans/otlp-export/testing_plan.md:458` records 4 fork-copied compactions in the canonical store, so the viewer's compaction badge and five analysis queries (`view_run_header.sql:47`, `view_runs.sql:32`, `view_compactions.sql:43`, `view_numbers_compaction.sql:16`, `agent_compactions.sql:23`) over-count while the OTLP census filters. Fix: set `replayed` on `Compaction` at extract time, flip `_COUNTED["compactions"]` to `True`, delete `copied_compaction` and `_check_one_live_copy`. Needs a schema column, migration, `SCHEMA_VERSION` bump; measure the actual over-count in the store first. **M**

- **M3. Refresh the store's views on every open, not only on extract** — `export/duckdb.py:262-268` defines `live_*`, `corpus_*` and the rollups but only the extract path executes them (`duckdb.py:334`), while `enrich/store.py:213` rebuilds its own views on every open *on top of them*. Editing `_live_view` or `_rollup_view` therefore silently does nothing for `hp view`, `hp query` or `hp enrich` until the next `hp extract` against that file. Move view creation into `open_trace_store` (or a shared `refresh_views(connection)`). M2's fix edits `_live_view`, so land this first or together. **M**

- **M4. One read-only opener, one version gate** — three copies of connect + UTC + version check: `analyze/runner.py:113-118`, `view/store.py:144-169`, and the real one at `export/duckdb.py:289-311`. The runner's copy skips the file-exists refusal, so `hp query --db missing.duckdb` ends in a raw `duckdb.IOException` traceback (auditor-verified by running it); the viewer's copy compares `held_schema_version` by hand and never surfaces `MIGRATE_REMEDY` when a store is merely migratable. Route both through `open_trace_store(read_only=True)`, each translating to its own refusal (`QueryError` / `SchemaMoved`). One auditor claimed `hp view` skips startup validation entirely — refuted: `view/app.py:74-78` probes the store at build time; the weakness is only which gate it uses. **M**

- **M5. Single-source the log-cell CSS classes** — `view/columns.py:64-105` declares css per `Column`; `view/components/logs.py:215-296` (`_cells`) restates it as a literal `css=` per cell that can silently disagree. Read `Column.css` off the column being rendered, delete the literals. Verified. **S**

- **M6. Delete the unread `request` params and the one dead function** — `request: Request` is threaded through ~20 handler signatures with only three real reads (`view/listing.py:375,378`, `view/pages.py:93`); unread at `browse.py:129`, all eight `node_pages.py` handlers, five in `fragments.py`, two in `expansions.py`. Also delete `thread_outline` (`view/store.py:248-261`), orphaned when the session-timeline page was retired — sweep-verified zero call sites in src, tests, tools, and docs. **S**

---

## Should

### The viewer DI epic (your `Depends` example — validated)

- **S1. Migrate the viewer to FastAPI `Depends`** — five modules each define `def routes(viewer: Viewer) -> list[BaseRoute]` and nest every handler (plus shared helpers like `fragments.counted`/`prose`/`code`) inside the closure purely to capture `viewer`: `node_pages.py:44` is one 470-line closure; also `fragments.py:30-143`, `expansions.py:120`, `listing.py:322`, `pages.py:32`, assembled at `app.py:123-127`. Handlers are neither importable nor individually testable. Move to module-level `APIRouter`s, `app.state.viewer` set in `build_app`, and a `deps.py` with `ViewerDep = Annotated[Viewer, Depends(...)]` plus a yield-dependency `Db` wrapping `open_store`. Auditor verified the constraints survive: route ordering holds (use `app.router.routes.extend(...)` if `include_router` nesting bites), and exceptions raised inside dependencies still reach the `StoreLocked`/`SchemaMoved` handlers (`app.py:98-112`). One real trade-off: a yield-dependency holds the read-only connection through rendering, where today routes close it before `viewer.html(...)` runs — keep explicit `open_store` in the few fat routes if the lock window matters. Unnests ~1,900 lines; also dissolves `viewer.py`'s reason to exist and fixes S12's double-open. **L**

- **S2. A `Knobs` object built by one dependency** — both view audits converged here independently. `nav`/`kin`/`log`/`detail` are declared with identical defaults in 12 handler signatures (`node_pages.py:52-56` et al., `expansions.py:158-162,231-234,351-355,377-380`), validated twice (`browse.py:147-150`, `knobs.py:59-66`), and threaded as four positionals into 10-param functions (`browse.py:127-138`, `expansions.py:266-296`). One `Knobs` NamedTuple that validates on construction and carries `.suffix`; routes take it via `Depends`, and `browse`, `spilled`, `preset_choices`, `pager` take one value. The widest interface in the package. **M**

- **S3. Shrink `node_page.page()`'s 21 keyword parameters** — `components/node_page.py:69-92`. The signature is as complex as the body — a shallow module by Ousterhout's measure. Group into the view-models the params already form (the node's body inputs; the chrome: crumbs/walk/failures/citations; the NavTree side). Do after S2 so the knob group folds in. **M**

### Viewer layering

- **S4. `listing.py` is four layers in one module** — SQL fragment `SHOWN` (`view/listing.py:125-132`), f-string query composition `sorted_sessions` (145-203), request parsing (206-233), row→view-model mappers (266-319), and the routes (322-467). Everywhere else SQL composition lives in `view/store.py` (`window`, `cursorless_rows`, 223-286). Move `SHOWN`, `SORTS`/`FILTERS`/`DIRECTIONS`, and `sorted_sessions` beside `window`; the closed-dict safety argument moves intact. The one place a route module builds SQL text. **M**

- **S5. Window-count column names leak into routes** — routes know each query's count column as a string literal: `"matched_api_calls"` (`browse.py:313`), `"matched_tool_calls"` (`node_pages.py:266`), `"matched_errors"` (`errors.py:67`), `"matched_projects"` (`listing.py:351`), `"matched_records"` (`pages.py:120,147`). `store.py:215` already shows the right pattern (`MATCHED_ROWS`). Standardize the library queries on one name, or hang it on the `Page`/`Fragment` enum member so `listed()`/`paged()` default it. A query rename currently breaks a distant route at runtime. **S/M**

- **S6. Session-header bindings spelled twice** — `browse.py:113-124` defines `header_bound` "named once for every reader" (its own docstring), but `expansions.py:304-307` re-spells the identical three bindings inline for the same `Page.SESSION_HEADER` read. Call `header_bound` there. **S**

- **S7. Build the Facts NamedTuples from their own fields** — `components/node_body.py:28-158` declares six `*Facts` NamedTuples; `builders.py:333-437` (`node_facts`) hand-writes ~100 lines of `field=row["field"]` where every key equals the field name (auditor verified for Session/Turn/Run/Call/Tool/Compaction; only `BucketFacts` differs). `Cls(**{f: row[f] for f in Cls._fields})`, keeping explicit construction for the composed cases. **S**

- **S8. One pager** — `components/logs.py:33-40,182-201` and `components/listing.py:240-247,350-375` hold near-identical `Pager`/`Pages` NamedTuples and prev/next markup over the same `knobs.pager` output. Keep one, parameterize the URL builder. Real duplication, not coincidental. **S**

- **S9. Share the session-list bindings between query and citation — carefully** — `view/listing.py:183-195` (query bindings) and 429-457 (footer citation bindings) repeat `head_chars`/`item_chars`/`head_items` and the enrichment quartet; a citation drifting from its query is a false citation. Caveat from my spot-check: the difference in `limit` (`size + 1` for the pager vs `size` cited) is deliberate — share the common core dict, keep the intended divergence explicit. **S**

- **S10. Rename `formatters.py` → `tool_names.py`** — `view/format.py` (value→string filters) and `view/formatters.py` (tool-call naming registry) share a stem and nothing else; both audits flagged the pair independently. `.claude/rules/viewer-ui.md` already describes the latter as "the only place a tool call is named", supplying the name. **S**

- **S11. Move `LIST_URL` out of the markup package** — `components/listing.py:22` defines `LIST_URL = "/sessions"`, and the logic layer imports its own mount point from the component it renders (`view/listing.py:33,249,357`, `browse.py:240`). Move it beside the other route constants (`view/nodes.py` owns `BODY_URL`, `KIN_URL`); the component imports it. The one inversion of the components-are-leaves rule. **S**

- **S12. One store open per enrichment-line fragment** — `fragments.py:197-198` opens a connection to check `enriched()`, closes it, then `fetched` (153-154) opens a second for the same request: two lock windows, and the existence check and read see different snapshots. Pass one connection through (or S1's `Db` dependency). Contradicts the one-connection-per-request contract in `store.py:1-5`. **S**

- **S13. Keyword-only `detail_of`, collected once** — `view/detail.py:120` takes five positionals, and four routes (`node_pages.py:121-142,189-220,283-304,350-391`) each re-type the same `[item for item in (...) if item is not None]` assembly. Make the params keyword-only and add one `details(*maybe)` collector. **S**

- **S14. `way` as a StrEnum** — `components/node_page.py:273-324` branches navigation on `way == "previous"`, a magic string in two components. Two-member `StrEnum`. **S**

### Extract structure

- **S15. Split `session_files.py` along the four jobs its docstring names** — `extract/session_files.py:1-10` lists them itself: file classification (128-212), line reading (325-357), replay reconciliation (65-116), agent-run assembly (215-268). 393 lines spanning path parsing, JSON reading, and model assembly; the hardest file in the layer to change safely. Split: classification → a layout module, reading → `transcript.py`, assembly → `agent_runs.py`. **M**

- **S16. Make the real extract seams public** — `claude_code.py:17-26` imports ~11 underscore names from its siblings, and `session_files.py:19` imports four more from `transcript.py`. The `_` prefix says internal, but these *are* the interfaces. Promote the cross-module entry points to public names with one-caller docstrings; keep `_` for what stays in-file. **S**

- **S17. Transcript knowledge leaks both directions between `transcript.py` and `session_files.py`** — `transcript.py:50-56,103-130` declares `_Line`/`_check_type` but only `session_files.py` builds and validates them (`_read`, 325-357); conversely `session_files.py` reads record internals directly in `_fork_context` (271-281), `_workflow_launches` (284-299), `_replays` (65-91), `_resolve_duplicates` (369-393). Neither module owns "what a record is". Consolidate line reading, type checking and dedup in one place; move the record readers beside their kin. Rides naturally with S15. **M**

- **S18. Split `sessions.py`: project identity vs Claude Code's disk layout** — `sessions.py` holds layout constants (22-39), project path identity (42-100), a SQL fragment `project_predicate` (79-91) consumed by `analyze/runner.py:40`, `enrich/store.py:167`, `view/listing.py:88`, and discovery (146-166). A query-layer module currently imports the file-discovery module to get one SQL string. Split into `projects.py` (resolution + predicate, shared by every layer) and an extract-side layout module. Also removes the name clash with `extract/session_files.py`. **M**

- **S19. Stop flattening and re-guessing a session's file structure** — `sessions.py:103-143` builds `SessionFiles` (knows `transcript`, `directory`, `subagent_transcripts()`); `claude_code.py:49-56` flattens it to a path tuple; `session_files.py:360-366` then string-matches `f"{id}.jsonl"` to find the transcript again and `_classify:136` recomputes the directory. Pass the structure through. Related: `SessionSource.files` (`pipeline.py:15-25`) is never read by the seam itself (`refresh()` uses only `id` and `fingerprint`) and `StoreSource` fills it with `()` — drop it from the protocol or make it an opaque extractor payload, since `pipeline.py:5-6` claims everything agent-specific lives behind `Extractor`. **M**

- **S20. `SessionLayoutError` beside `TranscriptSchemaError`** — `record_types.py:14-19` defines the error as "a transcript held a shape this parser does not know", but three of six raises in `session_files.py` (160, 191-212, 366) are about the session *directory*, not a transcript. A schema change and a layout change need different responses and currently look identical. Sibling class, same base. **S**

- **S21. `_required_timestamp` names the wrong record kind** — `transcript.py:273-279` hardcodes "prompt" in its message; six of eight callers are compactions, pr links, api calls, tool calls. Take the kind as an argument or read `line.record["type"]`. The message is the whole value of a fail-fast crash. **S**

- **S22. Move `record_types.py` into `records/`** — `extract/record_types.py` (the registries) is imported by every module in `records/` and is conceptually part of it; meanwhile three neighbours are named "schema" (`records/schema.py` — a docs generator; `export/schema.py` — versions and migrations; `docs/schema.md`). Move to `records/registry.py` and rename `records/schema.py` for its job (e.g. `field_tables.py`). The split a newcomer misreads first. **S**

### Store schema registries

- **S23. One `TableSpec` registry instead of three dicts in two packages** — `export/duckdb.py:274-286` (`TABLES`, `SESSION_KEY`) and `extract/store.py:28-44` (`ROW_ORDER`) are hand-maintained dicts over the same table names; `ROW_ORDER` restates the DDL's primary keys with nothing tying them together, and `SESSION_KEY.get(table, "session_id")` is spelled at both `duckdb.py:399` and `extract/store.py:130`. A new table needs three edits in two packages; a PK change that misses `ROW_ORDER` silently changes read order. One `TableSpec(model, session_key, order)` in the export layer. **S**

- **S24. Parity test: DDL columns == dataclass fields** — insert and read both build column lists from `dataclasses.fields(TABLES[table])` (`duckdb.py:423`, `extract/store.py:126`), so every DDL column must equal a model field, but nothing states it and `test_a_trace_round_trips` only exercises tables whose fixtures carry rows (`raw_records` and `offload_files` are only counted). Assert `declared_shape(_SCHEMA)[table] == {f.name for f in fields(model)}` for every `TABLES` entry. Don't generate DDL from dataclasses — it would lose the hand-written column comments. **S**

### Export seam

- **S25. Restate the `Exporter` contract honestly** — `pipeline.py:49-51` documents `export()` as "replace everything the sink holds for this session, atomically"; `DuckDbExporter` honors it, `OtlpExporter` cannot (batch-by-batch POST; its own docstring at `otlp_delivery.py:208-213` correctly says at-least-once). Restate the seam as "the sink holds this session at this fingerprint, or the run records nothing and retries". **S**

- **S26. Move `census` out of the transport module** — `otlp_delivery.py:26-34,365-418`: the module docstring claims it "never reads a store row, only the spans that module made", yet `Census`/`census`/`AmbiguousCompactionError` import mapper internals and do no HTTP. Move beside the mapper in `otlp.py`. The one leak in an otherwise clean mapping/transport split; partially dissolved by M2 (which deletes `_check_one_live_copy`). **S**

### Enrich

- **S27. One `LevelSpec` for the three enrichment levels** — level knowledge is declared in seven registries across three files: `prompts.py:34` (`PROMPT_VERSION`), `:38` (`_SUBJECT`), `:149-155` (three budget constants — two byte-identical), `:423-430` (`render()`'s match), `store.py:114-138` (`LEVELS`), `:540-544` (`readers`), `enricher.py:57` (`ROUND_ORDER`). Adding or renaming a level touches all seven with no enforcement. Fold `reader`, `renderer`, `budgets`, `prompt_version`, `subject` into `LevelSpec`, hosted in a level module neither `store.py` nor `prompts.py` (see S30) so no cycle. **M**

- **S28. One definition of the composite item key** — the `level|key|values` string is hand-built in eight places (`prompts.py:217,222`; `store.py:605,607,609,610,616-618,638`), including f-strings that must happen to match `Item.key_values` order and a hardcoded `MAIN_SOURCE`. A format change produces no error — parents stop matching and every item reads permanently stale. One `item_key(level, *values)` with `level_of` as its inverse, all callers routed through. **S**

- **S29. Make the dry run the real run with a stubbed client** — `enricher.py:60-79` (`plan`) and `:81-127` (`enrich`) traverse staleness by different rules and apply `--limit` differently (whole-list slice at :79 vs per-round decrement at :117-119), so `--dry-run --limit N` doesn't describe what `--limit N` sends. Have `plan` reuse `enrich`'s round construction, simulating success. The dry run is the only thing between an operator and a paid pass. Also: bind `sending[:remaining]` once (:117,119). **M**

- **S30. Split the item models out of `prompts.py`** — `prompts.py` holds `Level`, `Budgets`, eight item/row models, and the prompt renders (572 lines, three concerns); the persistence module imports its row types from a module of prompt text (`store.py:17-27`), and `enricher.py`, `cost.py`, `cli.py` all reach into `prompts` for `Level`. Split into `enrich/items.py` (or onto S27's registry). Enables S27 without a cycle. **M**

- **S31. Pair the project clause with its parameters mechanically** — `enrich/store.py:165-172` defines `_project_clause`/`_project_parameters`, hand-paired at eleven query sites (244/246, 295/304, 328/329, 335/337, 401/403, 419/421, 492/494, 516/517, 528/529, 568/569, 613/614). Forgetting the parameter half raises; forgetting the clause half silently returns the whole corpus — exactly the wrong-corpus error class the evidence rules exist to prevent. One `self._select(sql, project, *extra)` that splices both. **S**

- **S32. Keep the last stderr when the enrichment CLI fails** — `client.py:335-337` turns a non-zero exit into `Failed(api_error)` and discards `done.stderr`; a mistyped `--model` or revoked login fails every item identically and `EnrichmentFailed` (`enricher.py:42-49`) prints five keys and the word `api_error`. Keep a capped stderr tail (never stdout — that's where transcript text returns) and include it in the breaker-trip path. A 100%-failed run currently gives the operator no thread to pull. **S**

### Analyze + CLI

- **S33. Split the manifest: analysis vs viewer** — `analyze/manifest.py:59-577` mixes 22 `Scope.CORPUS` analysis queries with 44 `view_*` entries (~310 of 577 lines); the comment at :268 admits "the `view_` family belongs to the trace viewer". Two tables — the viewer's beside `view/bounds.py` — merged into one `QUERIES` registry the runner and smoke tier keep reading. **M**

- **S34. Make the `view_*` defaults true or delete them** — the viewer never reads the manifest (`view/store.py:181-186` binds whatever the caller passes), yet `manifest.py:3-6` calls a default "the value a bare invocation runs, and the value a committed report quotes". `chip_chars` is declared 60 (`manifest.py:338,379,446`) while the viewer binds 300, 100, 110, and 60 at four sites (`browse.py:162`, `node_pages.py:420`, `nav_tree.py:226,300`, `fragments.py:110`). Either have `page_rows` fill unbound params from the manifest, or drop defaults on `view_*` params so each surface must state its width. **M**

- **S35. A `cut(text, chars)` macro** — the one-past-the-width idiom `substr(x, 1, $chars + 1)` is written out 49 times across 18 files in `analyze/queries/`; the same protocol is already a macro for tool input (`macros.py:74-79`). Add it to `macros.BOUNDING`; the deliberate no-`+1` sites (closed vocabularies) then stand out as exceptions. The cut protocol is load-bearing for the viewer's payload bound and is currently a convention, not a definition. **M**

- **S36. Close `_parse`'s match** — `analyze/runner.py:180-190` covers three `ParamType`s with no fallthrough; a fourth enum member would silently bind SQL NULL. `case _: raise QueryError(...)` or `assert_never`. **S**

- **S37. Move `_census_otlp`'s loop into the library** — `cli.py:280-296` hand-drives `(source.extract(s) for s in source.sessions(project))`, re-implementing `pipeline.refresh` minus the fingerprint diff. Move to `export/otlp_delivery.py` (or wherever `census` lands per S26) as `census_project(...)`. The only real library logic left in the CLI. **S**

- **S38. Make `open_trace_store` a context manager** — it returns a bare connection, so `cli.py:267-274` and `:282-289` each write the try/finally close dance and `_export_otlp` nests a second try. `@contextmanager` like `view/store.open_store`. **S**

- **S39. `hp query --list`** — the only query listing today is `runner.py:103` dumping 68 names onto one error line, and `docs/analysis.md` step 2 effectively says "run `ls`". Print name, scope, and required params from the manifest. **S**

---

## Could

Worth doing opportunistically — ride them on whichever branch touches the file.

### Viewer

- **C1. Resolve the alias-forcing name pairs** — five basenames exist in both `view/` and `view/components/` (nav_tree, listing, citation, numbers, pages), forcing every import to alias, with the same alias meaning different modules in different files (`pages.py:19` vs `viewer.py:15`). The split itself is principled and enforced — rename the worst logic-side modules or accept it. **S/M**
- **C2. Rename `browse.py`** — it's the shared node-page engine, owns no routes, sits among route modules, and its name says nothing (`node_page_engine.py`, or fold into `node_pages.py`). **S**
- **C3. Deep accessors per query family** — routes hand-assemble every binding dict (`node_pages.py:62-65,95-101,167-172,245-263`; `fragments.py:45-51,63-69`), each caller knowing the query's parameter vocabulary. The citation design partly wants bindings visible; add typed accessors only where dicts already duplicate. **L**
- **C4. Pure `build_app`** — `app.py:74-78` opens the store at build time, so `tools/gen_routes.py:69-78` fabricates a temp DuckDB just to read `app.routes`. Move the fail-fast probe into `serve()` and `dev_app()`. **S**
- **C5. Break the `dev.py` ↔ `app.py` cycle** — `dev.py:26` imports `STATIC` from `app.py` while `app.py:86` lazily imports dev; move `STATIC` so the only lazy import left is the watchfiles-justified one. **S**
- **C6. Drop the seven one-line NavTree adapters** — `nav_tree.py:499-524` wrap level builders only to fit the `CHILDREN` table's uniform signature, and two builders take a `connection` they never use (:428, :438). Give builders the uniform signature or let the table carry the adapter kind. **S**
- **C7. Split `parts.stacked`** — 7 params, callers pad with trailing `None`s to reach their slot (six sites in `components/listing.py`). Two shapes are actually used. **S**
- **C8. Extract `counted_unit` / `more` components** — the errors, records, and offload pages each hand-write the same `.numbers` span pair and `.more` block (`components/pages.py`). **S**
- **C9. `GLYPH`/`GLYPH_CLASS` placement** — a CSS class in logic-module `enrichment.py:31-34`, read only by `components/parts.py`; `.claude/rules/viewer-ui.md` blesses the current spot, so move only if touching it, and update the rule. **S**

### Extract

- **C10. Merge the two price tables** — `pricing.py:58-70` (`PRICES`) and `:86-94` (`CONTEXT_WINDOWS`) keyed by the same model strings, kept in sync only by a test. One `ModelSpec(input, output, context_window)` — natural to fold into M1. **S**
- **C11. `_charges` returns per-million rates in a dollars type** — `pricing.py:116-134` reuses `CostSplit` for USD-per-million; the docstring says so, the type doesn't. Private `_Rates` tuple. **S**
- **C12. Reader helpers for the repeated comprehensions** — record-type filters at ~10 sites (`transcript.py:160,186,208,229,289,353,437,488,519`; `session_files.py:279`) and the timestamps-that-exist comprehension at 4. `_of_type(...)` / `_moments(...)`. **S**
- **C13. A `_Thread` context object** — `session_id`/`source`/`replayed` threaded through ten reader signatures (`transcript.py:88-510`), mostly for error messages. Rides well with S15/S17. **M**
- **C14. Construct once in `_offload_file`** — `session_files.py:302-322`: two branches differing only in `content`/`lossy_decode`. **S**
- **C15. `model_for` is test-only machinery** — `records/shapes.py:560-577`, called only by `tests/extract/test_records.py`. Move it to the test or docstring its real job (mapping real records for the drift tier). Also: the module-level loop at :562-569 leaks `_model`/`_subtype` into the namespace. **S**
- **C16. `_Parsed.merge`** — `claude_code.py:87-98` flattens with four comprehensions and three loop-variable names for one idea. **S**
- **C17. Field-name constants shared by models and parser** — the record models describe fields the parser reads as raw dicts, tied together only by a source-grepping drift test (`test_records__drift.py:116-160`). Export the raw field names from the models for the parser to import — keeps every `Cited` annotation, makes the grep a reference. Full `model_validate` parsing is the bigger option and costs runtime on ~290k records. **L**

### Export

- **C18. Collapse `TextPolicy` to `max_chars: int | None`** — `otlp.py:105-118`: `include=False` makes `max_chars` meaningless, and the CLI silently ignores `--max-chars` without `--include-text` (`cli.py:251,327-330`) — refuse that combination. **S**
- **C19. A `Shaping` context for the six span builders** — each threads `session`/`text` differently and the root takes a different first argument (`otlp.py:121-156,192,254,277,316,351,434`). **M**
- **C20. Group `OtlpExporter.__init__`'s four test seams** — nine params, eight defaulted (`otlp_delivery.py:219-247`); `batch_spans`/`rate`/`monotonic`/`sleep` into one pacing/transport object. **S**
- **C21. `session_span_id` / `run_span_id` wrappers** — four call sites pass `""` for a `source` slot they don't have (`otlp.py:88-102` used at 209, 372, 414, 499-500). **S**
- **C22. Drop `spans_sent` or build `--verify`** — `otlp_delivery.py:73-74`: a column written for a feature that doesn't exist, read only by tests, inside the version-pinned DDL digest. Cheap to remove now, a migration later. **S**
- **C23. Rename `export/duckdb.py` → `export/trace_store.py`** — the writer is named for its engine (and shadows the `duckdb` package name); its reader counterpart is `extract/store.py`. `CONTEXT.md` already owns the term. ~8 importers. **S**
- **C24. `schema.create_tables` helper** — three DDL owners repeat guard-then-create-then-close-on-failure with the same copied comment (`duckdb.py:302-310,326-340`, `otlp_delivery.py:244-247`, `enrich/store.py:207-217`). **S**
- **C25. Generate `_rollup_view`'s eleven correlated subqueries** — `duckdb.py:225-259`: five counts and four coalesced sums from two tuples; one grouped aggregate would also cut three scans of `{prefix}_api_calls` per session. **S**

### Enrich

- **C26. Derive the enrichment payload columns once** — the column set is stated five times in `enrich/store.py` (35-47, 76-99, 140-150, 634); derive DDL body and projections from one `_PAYLOAD_COLUMNS` tuple, `model` alias named once. **S**
- **C27. Move `FailureKind` to neutral ground** — four of six members are transport states produced only by `client.py`, which imports the enum back out of `validation.py` (`validation.py:17-38`, `client.py:31`). **S**
- **C28. Validate `--model` against `PRICES` at startup** — a typo today burns the full breaker cycle and reports `api_error` (`cli.py:228-232`, `client.py:143`, `cost.py:27-30`). The startup-validation rule points here. **S**
- **C29. Memoize `_run_links` / skip the needless join** — re-queried from two paths per pass (`store.py:506,603`), and every reader joins `sessions s` even when `project is None` (folds into S31's helper). **S**
- **C30. Point the analyze `*_said` queries at the `enriched_*` views or delete the views** — `store.py:76-96` declares three views with one consumer (`enrichment_digest.sql`); seven other sites hand-write the LEFT JOIN (the viewer's is justified in `docs/enrichment.md`; the analyze queries' aren't). **M**
- **C31. Simplify `_fit`** — `prompts.py:535-568`: a hand-rolled two-ended greedy fill; cumulative-sum arithmetic would replace the index bookkeeping. Well tested, so readability only. **M**
- **C32. Drop the `cost.Prompt` adapter** — `cost.py:49-54` wraps two fields `PlannedItem` already has (`cli.py:345`); accept `Sequence[tuple[Level, str]]` instead. **S**
- **C33. Docstring the store's test-only surface** — `turn_items`/`run_items`/`session_items` are reached in src only through `items()` ("the enricher's one door", its own words); one docstring line saying tests are the other caller. **S**

### Analyze + CLI

- **C34. Move viewer constants out of `analyze/queries.py`** — `CRUMB_CHARS` (:213), `LIST_ITEMS` (:174), `FIRST_PAGE` (:230), `UNATTRIBUTED` (:243), `VIEW_PREFIX` (:247) are used only by `view/` and tests; `view/bounds.py:8-10` states the rule they break. **S**
- **C35. One spelling for the width `Param`s** — `Param(type=INTEGER, default=X)` inline 13× in `manifest.py` while `queries.py:150-226` defines shared `*_CHARS_PARAM` constants for four other widths; pick one style. **S**
- **C36. Decide `co_occurrence`'s fate** — `manifest.py:62-67` + 60 lines of SQL + a floor-of-1 test, cited by no report or doc, unlike every other analysis query (needs your call: drop or document). **S**
- **C37. Delete the `--no-batch` rejection test** — `tests/enrich/test_enricher__cli.py:80-86` pins argparse rejecting a removed flag: a compatibility shim in test form. **S**
- **C38. Symmetric project resolution** — `cli.py:213` resolves the project for `hp enrich` while `hp query`'s runner resolves internally (`runner.py:144`); push it into the library on both paths. Related: `build_client` (`cli.py:74-79`) is a test seam living in the argv layer — move beside the client if touched. **S**

---

## Suggested sequencing

1. **Now:** M1 (deadline), then M6 + the dead function — trivial, land immediately.
2. **Store correctness branch:** M3 → M2 (M2's view edit needs M3 to take effect) → M4, with S23/S24 riding along.
3. **Viewer DI branch:** M6's deletions → S2 (`Knobs`) → S1 (`Depends`) → S3, with M5, S6, S12, S13 riding.
4. Everything else lands independently; C items ride whichever branch touches their file.

## Method notes

- Auditors: view architecture and this synthesis on Fable; view rendering, extract, export, enrich, analyze on Opus; the dead-code sweep on Sonnet with vulture at 60% confidence, every hit grep-verified.
- The sweep found exactly one dead symbol in the whole repo (M6's `thread_outline`) — no orphaned tools, unused deps, or commented-out code. The codebase is in unusually good shape; most of this list is about making it cheaper to change, not fixing rot.
- Items the auditors checked and judged fine, so you don't re-litigate them: no fail-fast violations anywhere (every broad `except` re-raises after cleanup); the components/logic split is principled and test-enforced; the exhaustive `CHILDREN` table is by design; schema knowledge outside the registries above is well factored; all six `tools/` generators are live.
