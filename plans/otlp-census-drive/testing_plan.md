# Testing plan: the OTLP census as an exporter refresh drives

Obligations for `plans/otlp-census-drive/design.md` — splitting `DeliveryLedger` out of
`OtlpExporter`, adding `OtlpCensus` as a second `Exporter`, deleting `census_project`, and
driving the dry run through `refresh()`. Every repository claim the design makes was
re-checked against the working tree on 2026-09-05; the report at the bottom says which held.

The seam is the design's: `refresh(project, extractor=StoreSource(connection),
exporter=OtlpCensus(...))` over the fixture stores, with `census(traces)` and the SQL
`MAPPING` formula as the two oracles `tests/export/test_otlp__census.py` already carries. The
design's stated leans stand: the no-key dry run, and the ledger staying in
`export/otlp_delivery.py` — the transport docstring pin below is what settles the second.

**This plan is written against the tree as `plans/trace-replay-rule/` will leave it.** That
refactor rewrites `session_spans` over `trace.live()` and derives the store's `live_*` family
from `LiveRows`. Its plan lists no edit to `tests/export/test_otlp__census.py` — it only runs
those leaves — so the two branches share no test line and no leaf here conflicts textually.
What every leaf here inherits is that `session_spans(trace, text)` keeps its signature and
its answer: the census counts the mapper's spans, so it follows the replay rewrite without an
edit (the design says as much under Out of scope). The leaves marked *(after replay)* below
are the ones whose numbers move if that assumption breaks.

The stores the plan leans on, verified in `tests/export/conftest.py` and `tests/conftest.py`:

| fixture | holds | why this tier uses it |
| --- | --- | --- |
| `delivered_db` / `store_path` | `server_tools` + `spine`, two recorded mycelia sessions | small enough to send whole through the receiver, so a leaf can send one and census the rest |
| `exportable_db` / `counted` | every fixture transcript but `fork_byref` — including `fork_origin`'s replayed compaction | the census oracles: `MAPPING` and `live_compactions` |

## unit (delivery ledger) — `tests/export/test_otlp__delivery.py` and `tests/export/test_schema.py`, a real DuckDB store, no HTTP

- **A ledger over a store that holds no `otlp_delivery` table answers `{}` and creates
  nothing.** This is the whole reason the split is possible: the dry run opens read-only, so
  the ledger it reads must survive a missing table without DDL and without the write lock.
  *Evidence:* a new leaf, `test_a_ledger_over_a_store_with_no_table_holds_nothing`, over a
  fresh `store_path` copy opened read-only — `DeliveryLedger(connection,
  backend=GENERIC).fingerprints() == {}`, then `SELECT count(*) FROM duckdb_tables() WHERE
  table_name = 'otlp_delivery'` is still `(0,)`. Its red-check is free: today's bare `SELECT …
  FROM otlp_delivery` raises a DuckDB `CatalogException`, so a ledger that forgot the
  existence check fails on the first line
- `create()` runs `check_shape` before the DDL, and only `OtlpExporter.__init__` calls it.
  *Evidence:* `test_the_ledger_is_created_without_a_schema_bump` (unchanged — it asserts the
  table absent before an export and present after) beside the leaf above, which asserts a bare
  ledger leaves it absent. The pair is what makes "the ledger never creates the table" an
  assertion rather than a code reading
- A store whose ledger columns drifted is refused at exporter construction, naming the table
  and the column. *Evidence:*
  `test_schema.py::test_a_renamed_delivery_column_is_refused_the_same_way`, updated only for
  the new constructor (`OtlpExporter(backend, DeliveryLedger(connection, backend="test"))`).
  If `create()` were moved onto the ledger's own construction, this leaf's second
  `OtlpExporter(...)` would refuse at a different point and the leaf says so
- The ledger reads and writes per backend at `MAPPER_VERSION`, and slice 1 changes none of it.
  *Evidence:* `test_delivery_is_recorded_per_backend`,
  `test_an_unchanged_session_is_not_sent_again`,
  `test_a_changed_fingerprint_re_sends_the_whole_session` and
  `test_a_stale_mapper_version_re_sends_everything`, all unchanged in body. The only edit
  slice 1 may make to this file is the constructor in `conftest.py:deliver` and the two direct
  `OtlpExporter(backend, store, …)` calls at `test_otlp__delivery.py:124` and `:534`. A diff
  touching an assertion in this file is the slice failing its "no behaviour change" promise
- `record()` writes exactly the row the send wrote before the split. *Evidence:*
  `test_a_confirmed_session_records_one_delivery_row` and
  `test_spans_sent_counts_what_the_receiver_took`, unchanged — the second pins `spans_sent`
  against what the receiver actually decoded, so a ledger that lost the count fails there
- **The transport module still owes no session row.** The design's open question is settled by
  whether `otlp_delivery.py`'s docstring — "this module reads no session row, only the spans
  that module made and the delivery ledger it owns" — still reads true. *Evidence:* the
  docstring itself, reviewed at slice 1, plus `rg -n 'FROM (sessions|turns|api_calls|
  tool_calls|agent_runs|compactions|extract_state)' src/hyphae/export/otlp_delivery.py`
  returning nothing. A ledger that only ever names `otlp_delivery` keeps the sentence true and
  the module stays; if the implementer finds it needs a session row, the answer flips and the
  ledger moves to its own module. Naming the check is the obligation — there is nothing here
  worth a pytest leaf

