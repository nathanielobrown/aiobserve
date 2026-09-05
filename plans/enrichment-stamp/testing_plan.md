# Testing plan: give the enrichment stamp a module

Obligations for `plans/enrichment-stamp/design.md`, grouped by the level tests run at and tagged with the design slice that owes them. Every leaf ends with an *Evidence:* clause naming the artifact that discharges it; an auditor traces each leaf to that artifact.

The design's decisions are taken as settled here: `Versions.prompt` is a `Mapping[Level, int]`, `PAYLOAD_COLUMNS` is public and `_SCHEMA` stays underscored, and the four monkeypatches go.

Three rules shape everything below.

- **This refactor moves no schema and reads no Claude Code field.** The stamp is ours, so the recorded-session rule bites only through the base rows: every store leaf runs over a real DuckDB built by the pipeline from `tests/fixtures/` (`tests/enrich/conftest.py:fixture_db`, `mutable_db`, `store`, and `test_enricher.py:forest`). Stamps and model answers are invented and always were — there is no recorded session that holds one
- **Prefer recomposing an existing leaf.** Nine of the leaves below are named existing tests with their oracle unchanged; the plan says which and what moves in each
- **An oracle keeps reading the declarations, not the module.** Where a test judges staleness, it reads `LEVELS[level].prompt_version` and `TAXONOMY_VERSION` itself. A test that asked `Versions` what the versions are would test the code against itself

## Levels

- **unit (stamp)** — `tests/enrich/test_stamp.py`, new file. Plain maps and strings in, verdicts out; no store, no client, no I/O. The one new file: the module is new, and `.claude/rules/testing.md` mirrors the package layout
- **integration (enrichment store)** — `tests/enrich/test_store.py`. A real DuckDB on `tmp_path` under `mutable_db`; assertions are SQL and round trips through `upsert`/`stamps`
- **end-to-end (a pass)** — `tests/enrich/test_enricher.py`. The real `spine/` and three-session `forest` stores, real renders, `passes.py:FakeClient` for the model
- **integration (CLI)** — `tests/enrich/test_enricher__cli.py`. `cli.main("enrich", …)` over the same stores, under the autouse subprocess guard
- **integration (viewer)** — `tests/view/test_enrichment.py`. Served pages over the planted store (`tests/conftest.py:planted_stamp`), read through `data-enrichment`

---

## Slice 1 — the module and the seam

### unit (stamp)

- `stale` names the planned key whose held stamp differs, and only that one, on each of the four axes. *Evidence:* new parametrized leaf over `{"input_hash": …}`, `{"prompt_version": 99}`, `{"taxonomy_version": 99}`, `{"model": "claude-sonnet-4-5"}` — the four cases lifted from `test_store.py:test_staleness_returns_the_rows_whose_key_moved`, applied to a `replace()`d copy of a planned stamp in a plain held map. Invented stamps, as every stamp in the suite already is.
- A planned key the held map does not hold is stale. *Evidence:* `stale(planned, {})` returns every planned key, in the planned order — the assertion `test_store.py:test_an_item_with_no_row_is_stale` makes today.
- An identical stamp is not stale, and a held key nothing planned is not reported. *Evidence:* one leaf asserting `stale(planned, dict(planned) | {"extra": other})== []`; the second half is what keeps `stale` from reporting the store's own leftovers as work.
- **`Versions.current()` is the one read of the two declarations.** *Evidence:* assert `current().prompt == {level: LEVELS[level].prompt_version for level in Level}` and `current().taxonomy == TAXONOMY_VERSION`, the oracle reading `levels.py` and `taxonomy.py` directly. Bolded: this equality is the whole reason the viewer and the enricher can no longer disagree, and it is also what makes every `moved_past` leaf below meaningful.
- `Versions.stamp` puts the four together: the hash of what it was handed, the level's version, the taxonomy version, and the model on the call. *Evidence:* assert the whole `Stamp` against a spelled-out expected built from `input_hash(rendered)` and the declarations, for two levels — so a `stamp` that read one level's version for another is caught.
- `input_hash` survives the move byte for byte. *Evidence:* assert the digest of a short fixed string equals `hashlib.sha256(...).hexdigest()`, computed in the test. A hash that changed under the move would silently re-enrich every row in the canonical store.
- `moved_past` is true when either version differs and false on today's pair. *Evidence:* three cases against `Versions.current()` — today's pair, a prompt version behind, a taxonomy version behind — with the expected values read off the declarations.
- `moved_past` judges neither the hash nor the model. *Evidence:* the signature takes neither; assert two rows differing only in a model name get the same verdict, which is the partial rule the docstring promises and the viewer relies on.

