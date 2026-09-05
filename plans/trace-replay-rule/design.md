# Design: let the session trace name its live rows

Give `SessionTrace` one answer to "which rows count", so the OTLP mapper and the store's `live_*` views both derive from the model instead of each spelling the replay rule again. Refactor-audit item M2 (`plans/refactor-audit-2026-08-30/findings.md`) has landed since the card was written: `Compaction.replayed` exists, `_COUNTED["compactions"]` is `True`, and the census/`live_compactions` parity is a test, not a doc promise. This design covers what M2 left: the filters the mapper still writes by hand, and the registry the store keeps by hand.

Designed against `tests/fixtures/fork_origin/` (session `5a88789c`, Claude Code 2.1.215 — the copied-history fork with a borrowed compaction) and the canonical store probed read-only on 2026-09-03: 630 sessions; replays in 9 of them across 32 fork threads — 28 of 4,694 turns, 367 of 182,424 api calls, 514 of 214,424 tool calls, 4 of 1,506 compactions; 481 live api calls sit under a replayed turn; no `main` thread holds a replay.

## Problem

The rule that decides which lines are a fork's copies is written once, in `extract/replays.py:replayed_lines`, and lands on the model as `replayed: bool` on `Turn`, `ApiCall`, `ToolCall` and `Compaction`. The *consequence* of the flag — count and ship only the rows that aren't copies — has no home. `SessionTrace` holds every row and says nothing about which ones count, so each sink re-derives it:

- `export/otlp.py:session_spans` filters four comprehensions on `not x.replayed` and builds a `live_tools` list to filter a fifth time (lines 138–155)
- `export/duckdb.py:_COUNTED` is a hand-kept table-name → bool registry, and `_live_view` writes `WHERE NOT replayed` from it (lines 204–219)
- `export/schema.py:_flag_the_compactions_a_fork_replayed` back-fills the flag from `agent_runs` timing; frozen migration history, not a live spelling

The deletion test: none of the mapper's filters can go today, because the trace offers nothing to replace them. A third exporter would write a sixth copy. The constraint that decides the shape: the store keeps the copies (`docs/store.md`: "The tables hold what each file recorded, replays and resume copies included"; CONTEXT.md *Replay*: "kept in the store, excluded from the corpus"), so the trace must keep carrying every row and add a projection, not drop rows.

## Options

Blast radius: this touches `model.py`, which CLAUDE.md calls foundation-shaping, so the fork is laid out rather than decided in passing.

**A. A `live()` projection on the trace (recommended).** Keep the per-row flag and every row. Add `LiveRows` and `SessionTrace.live()`, which returns the rows no fork copied plus every agent run. The mapper shapes `trace.live()`; the store derives its `live_*` family and each view's predicate from `LiveRows` and the row types. Stored schema unchanged. No `EXTRACTOR_VERSION` or `SCHEMA_VERSION` bump: the flag's values don't move, and `live()` derives from them at read time.

**B. Split the trace into `live` and `replays` containers and drop the flag from the row types.** Zero filters anywhere. Rejected: the model stops mirroring the tables (`TableSpec`, `_insert`, `StoreSource` and `test_a_tables_ddl_columns_are_exactly_its_models_fields` all lean on `fields(model)` == columns), so every adapter grows a special case to move one bit from a container position into a column and back.

**C. Drop replays from the normalized model and tables; keep them only in `raw_records`.** Deletes the flag, `_COUNTED`, `_live_view` and the `live_*` layer. Rejected: it changes the stored schema (four columns, a row-deleting migration, `SCHEMA_VERSION` 10) and bends the *Replay* definition to buy what the views already give; the 481 live calls under replayed turns would name a `turn_id` the store no longer holds; and over two hundred `live_*`/`corpus_*` reads (`rg -c 'live_|corpus_' src/hyphae`) would rename or read through an alias that lies. Worth reopening only if the store should stop holding what a fork's file recorded — see Open questions.

**D. Do less: derive `_COUNTED`'s booleans from the models and stop.** Rejected as the whole answer because the mapper's filters — the only duplication with a live bug surface — stay. It's slice 2 of option A on its own.

## Call paths, current → proposed

Current, OTLP: `OtlpExporter.export(trace)` → `session_spans(trace, text)` filters `trace.turns`, `trace.api_calls`, `trace.tool_calls`, `trace.compactions` on `replayed` inline, and `_chat_parent` reads `turn.replayed` to pick a live call's parent.