## unit and integration (census exporter) — `tests/export/test_otlp__census.py`, a real store, `refresh()` driving `OtlpCensus`, two independent oracles

- **A census counts only what the send after it would ship.** The whole point of the change.
  *Evidence:* a new leaf, `test_a_census_counts_only_what_a_send_would_ship`, over a
  `store_path` copy and the `receiver`: census the store with an empty ledger and get both
  sessions; `deliver` the store once through the receiver; census again and get one session's
  spans, one skipped id, and `result.extracted == [the id the send would ship]`. Red-check
  built in — before the change the second census equals the first, and the leaf asserts they
  differ. This is the leaf the design's problem statement exists for *(after replay: the span
  totals come from `session_spans`, which the replay branch rewrites without changing its
  answer)*
- **The census total is the mapper's own answer and the store's rows agree with it.**
  *Evidence:* `test_the_census_counts_what_the_mapper_would_ship`, rewritten off the deleted
  `census_project` onto `refresh(Path(MYCELIA), extractor=StoreSource(counted),
  exporter=OtlpCensus(DeliveryLedger(counted, backend=GENERIC), text=METADATA_ONLY))` over
  `counted`, which holds no delivery rows so the whole selection is still counted. Its three
  assertions stand as written: `counts.sessions == len(shipped)`, `counts.spans ==
  sum(len(session_spans(t)) …)`, and `counts.spans == mapping_true(counted, shipped)`. The
  `MAPPING` formula keeps its own hand-written `NOT replayed` terms — per the replay plan's
  decision, the oracle must not learn `live()` *(after replay)*
- `Census.__add__` sums each field, so a census built one session at a time equals
  `census(traces)`. *Evidence:* an assertion folded into the rewritten leaf above rather than
  a leaf of its own: `sum((census([t]) for t in shipped), start=Census(0, 0, 0)) ==
  counts`, comparing the whole dataclass so a `__add__` that dropped `compactions` fails. A
  new operator with only a total-spans assertion behind it is where a mutant lives
- The empty case: a corpus with nothing left to ship censuses to zeros rather than crashing.
  *Evidence:* the send-then-census leaf, extended — deliver *both* sessions, then census and
  assert `Census(sessions=0, spans=0, compactions=0)` and two skipped ids. This is the case
  the design's CLI line was written for ("6 would ship" with no "567 unchanged" reads like a
  shrunken corpus)
- **A census posts nothing and records nothing.** *Evidence:* the same leaf, asserting
  `receiver.bodies == []` across both censuses and that `delivery_rows(connection)` is
  byte-identical either side of a census, `delivered_at` included. `OtlpCensus` never holds a
  `Backend`, so a census that tried to send has nowhere to send to — the assertion is
  cheap and it is the one that would catch a copy-paste from `OtlpExporter.export`
- The compaction term and the store agree on a fork's copy. *Evidence:*
  `test_the_compaction_term_and_the_store_agree_on_a_fork_copy`, unchanged — it calls
  `census(traces(counted))`, the pure function this design keeps *(after replay)*
- A fan-out's shared spawning call is suppressed once and each run still ships. *Evidence:*
  `test_one_call_shared_by_many_runs_is_suppressed_once`, unchanged; its `FANOUT_RUN` is
  planted and labelled as such in the file already