### end-to-end (a pass)

- **A prompt-version bump re-enriches that level and no other, driven through the argument rather than a monkeypatch.** *Evidence:* `test_enricher.py:test_a_prompt_version_bump_re_enriches_the_level`, rewritten to `enrich(store, FakeClient(), versions=replace(current, prompt={**current.prompt, Level.turn: 99}))`; `monkeypatch.setitem(LEVELS, …)` at line 198 deleted, the existing assertions on `client.keys` and on the stored `prompt_version` kept. Bolded: this leaf is the design's stated symptom, and the whole seam exists so it can be written this way.
- **A taxonomy bump re-enriches every level.** *Evidence:* `test_enricher.py:test_a_taxonomy_bump_re_enriches`, rewritten to pass `replace(current, taxonomy=99)`; `monkeypatch.setattr("hyphae.enrich.enricher.TAXONOMY_VERSION", 99)` at line 211 deleted, the four-key ordering assertion kept. Bolded: the monkeypatched attribute path is the one a reader could not tell from a real bump, and deleting it is what proves `enricher.py` no longer imports the constant.
- A `--model` switch still re-enriches, with `versions` unchanged across both passes. *Evidence:* `test_enricher.py:test_a_model_switch_re_enriches`, unchanged but for the new keyword — the leaf that would fail if `Versions` had absorbed the model and a second copy could disagree with `client.model`.
- A second pass over an unchanged store, handed `Versions.current()`, sends nothing and rewrites nothing. *Evidence:* `test_enricher.py:test_a_second_run_over_an_unchanged_store_sends_nothing` over the `forest` store, gaining the keyword only.
- A written row carries today's versions, judged against the declarations rather than against `Versions`. *Evidence:* `test_enricher.py:test_a_run_writes_a_row_for_every_stale_item`, whose expected tuple already spells `LEVELS[Level.turn].prompt_version` and `TAXONOMY_VERSION` — the independent oracle, kept as is.
- A dry run and a paid pass judge staleness by one object. *Evidence:* `test_enricher.py:test_a_dry_run_names_exactly_the_items_a_run_sends`, with the same `versions` value handed to `plan` and to `enrich`; the equality of `client.keys` and the planned keys is the existing assertion.

### integration (CLI)

- **`hp enrich` runs a pass against today's declarations, because `_enrich` builds `Versions.current()`.** *Evidence:* extend `test_enricher__cli.py:test_the_cli_writes_what_the_library_writes` to assert the stored `prompt_version` and `taxonomy_version` of the rows the CLI wrote equal `LEVELS[level].prompt_version` and `TAXONOMY_VERSION`. Bolded: `cli.py` is the only production caller, and nothing else would catch a CLI that passed an empty or hand-built `Versions` — every other leaf supplies its own.
- The dry-run leaves keep quoting what a pass sends. *Evidence:* `test_enricher__cli.py:test_a_dry_run_counts_the_ancestors_of_what_is_stale` and `test_a_dry_run_quotes_a_price_it_computed_itself`, whose `plan(store, MODEL, project=None, limit=None)` calls (lines 201, 221) gain `versions=`; assertions unchanged.

---

## Slice 2 — one column list

### integration (enrichment store)