Current, store: `refresh_views(connection)` → `_live_view(table, replayed, view)` per entry of `_COUNTED` → `live_<table>` with or without `WHERE NOT replayed`; `_corpus_view` and `_rollup_view` read those.

Proposed, OTLP: `session_spans` opens with `live = trace.live()` and shapes `live.turns`, `live.api_calls`, `live.tool_calls`, `live.agent_runs`, `live.compactions`. The `launched`/`spawns` suppression reads `live.tool_calls`. `_chat_parent` is unchanged: it looks the turn up in every turn the trace holds — a missing one is still drift and still crashes — and reads `turn.replayed` to fall back to the source parent. That is the one read of the flag left in the module, and it isn't a filter: it needs the copied row to tell a copy from an absence.

Proposed, store: `refresh_views` iterates `fields(LiveRows)`; for each name it takes `TABLES[name].model` and adds `WHERE NOT replayed` when that model has a `replayed` field. `_COUNTED` is deleted. `_corpus_view`, `_rollup_view`, `first_seen` and every reader of a `live_*` or `corpus_*` view are untouched.

Extraction is untouched: `claude_code.py:extract` builds the trace the same way and `transcript.py:parse` sets the flag the same way.

## File-tree diff

```
src/hyphae/
  model.py               CHANGED  LiveRows; SessionTrace.live()
  export/otlp.py         CHANGED  session_spans shapes trace.live(); four inline filters and live_tools deleted
  export/duckdb.py       CHANGED  live-view family and predicate derived from LiveRows and TABLES; _COUNTED deleted
tests/
  export/test_duckdb.py  CHANGED  one parity test: len(trace.live().<name>) == count(live_<name>) per LiveRows field
  export/test_schema.py  CHANGED  every LiveRows field names a TABLES entry whose model is the list's row type
docs/store.md            CHANGED  the views paragraph: the family comes from the model
docs/otlp-export.md      CHANGED  the census sentence: the mapper ships the trace's live rows
CONTEXT.md               CHANGED  one line, under Pipeline (see Glossary changes)
```

## Key contracts

```python
# model.py
@dataclass(frozen=True)
class LiveRows:
    """The rows a sink counts or ships: every threaded row no fork copied, plus every agent run.

    Field names are the store's table names — `refresh_views` builds `live_<field>` from them.
    A run has no `replayed`: it is described by its own pair of files, which no fork copies.
    """
    turns: list[Turn]
    api_calls: list[ApiCall]
    tool_calls: list[ToolCall]
    agent_runs: list[AgentRun]
    compactions: list[Compaction]

class SessionTrace:
    def live(self) -> LiveRows:
        """What this session's files recorded minus every row a fork copied from another.

        The same set `live_*` holds for this session in the store; `session_rollups` counts it.
        """
```

`SessionTrace`'s fields don't change, so `StoreSource`'s whole-object round trip and every fixture factory keep working. `live()` is a method, not a field: a field would be a second copy of the rows the exporter has to write or the source has to rebuild, and it would need the exporter to know to skip it.

The `Exporter` and `Extractor` seams in `pipeline.py` don't change. The stored schema doesn't change; `mise run check`'s schema digest holds.

## Chosen test seam

The two sinks, driven by the recorded fork. The contract under test is "the trace's live rows are the store's live rows are the spans that ship", so the tests sit where each sink reads the trace:

- OTLP: `tests/export/test_otlp__shaping.py::test_a_compaction_a_fork_replayed_ships_no_span` and `::test_a_live_call_under_a_replayed_turn_hangs_off_its_run`, and every leaf of `tests/export/test_otlp__census.py`, run unchanged — they already assert what ships and what doesn't through `session_spans`. Their setup reads `.replayed` per row to find the fixture's copies, which is fine: the test is entitled to the store's flag
- Store: a new `tests/export/test_duckdb.py::test_the_live_views_hold_what_the_trace_calls_live`, parametrized over `fields(LiveRows)`, exports `fork_origin` and asserts `len(getattr(trace.live(), name))` equals `SELECT count(*) FROM live_<name> WHERE session_id = ?`, and that at least one kind differs from its base table so the leaf can go red. This is the census promise generalized from compactions to every kind
- `tests/export/test_schema.py` gains the pin that each `LiveRows` field names a `TABLES` entry — a rename on one side fails there rather than as a `KeyError` inside `refresh_views` at the first open

No test is layered over the old shape: nothing pins `_COUNTED` or `live_tools` today (`rg '_COUNTED|live_tools' tests/` is empty), so nothing is deleted.