- The identity holds over a real corpus. *Evidence:*
  `test_the_census_holds_over_a_real_corpus`, `@pytest.mark.slow` and gated on
  `HYPHAE_CENSUS_STORE`. Run it by hand against `data/traces.duckdb` before the PR — the
  design's numbers (573 sessions, 571 `generic` rows at `MAPPER_VERSION` 1, 567 current, 6
  shipping) came from there and no fixture reproduces that ratio. Add the diff half to the
  hand-run: with `HYPHAE_CENSUS_STORE` naming a copy of the canonical store, a census through
  `refresh` against the `generic` ledger reports the design's 6 and 567. `mise run check`
  cannot run either

## integration (CLI) — `tests/export/test_otlp__cli.py`, the command as an operator runs it

- **`--dry-run` counts against the named backend's ledger and prints both numbers.**
  *Evidence:* `test_a_dry_run_counts_without_a_backend`, rewritten: keep the planted
  compaction (invented, and already labelled so in the leaf — neither fixture session
  compacted, and a zero would prove nothing about the line), then run the dry run, deliver the
  store, run it again, and compare each printed line against a `census(...)` computed in the
  test. The line is the design's:
  `"{sessions} session(s) and {spans} span(s) would ship to {backend}, {compactions} of them
  compactions; {skipped} unchanged — nothing sent"`. Today's three pins stay in the same leaf:
  no request reached the receiver, the environment held no key, and the store came away
  without an `otlp_delivery` table before the send
- **A dry run naming a backend whose key is unset counts rather than refusing, and counts that
  backend's remainder.** This is the design's stated lean on its second open question, and
  nothing today asserts it. *Evidence:* a new leaf,
  `test_a_dry_run_needs_no_key_for_a_named_backend`, under the `unconfigured` fixture: deliver
  the store to `generic` through the receiver, then `cli.main("export-otlp", MYCELIA, "--db",
  …, "--dry-run", "--backend", "honeycomb")` prints both sessions still shipping — a ledger
  keyed by the wrong backend would print zero — and raises no `SystemExit`. Its foil is
  `test_a_named_backend_refuses_without_its_key`, unchanged, where the same backend without
  `--dry-run` still refuses naming `HONEYCOMB_API_KEY`
- The dry run opens the store read-only. *Evidence:* a recording wrapper over
  `cli.open_trace_store` in the rewritten dry-run leaf, capturing the `read_only` argument —
  precedent is `test_the_delivery_flags_reach_the_exporter`, which monkeypatches
  `cli.OtlpExporter` the same way. The design collapses the two opens into one
  `read_only=args.dry_run`, so which value each mode passes is now one expression a mutant can
  flip; the no-table assertion alone would not catch a read-write open
- **A run with nowhere to ship still refuses before the store is opened.** The restructure
  moves the open above the mode branch, which is exactly where this ordering gets lost.
  *Evidence:* `test_missing_configuration_refuses_before_anything_is_read`, unchanged — it
  asserts the `SystemExit` names `OTLP_ENDPOINT`, that nothing reached the receiver, and that
  the store holds no ledger table afterwards
- Both the census and the exporter receive every flag the command parses, and the census's
  `text` is an explicit argument with no default. *Evidence:*
  `test_the_delivery_flags_reach_the_exporter`, extended with a dry-run case: a recording
  subclass of `OtlpCensus` capturing its kwargs, asserting `{"text": TextPolicy(include=True,
  max_chars=20)}` whole, so a flag the wiring drops fails here. The no-default half is a type
  obligation, not a runtime one — `mise run check`'s pyrefly pass refuses a construction that
  omits `text`
- A project the store holds nothing under stops the run on either path. *Evidence:*
  `test_a_project_the_store_holds_nothing_under_stops_the_run`, parametrized over `()` and
  `("--dry-run",)`, unchanged — `UnknownProjectError` now surfaces from inside a single `with`
  rather than two, and this leaf is what says the `SystemExit` still comes out
- The send path ships what a direct `refresh` ships. *Evidence:*
  `test_the_command_ships_what_a_refresh_ships`, body unchanged but for the exporter
  construction at line 65 taking a ledger. It compares the receiver's spans and the whole
  delivery ledger against a hand-driven `refresh`, so a CLI that rewired the send while
  rewiring the dry run fails here
- A locked store still stops the send at the open, and a failing run still prints no key.
  *Evidence:* `test_a_locked_store_stops_the_run` and `test_a_failing_run_never_prints_the_key`,
  both unchanged

## pins (schema and contracts) — `tests/export/test_schema.py` and the type checker

