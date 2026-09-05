# Testing plan: let the session trace name its live rows

Obligations for `plans/trace-replay-rule/design.md` — adding `LiveRows` and
`SessionTrace.live()`, shaping `session_spans` over it, and deriving the store's `live_*`
family and predicate from it. Every repository claim the design makes was re-checked against
the working tree on 2026-09-05; the report at the bottom says which held and which did not.

The seam is the design's: the two sinks, driven by the recorded fork. Nothing here tests
`live()` in isolation — there is no `tests/test_model.py`, and a projection with no reader
proves nothing. The design's stated leans stand: option A, and the viewer's dead fact stays.

The fixture the plan leans on, re-measured by extracting `tests/fixtures/fork_origin/`
(session `5a88789c`, Claude Code 2.1.215):

| kind | rows | replayed |
| --- | --- | --- |
| turns | 2 | 1 |
| api_calls | 4 | 1 |
| tool_calls | 11 | 4 |
| compactions | 2 | 1 |
| agent_runs | 2 | — (no field) |

Four of the five kinds shrink under `live()` and `agent_runs` does not, so one recorded
session discriminates every field of `LiveRows` at once. It rides in `exportable_db`
(`tests/conftest.py:exportable_transcripts` takes every fixture directory but `fork_byref`),
so the census tier sees it too.

## unit (mapper shaping) — `tests/export/test_otlp__shaping.py`, recorded sessions in, span list out, no I/O

- **A compaction a fork replayed ships no span, and the run that recorded it ships one.**
  *Evidence:* `test_a_compaction_a_fork_replayed_ships_no_span`, unchanged. It asserts both
  copies are in the trace with `{(FORK_ORIGIN_RUN, False), (FORK_RUN, True)}`, then that the
  copy's span id is absent. If `live()` dropped the wrong compaction or none, this is red
- **A live api call under a replayed turn hangs off its run, not off a span that never
  shipped.** *Evidence:* `test_a_live_call_under_a_replayed_turn_hangs_off_its_run`,
  unchanged. This is the one leaf pinning `_chat_parent`'s surviving read of `turn.replayed`:
  the parent lookup goes through `turns` (every row the trace holds, including the copy), and
  the fixture's two live `FORK_RUN` calls sit under a turn `live()` drops. Rewriting the
  lookup over `live.turns` makes it red