- **`upsert` then `stamps(level)` returns what was written, keyed by `item_key`.** *Evidence:* recompose `test_store.py:test_staleness_returns_the_rows_whose_key_moved` as a round trip — enrich every `spine/` main turn under one stamp, assert `store.stamps(Level.turn) == {item.key: stamp() for item in items}`, then mutate one stored column and assert `stale(planned, store.stamps(Level.turn)) == [target.key]`, still parametrized over the four columns. Bolded: this is the only leaf that reads the `COLUMNS`-driven `SELECT` against the `astuple`-driven binding, so a reordering of either — which typechecks and which the DDL parity test cannot see — fails here and nowhere else.
- An item with no stored row is stale. *Evidence:* `test_store.py:test_an_item_with_no_row_is_stale`, rewritten to `stale({item.key: stamp() …}, store.stamps(Level.turn))`; the assertion is unchanged.
- **Every level's table declares exactly its keys plus the payload columns.** *Evidence:* new leaf replacing `test_store.py:test_stamp_is_the_four_field_staleness_key` — `declared_shape(store._SCHEMA)[spec.table] == set(spec.keys) | set(PAYLOAD_COLUMNS)` for every `LevelSpec` in `LEVELS`, via `export/schema.py:declared_shape`, which runs the DDL against a scratch DuckDB. Bolded: a fifth stamp field, or an `Enrichment` field added without a DDL column, fails here naming the table that must change — which is what the deleted four-field pin was for and more than it could do.
- The enrichment fields land in their own columns and read back as the taxonomy's plain strings. *Evidence:* extend `test_store.py:test_a_second_upsert_replaces_the_row` to select `category, outcome` beside `description, input_hash` and assert `("test", "completed")`. This is the guard on the binding's one non-mechanical step: `upsert` coerces the two `StrEnum` members with `str()` today, and a binding written as a bare `astuple(enrichment)` would hand DuckDB the members.
- The three views still project every payload column, with the enrichment's model renamed. *Evidence:* `test_store.py:test_the_run_and_session_views_left_join_too`, unchanged — its `>= {"brief", "agent_model", "description", "enrichment_model"}` assertion is the regression on the derived projection, and the leaf that fails if `enrichment_model` is lost when the `e.…` list is generated.
- Every query in the analysis library that reads the views by column name still runs. *Evidence:* `tests/analyze/test_enrichment.py`, unchanged — `test_coverage_splits_a_level_by_the_stamp_its_rows_were_written_under` and `test_a_digest_lists_one_session_at_every_level…` read `enrichment_model`, `prompt_version` and `taxonomy_version` off the views by name.

---

## Slice 3 — the viewer asks the module

### integration (viewer)

- A row described under an older prompt version is tagged stale and one described under today's is not. *Evidence:* `tests/view/test_enrichment.py:test_an_item_described_under_an_older_version_is_marked_stale`, renamed for the taxonomy case the obligation below adds and otherwise unchanged. Its `wrote()` oracle reads `LEVELS[Level.turn].prompt_version` and `TAXONOMY_VERSION` directly, on purpose: an oracle that called `moved_past` would compare `Enrichment.stale` against the function it now delegates to. The planted corpus supplies both sides — `tests/conftest.py:planted_stamp` puts every fifth row one prompt version behind.
- **The viewer and the enricher call one row stale or fresh alike on the taxonomy axis.** *Evidence:* extend `tests/conftest.py:planted_stamp` to put some rows one taxonomy version behind — the same every-fifth-row shape, on an index coprime with the prompt-version cycle so the two axes can be told apart — and add a case to the viewer leaf above for a row stale on taxonomy alone. See the report: today's planted corpus never moves `taxonomy_version`, so that arm of `moved_past` reaches no page.

---

## Slice 4 — docs

- `docs/enrichment.md` and `CONTEXT.md` name `src/hyphae/enrich/stamp.py` as the owner, and every path and link in them resolves. *Evidence:* `mise run check`, which runs the aigarden link and freshness check over every document and generated block.

---

## Not covered, and why