- The stored schema does not move, so `SCHEMA_VERSION` stands and the delivery DDL is
  byte-identical. *Evidence:*
  `test_no_owners_tables_can_change_without_the_schema_version` and
  `test_a_table_a_ddl_declares_but_the_store_lacks_is_not_drift`, both unchanged and both
  green without an edit — the ledger moves module-internally and `_DELIVERY_SCHEMA` moves with
  it. A schema-digest line in the diff is the contradiction
- `OtlpCensus` satisfies `Exporter` and `refresh()` is untouched. *Evidence:* pyrefly under
  `mise run check` (the protocol is structural, so a wrong `export` signature is a type
  error), plus `git diff src/hyphae/pipeline.py` being empty — the design promises `refresh()`
  and `RefreshResult` do not change, and every census leaf above drives the real one

## no-op guarantees — across tiers

- Nothing is layered over the deleted function. *Evidence:* `rg -n 'census_project' src tests`
  empty after slice 2; `plans/refactor-audit-2026-08-30/findings.md:117` is the only other
  mention and is history
- **The suite would notice the diff being wrong.** *Evidence:* `mise run mutate` scoped to
  `hyphae.export.otlp.*`, `hyphae.export.otlp_delivery.*` and `hyphae.cli.*`, cold and serial
  per `.claude/rules/testing.md`. The mutants that must die: `DeliveryLedger.fingerprints`
  dropping the `backend` or `mapper_version` predicate, its missing-table branch returning
  rows instead of `{}`, `Census.__add__` summing one field twice, and
  `open_trace_store(read_only=…)` taking the constant instead of `args.dry_run`. A survivor
  over the ledger predicate is a missing assertion in the per-backend leaf, not a new test
- Each slice passes `mise run check` alone. *Evidence:* a green run at each of the three code
  commits; slice 1 changes no printed line and slice 3 changes no count

## not covered, deliberately

- **Whether the backend really holds what the ledger claims.** Out of scope in the design: the
  ledger records what was acknowledged, not what a query there finds (`docs/otlp-export.md`),
  and the census inherits that. No leaf asserts a remote count
- Live delivery to a real backend. Behind the `live` marker and `HYPHAE_LIVE_OTLP`, guarded by
  the autouse `offline` fixture; `test_a_live_send_is_accepted` stands unchanged
- A census for `hp extract`. `DuckDbExporter` is the store and its refresh needs no preview
- Two connections for the two modes. The dry run opens read-only on purpose, which the
  read-only pin above asserts and nothing else needs to
- `docs/otlp-export.md` and `CONTEXT.md`. They ride slice 4 through doc-sync at PR time;
  `mise run check` reports a link or path that stops resolving, and the "Preview the exact
  export" section (`docs/otlp-export.md:5-16`) is the paragraph the change makes true

## Report

Twenty-six obligations. Verified against the working tree, all as the design claims:
`census_project` at `export/otlp.py:211-220` with the no-diff docstring and its two callers
(`cli.py:319` and `test_otlp__census.py:88`); `cli.py:_census_otlp` at 316-326, nine lines;
`test_a_dry_run_counts_without_a_backend` pinning the read-only open, the absent key and the
absent table; `otlp_delivery` created by `OtlpExporter.__init__` after `check_shape`;
`fingerprints()` selecting on `backend` and `mapper_version`; `--backend` parsed but unread on
the dry-run path; `Census` carrying `sessions`, `spans`, `compactions`; six `OtlpExporter(…)`
construction sites, five of them in tests. The design's store numbers were not re-measured —
the canonical store is private and the slow leaf is the place to quote them.

One obligation the design's seam cannot reach, not papered over: **`OtlpCensus.export()`
recording the fingerprint in memory is unobservable.** `refresh()` reads `fingerprints()` once
before its loop, so an in-memory record never changes what a pass counts or skips — no leaf at
any tier can go red on it. Either drop the in-memory dict from the design, or accept that its
only evidence is a direct unit assertion (`export()` twice, then `fingerprints()`), which
proves the code does what it says and nothing about what a run ships. I recommend dropping it:
the `Exporter` protocol asks for `export` and `fingerprints`, and delegating `fingerprints` to
the ledger satisfies it already.

Two soft spots, flagged rather than hidden. The dry run "takes no write lock" is asserted
indirectly — through the recorded `read_only=True` and the absent table — because a DuckDB
read-only open cannot proceed while another process holds the write lock, so the
`locked()`-beside-a-dry-run leaf that would prove it directly cannot exist. And the planted
compaction in the CLI leaf stays invented: neither `delivered_db` session compacted, and the
design's line prints a compaction count that a zero would not exercise.