- A replayed turn and a replayed tool call ship no span. *Evidence:* **already discharged
  directly**, contrary to this plan's reading — `tests/export/test_otlp.py:154
  test_a_forks_copies_never_become_spans` asserts span-key disjointness per kind over all
  seven of `fork_origin`'s copies, then counts what is left. The audit looked only in
  `test_otlp__shaping.py` and missed it, so the coverage seam described here and in the report
  below does not exist, and the census is not the only carrier. No leaf was added. Confirmed
  by red-check: a `live()` that filters nothing reds this leaf
- A tool call that spawned a run ships as the run rather than as a call of its own, and the
  suppression is computed over live rows. *Evidence:*
  `test_a_matched_tool_call_becomes_the_run_it_spawned` (spine) and
  `test_a_fork_spawned_inside_its_own_transcript_hangs_off_the_run_it_continues`
  (`fork_origin`), both unchanged. The fork's spawning call
  `toolu_012WL3dJAxjaVtQgcCsKFUpC` is live, so the rewrite from `live_tools` to
  `live.tool_calls` must keep placing it
- **A replayed copy of a spawning tool call is not read as a spawn.** *Evidence:* no recorded
  fixture holds one — every `tool_use_id` a `fork_origin` run names matches a live call, and
  the four replayed calls match none. Discharge it the way `test_otlp__census.py` discharges
  the fan-out (`FANOUT_RUN`): flip one matching call's `replayed` to `True` on a
  `dataclasses.replace`d trace and assert the run still ships and the copy still ships
  nothing. Invented data, and labelled as such in the test — the shape is the whole point
- A run naming a call this trace never held hangs off the root, carrying the id. *Evidence:*
  `test_a_run_naming_a_call_this_trace_never_held_hangs_off_the_root`, unchanged. It reads
  `trace.tool_calls`, not `live()`, which keeps "absent" and "replayed" told apart
- **A call whose turn the trace does not hold crashes with `UnparentedCallError`.**
  *Evidence:* no test names that exception today (`rg -n 'UnparentedCallError' tests/` is
  empty), yet the design's `_chat_parent` decision turns on it. Add a leaf: a `fork_origin`
  trace with one turn removed by `dataclasses.replace`, asserting the raise and that the
  message names the call. Planted removal over a recorded session, the smallest invention
  that reaches the branch

## integration (census) — `tests/export/test_otlp__census.py`, real store over the fixture corpus, SQL oracle spelling the rule its own way

- **The span total a dry run quotes is the total a send puts on the wire, and equals what the
  store's rows say through `MAPPING`.** *Evidence:*
  `test_the_census_counts_what_the_mapper_would_ship`, unchanged and run against the rewritten
  mapper. `MAPPING` keeps its five hand-written `NOT replayed` terms, per the design's
  decision — the oracle must not learn `live()`, or the test agrees with itself
- The compaction term and the store agree on a fork's copy. *Evidence:*
  `test_the_compaction_term_and_the_store_agree_on_a_fork_copy`, unchanged: it asserts
  `census(...).compactions == count(*) FROM live_compactions`, which is exactly the
  trace-side and store-side identity this design generalizes
- A fan-out's one shared call is suppressed once and each run still ships. *Evidence:*
  `test_one_call_shared_by_many_runs_is_suppressed_once`, unchanged; its planted `FANOUT_RUN`
  is the precedent for the planted leaves above
- The identity holds over a real corpus, not just 16 fixtures. *Evidence:*
  `test_the_census_holds_over_a_real_corpus`, `@pytest.mark.slow` and gated on the store env
  var. Run it by hand once against the canonical store before the PR — the design's own
  numbers (replays in 9 of 630 sessions, 481 live calls under replayed turns) came from there,
  and no fixture reproduces that density. `mise run check` cannot run it

## integration (store views) — `tests/export/test_duckdb.py`, real DuckDB file, rows written by the real exporter

- **The live views hold exactly what the trace calls live, for every field of `LiveRows`.**
  *Evidence:* the new
  `test_the_live_views_hold_what_the_trace_calls_live`, parametrized over
  `dataclasses.fields(LiveRows)`, exporting `fork_origin` and comparing
  `len(getattr(trace.live(), name))` against
  `SELECT count(*) FROM live_<name> WHERE session_id = ?`. Its red-check is in the same
  fixture: assert alongside that the four flagged kinds are strictly smaller than their base
  tables (2/1, 4/3, 11/7, 2/1) so a `live()` that filtered nothing and a view that filtered
  nothing fail together rather than agreeing on the wrong number
- `agent_runs` gets no predicate, because `AgentRun` declares no `replayed` field (verified:
  `src/hyphae/model.py:171`). *Evidence:* the same parametrized leaf's `agent_runs` case,
  where live equals base at 2; and a `WHERE NOT replayed` added there is a DuckDB binder error
  at the first `refresh_views`, which every leaf in this file would report
- A session's rollup counts a fork's copied history once. *Evidence:*
  `test_a_rollup_counts_replayed_work_once`, unchanged — its `(3, 6050, 1)` is the number that
  moves if the derived family or predicate comes out wrong for `api_calls` or `compactions`,
  and it also asserts the base tables still hold both copies, which is the design's
  "projection, not partition" contract
- A resumed session's corpus rollup is untouched. *Evidence:*
  `test_a_corpus_rollup_counts_a_resumed_session_once`, unchanged — `_corpus_view` reads the
  `live_*` family by name, so a renamed or missing view surfaces here as well
- A view definition edited in code reaches the next reader with no re-extract. *Evidence:*
  `test_a_view_definition_reaches_a_reader_without_a_re_extract`, unchanged. It plants a
  stale `live_turns` and reads through both the view and `session_rollups`; deriving the
  family from `LiveRows` must not change when the statements are built

## pins (schema and names) — `tests/export/test_schema.py`, DDL and dataclasses, no store

- **Every `LiveRows` field names a `TABLES` entry whose `model` is that field's row type.**
  *Evidence:* the new pin, parametrized over `fields(LiveRows)`, reading the list's element
  type off the annotation and comparing it with `TABLES[name].model`. Renaming
  `LiveRows.tool_calls` or repointing a `TableSpec` fails here rather than as a `KeyError`
  inside `refresh_views` at the first open — which is the whole reason the design couples the
  two by name
- The stored schema does not move, so `SCHEMA_VERSION` stands. *Evidence:*
  `test_no_owners_tables_can_change_without_the_schema_version` and
  `test_a_tables_ddl_columns_are_exactly_its_models_fields`, both unchanged and both green
  without an edit — the design adds no column and `LiveRows` is not a table
- `mise run check`'s schema digest is unchanged. *Evidence:* a green `mise run check` on each
  slice; the design promises byte-identical rows, so a digest line in the diff is the
  contradiction

## no-op guarantees — across tiers

- The trace still round-trips through the store whole, so `StoreSource` and every fixture
  factory are untouched by a method added to `SessionTrace`. *Evidence:*
  `test_a_trace_round_trips`, unchanged
- No re-extraction is triggered. *Evidence:* `EXTRACTOR_VERSION` is absent from the diff, and
  `tests/extract/` runs unchanged. A bump would re-extract 630 sessions to store identical
  rows
- **The suite would notice `live()` or the predicate being wrong.** *Evidence:*
  `mise run mutate`, cold and serial per `.claude/rules/testing.md`, **for the two exporter
  scopes only**. `hyphae.model.*` is unreachable: mutmut skips every method of a decorated
  class (`mutmut/mutation/file_mutation.py:292`), `SessionTrace` is a `@dataclass`, so
  `mutants/src/hyphae/model.py` holds no mutant of `live()` and a scoped run exits 1 with
  "nothing matches". Substituted by hand red-checks, each planted then reverted, recorded in
  the PR body: `live()` filtering nothing (reds 5 leaves), `spawns` built over
  `trace.tool_calls` (reds only the planted spawn leaf), `turns` indexed over `live.turns`
  (reds 15), `_live_view` emitting the empty predicate (reds 7, including four parity cases),
  the predicate forced onto `agent_runs` (binder error across the file), and a member dropped
  from the derived family (catalog error across the file). A survivor over the derived
  predicate is a missing assertion in the parametrized parity leaf, not a new test
- Slice independence: slice 1 changes no SQL and slice 2 changes no shaping. *Evidence:*
  `mise run check` green at each commit, and `rg -n '\.replayed' src/hyphae/export/otlp.py`
  returning only `_chat_parent`'s read after slice 1

## not covered, deliberately

- **The viewer's dead "Replayed" fact.** Verified: `view_turn_header.sql:41` reads
  `FROM live_turns` and line 35 selects `t.replayed`, so `facts.replayed`
  (`view/pages/node/markup/body.py:353`) is false on every page ever rendered. Per the
  design's stated lean it is not removed in this PR, so no leaf here asserts anything about
  it — and none should assert it renders `True`, which nothing can produce
- `corpus_*` and `first_seen`. The cross-session resume rule needs two sessions and stays
  SQL; `test_a_corpus_rollup_counts_a_resumed_session_once` covers it as it stands
- Migration 9's back-fill (`_flag_the_compactions_a_fork_replayed`). Frozen history for stores
  at schema 8; `tests/export/test_duckdb__migrations.py` owns it and is untouched
- Option C's question — whether the store should stop holding a fork's copies. That is a
  product decision, not an obligation; the leaves above deliberately assert the copies *stay*
  in the base tables, so flipping to C later fails them loudly rather than quietly
- Live delivery to a telemetry backend. Behind its marker and env var as before; the mapper is
  what changed, not the wire

## Report

Verified against the working tree, all as the design claims: the four inline filters and
`live_tools` at `export/otlp.py:138-155`; `_COUNTED` and `_live_view` at
`export/duckdb.py:204-219`, read by `refresh_views` at lines 311-312; `AgentRun` carrying no
`replayed` field; `TABLES` holding a `TableSpec.model` per table; the census `MAPPING`
spelling the rule independently; `rg '_COUNTED|live_tools' tests/` empty, so no test is
layered over the old shape; and the dead viewer fact. The design's fixture and store numbers
were not re-measured against the canonical store — the fixture ones were, and are in the table
above.

Two obligations the design's seam reaches only with planted data, both flagged on their leaves
and neither papered over:

- **`UnparentedCallError` has no test at all today.** The design's `_chat_parent` decision
  ("rejected looking the turn up in `live.turns`… loses `UnparentedCallError`") rests on a
  branch nothing exercises. A recorded fixture cannot produce it — a real session's calls name
  turns the session holds — so the leaf plants the absence over `fork_origin`
- **No recorded fixture holds a replayed tool call that a run names as its spawn**, which is
  the case the current `live_tools`-before-`spawns` ordering exists for. The design's rewrite
  preserves the ordering; only a planted flag flip can prove it, following the census's
  `FANOUT_RUN` precedent

One design claim to soften rather than a contradiction: the plan's third shaping leaf — a
replayed turn and a replayed tool call ship no span — has no leaf of its own at the shaping
level; it is carried by the census total, where a lost filter shows up as five spans rather
than as a named failure. That is weaker evidence than the compaction leaf gives, and the
implementer may fold a per-kind span-set assertion for `fork_origin` into the shaping file if
the census leaf reads as too indirect.

No collision with `plans/one-price-table/` or `plans/enrichment-stamp/`: those touch
`enrich/`, `extract/pricing.py`, `cli.py` and their tests. The only shared file is
`CONTEXT.md`, one glossary line each.