- **`versions` being a required keyword.** There is no runtime obligation: a call omitting it is a `TypeError` at the call, which no leaf should assert. Pyrefly under `mise run check` is the whole guard, and it is the right one
- **`COLUMNS == tuple(f.name for f in fields(Stamp))`.** Asserting it restates the definition. What the ordering actually buys is checked where it is spent — the store round trip above reads a row written through `astuple` back through `COLUMNS`
- **`Versions.current()` being built per call rather than cached.** A decision with no observable behavior; a test would pin an implementation
- **The grep in the design's "Call paths".** `rg -n 'TAXONOMY_VERSION|\.prompt_version' src/hyphae` returning only the two declarations, `stamp.py` and comments is a slice-verification step, not an obligation with a leaf. A static scan in the style of `tests/view/test_components.py` could hold it, and would be worth adding if the constant creeps back — but the imports it would police are already deleted by named leaves above
- **Live model calls.** As ever: `tests/enrich/conftest.py:refuse_subprocess` fails any test that starts a process
- **A migration.** The tables' columns are the same bytes, so `SCHEMA_VERSION` does not move and there is no migration step to exercise. The DDL parity leaf is what would catch a change that thought otherwise

## Closing checks

- `mise run check` green on each slice, each slice its own commit
- `mise run mutate 'hyphae.enrich.stamp.*'` after slice 1, cold and serial. `stale`, `moved_past` and `Versions.stamp` are small pure functions over the exact comparisons this refactor exists to name once — a survivor there is a claim no leaf makes, and the fix is usually one more assertion in `test_stamp.py`

---

## Report

Twenty-three obligations: eight new leaves (`test_stamp.py`), two replacing deleted leaves (the store round trip, the DDL parity test), seven existing leaves gaining an argument or an assertion, and six existing leaves kept unchanged as regressions.

**Verified against the working tree** (2026-09-05, `main` at `718a2b8`): the two monkeypatches at `test_enricher.py:198` and `:211`; `stale_keys`/`_stamps` having exactly one production caller, `enricher.py:153`; `cli.py` never touching a stamp; `Enrichment` being a frozen dataclass whose field order (`description, category, outcome, friction`) makes the design's `PAYLOAD_COLUMNS` expression reproduce today's `_PAYLOAD_COLUMNS` exactly; `declared_shape` accepting a DDL with views in it (`table_ddl` strips them); `test_stamp_is_the_four_field_staleness_key` and the four-way parametrized staleness test both existing where the design says.

**Unreachable through the seam.** One: `versions` being required. It is a static property of the signature, so no leaf can hold it and pyrefly does — recorded above rather than moved to a runtime level where it would prove less.

**Two findings for the implementer, neither a contradiction.**

1. *The binding is mechanical after all — this finding was refuted while implementing it.* It read: `upsert` coerces the two closed vocabularies with `str()`, so a payload bound as `astuple(enrichment)` would hand DuckDB two `StrEnum` members, and no store leaf reads either column back. The probe — drop both coercions, run the suite — left every leaf green, because a `StrEnum` member is a `str` and binds to the same bytes. The binding is `*astuple(enrichment)` and the coercions are gone. The new assertion on `test_a_second_upsert_replaces_the_row` earns its place on the column *order*, which is what the derived list can still get wrong
2. *The viewer's taxonomy arm is dark.* `planted_stamp` moves only `prompt_version`, so no page in the fixture corpus is stale on taxonomy. `Enrichment.stale` passes both versions to `moved_past`, and today an implementation that dropped the taxonomy comparison would go green. Fixing it costs three lines in `tests/conftest.py`

**Collision with `plans/one-price-table/design.md`:** none in the same lines. That change edits `cli.py`'s `--model` argparse block and `enrich/cost.py`; this one edits `cli.py:_enrich`'s body. Both touch `tests/enrich/test_enricher__cli.py` — one adds a door test, this one adds `versions=` to the `plan()` calls at lines 201 and 221 and extends `test_the_cli_writes_what_the_library_writes`. Whichever lands second rebases cleanly.