## Slices

1. **Model and mapper.** Add `LiveRows` and `SessionTrace.live()`; rewrite `session_spans` over `trace.live()`; delete the four filters and `live_tools`. Verified by `mise run check`: the shaping and census leaves above are the behavior, and `rg -n '\.replayed' src/hyphae/export/otlp.py` returns only `_chat_parent`'s read. One commit
2. **Store derives its family.** `refresh_views` iterates `fields(LiveRows)` and reads the predicate off `TABLES[name].model`; delete `_COUNTED`. Add the parity test and the `test_schema.py` pin. `docs/store.md`, `docs/otlp-export.md` and the CONTEXT.md line ride here, since this slice is what makes the sentences true. Verified by `mise run check` (the new parity test, plus `test_a_rollup_counts_replayed_work_once`, which would go red if the family or a predicate came out wrong). One commit

Each slice passes `mise run check` alone; slice 1 changes no SQL and slice 2 changes no shaping.

## Decisions

- **Projection, not partition** (A over B and C): the store's contract is to keep the copies, so the model keeps the rows and grows a view of them. Rejected: containers without a flag; dropping the rows
- **`live()` is a method** — rejected `functools.cached_property` (works on a frozen dataclass by writing `__dict__`, but only by accident of no `__slots__`, and a reader has to know that) and an eager field (a second copy the exporter must skip). The mapper calls it once per session; the list builds cost nothing beside protobuf encoding
- **`LiveRows` carries `agent_runs`** — rejected four fields only. The fact that runs have no replays is a fact about the model, and the store's `_COUNTED["agent_runs"] = False` was the one place it lived; now the model says it and both sinks inherit it
- **The store derives both the family and the predicate from the model** — rejected keeping the hand tuple of table names and deriving only the booleans. The field names of `LiveRows` are the family; a pin in `test_schema.py` keeps the name coupling honest
- **`_chat_parent` keeps reading `turn.replayed`** — rejected looking the turn up in `live.turns` and treating absence as "replayed": that collapses a copy and a drift crash into one branch and loses `UnparentedCallError`
- **No version bump** — rejected bumping `EXTRACTOR_VERSION` out of caution. Nothing the extractor writes changes, so a bump would re-extract 630 sessions to store identical rows
- **The census's SQL oracle `MAPPING` keeps spelling `NOT replayed`** — an independent oracle is supposed to spell the rule its own way (`tests/export/test_otlp__census.py`); rewriting it over `live()` would make the test agree with itself

## Out of scope

- **The viewer's dead "Replayed" fact.** `view_turn_header.sql` reads `FROM live_turns` and selects `t.replayed`, so `facts.replayed` in `view/pages/node/markup/body.py` is false on every page ever rendered. Deleting the fact, the label and the column from the query is a viewer change with its own gallery evidence; it doesn't move the rule and shouldn't ride this branch
- **`corpus_*` and `first_seen`**: the cross-session resume rule needs two sessions and stays SQL. A trace knows one session
- **Migration 9's back-fill**: frozen history for stores at schema 8; it stays as written
- **`extract/agent_runs.py`'s use of `replays`** to time a fork's own start: that is the extractor consuming the line-level rule before the model exists, not a sink re-deriving it
- Any change to what ships or what the store holds. Every span, every row and every rollup is byte-identical before and after; the change is where the rule is read from

## Open questions

- **Should the store stop holding a fork's copies at all (option C)?** Nothing reads a replayed row today — every `src/hyphae` reader goes through `live_*`/`corpus_*` (`rg -n 'FROM (turns|api_calls|tool_calls|compactions)\b' src/hyphae` finds nothing; only test oracles name the base tables). The rows are kept for the viewer to one day show a fork's file whole. If Nathaniel doesn't want that surface, C becomes the stronger cut and this design is its first half. What settles it: a decision on whether the viewer will ever render a thread's inherited prefix
- **Should the dead fact be removed in the same PR?** It's a one-query, one-component deletion, but it needs a scenario in `tests/view/scenarios.py` to prove the page still renders. I left it out so the PR stays one rule; say so if it should ride along

## Glossary changes

Add under **Pipeline**, after *Corpus*, in `CONTEXT.md`:

```
- **Live** — a row no fork copied, plus every agent run: what a sink counts or ships; the trace's `live()` and the store's `live_*` views name the same rows
```

*Replay* and *Corpus* stand as written.
