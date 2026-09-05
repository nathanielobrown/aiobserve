# Design: give the enrichment stamp a module

Audit items S27 (landed), S28 (landed), C26; architecture review of 2026-09-03, candidate 2.

## Problem

`docs/enrichment.md` states one rule — four values decide whether a row is stale — and no module owns it. Minting a stamp, comparing it, writing it and reading it back is spread over five files (verified 2026-09-03):

- `src/hyphae/enrich/enricher.py:_plan_level` mints `Stamp(input_hash(rendered), LEVELS[level].prompt_version, TAXONOMY_VERSION, model)` — the only place the four are put together
- `src/hyphae/enrich/store.py` declares `Stamp`, compares in `stale_keys`, reads in `_stamps` with a hand-listed `SELECT input_hash, prompt_version, taxonomy_version, model`, and binds the same four positionally in `upsert`
- `src/hyphae/enrich/prompts.py:input_hash` holds the hash beside prompt text it has nothing to do with
- `src/hyphae/view/enrichment.py:Enrichment.stale` restates the version half of the rule from `LEVELS` and `TAXONOMY_VERSION` directly, so the viewer and the enricher can disagree on what "stale" means
- The payload column list is spelled in `store.py` five times: the DDL, the three `enriched_*` views, `_PAYLOAD_COLUMNS` (C26), plus the stamp's four in `_stamps`

Deletion test: no single deletion makes the concept vanish. The symptom in the suite: `tests/enrich/test_enricher.py:198,211` reach the version branch only by monkeypatching `LEVELS` and `hyphae.enrich.enricher.TAXONOMY_VERSION`.

The card was written before S27/S28 landed. `LevelSpec.prompt_version` (`enrich/levels.py`) and `item_key` (`enrich/items.py`) already exist, so "item key ×1" is done and the prompt version's *declaration* stays where it is. The card's second consumer is the viewer, not the CLI: `cli.py` never touches a stamp.

This design reads no Claude Code field. The stamp is ours, so no recorded session is at stake.

## Call paths, current → proposed

Current: `cli._enrich` → `enricher.enrich(store, client)` → `_pass(store, client.model)` → `_plan_level` mints `store.Stamp` from `prompts.input_hash`, `LEVELS[...]`, `TAXONOMY_VERSION` → `store.stale_keys(level, planned)` reads `_stamps` and compares → `store.upsert(item, enrichment, stamp)`. Beside it, `view/enrichment.described` builds `Enrichment` rows whose `stale` property re-derives the version half.

Proposed: `cli._enrich` builds `Versions.current()` once → `enricher.enrich(store, client, versions=...)` → `_plan_level(store, versions, model, level)` calls `versions.stamp(level, rendered, model)` → `stamp.stale(planned, store.stamps(level))` → `store.upsert(item, enrichment, stamp)`. The viewer's `Enrichment.stale` asks `Versions.current().moved_past(level, prompt_version=..., taxonomy_version=...)`. `plan` (the dry run) takes the same `versions`, so a quote and a pass judge staleness by one object.

After the change the grep `rg -n 'TAXONOMY_VERSION|\.prompt_version' src/hyphae` names only the two declarations (`taxonomy.py`, `levels.py`), the one reader (`stamp.py`), and comments.

## File-tree diff

```
src/hyphae/enrich/stamp.py        + Stamp, COLUMNS, input_hash (moved), Versions, stale
src/hyphae/enrich/store.py        ~ Stamp and stale_keys leave; _stamps becomes stamps(level); PAYLOAD_COLUMNS derived; the three views project from it
src/hyphae/enrich/prompts.py      ~ input_hash leaves
src/hyphae/enrich/enricher.py     ~ enrich/plan take versions; _plan_level mints through it; no LEVELS-for-version, TAXONOMY_VERSION or input_hash import
src/hyphae/cli.py                 ~ _enrich passes Versions.current()
src/hyphae/view/enrichment.py     ~ stale via Versions.moved_past; TAXONOMY_VERSION import gone (LEVELS stays, for TABLES)
tests/enrich/test_stamp.py        + stale() on maps; Versions.stamp; moved_past
tests/enrich/test_enricher.py     ~ bump tests pass a Versions; monkeypatches deleted
tests/enrich/test_store.py        ~ stale_keys tests drive stale(planned, store.stamps()); four-field pin replaced by the DDL parity test
tests/enrich/conftest.py, items.py, passes.py  ~ import Stamp from stamp
docs/enrichment.md                ~ the staleness section names the module
CONTEXT.md                        ~ Stamp line (below)
```

## Key contracts

```python
# src/hyphae/enrich/stamp.py
@dataclass(frozen=True)
class Stamp:                      # unchanged fields, moved from store.py
    input_hash: str
    prompt_version: int
    taxonomy_version: int
    model: str

# The stamp's columns in field order: every writer binds astuple(stamp) against this, every reader unpacks Stamp(*row) from it.
COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(Stamp))

def input_hash(rendered: str) -> str          # moved verbatim from prompts.py

@dataclass(frozen=True)
class Versions:
    """The half of the stamp the code decides. A pass adds the hash and the model; a reader with no pass in hand can still judge a row against this half."""
    prompt: Mapping[Level, int]
    taxonomy: int

    @classmethod
    def current(cls) -> "Versions"            # the one read of LEVELS[*].prompt_version and TAXONOMY_VERSION
    def stamp(self, level: Level, rendered: str, model: str) -> Stamp
    def moved_past(self, level: Level, *, prompt_version: int, taxonomy_version: int) -> bool
        """Whether this build has moved past a row's versions — two of the four axes; the hash needs a render and the model a pass."""

def stale(planned: Mapping[str, Stamp], held: Mapping[str, Stamp]) -> list[str]
    """The planned keys whose held stamp is not the planned one. No row counts as not the one."""
```

```python
# src/hyphae/enrich/enricher.py
def enrich(store, client, *, versions: Versions, project=None, limit=None) -> EnrichReport
def plan(store, model, *, versions: Versions, project, limit) -> list[PlannedItem]

# src/hyphae/enrich/store.py
def stamps(self, level: Level) -> dict[str, Stamp]    # replaces stale_keys and _stamps: a read, no comparison
PAYLOAD_COLUMNS = (*(f.name for f in fields(Enrichment)), *stamp.COLUMNS, "enriched_at")
```

`versions` is a required keyword: the CLI has one right answer (`Versions.current()`), a test has another, and neither should get it by omission. The model stays on the client, so `versions` cannot disagree with `client.model`.

The DDL stays hand-written — its column comments are worth more than a generated body (the S24 reasoning). What derives is everything that repeats it: the three views' `e.…` projection with `model AS enrichment_model` named once, the `stamps` SELECT, and the `upsert` binding. No schema change; the tables' columns are the same bytes.

## Chosen test seam

Tests drive the deepened interfaces and nothing under them:

- `enrich(store, FakeClient(), versions=replace(current, prompt={**current.prompt, Level.turn: 99}))` over the real `spine/` store replaces the two monkeypatch tests, at the level they already run (`tests/enrich/test_enricher.py`)
- `stale()` is tested on plain maps in `tests/enrich/test_stamp.py`: one field moved, a missing row, an identical stamp
- The store keeps one round trip: `upsert` then `stamps(level)` returns what was written, and `stale(planned, store.stamps(level))` names the mutated row — the existing parametrized four-field test, recomposed
- Parity: `declared_shape(store._SCHEMA)[spec.table] == set(spec.keys) | set(PAYLOAD_COLUMNS)` for every `LevelSpec`, via `export/schema.py:declared_shape`. This replaces `test_stamp_is_the_four_field_staleness_key`: a fifth stamp field now fails here until the DDL carries it, which is the guard that test wanted
- The viewer keeps `tests/view/test_enrichment.py::test_an_item_described_under_an_older_prompt_is_marked_stale` as is; its `wrote()` oracle still reads `LEVELS` and `TAXONOMY_VERSION` directly, on purpose — an oracle that called `moved_past` would test the code against itself

## Slices

1. **The module and the seam.** Add `enrich/stamp.py` (moves plus `Versions`, `stale`); `enrich`/`plan` take `versions`; `cli._enrich` passes `Versions.current()`; `store.stale_keys`/`_stamps` become `stamps`; tests recomposed as above, monkeypatches deleted, `test_stamp.py` added. Verify: `mise run check`, and the grep in "Call paths" returns only declarations, `stamp.py`, `view/enrichment.py` and comments
2. **One column list.** `PAYLOAD_COLUMNS` derived; the three views project from it; `stamps` and `upsert` bind through `COLUMNS`/`astuple`; the parity test replaces the four-field pin. Verify: `mise run check` (the store round-trip tests and `tests/analyze/test_enrichment.py`, which reads the views' columns by name, both pass unchanged)
3. **The viewer asks the module.** `Enrichment.stale` calls `Versions.current().moved_past(...)`; the `TAXONOMY_VERSION` import leaves `view/enrichment.py`. Verify: `mise run check`; the grep now returns only declarations, `stamp.py` and comments
4. **Docs.** `docs/enrichment.md` "Four values decide whether a row is stale" names `src/hyphae/enrich/stamp.py` as the owner; `CONTEXT.md` line below. Verify: `mise run check` (aigarden link check)

## Decisions

- **A new module `enrich/stamp.py`, not more of `levels.py`** — the stamp spans levels (taxonomy, model, hash); putting it on `LevelSpec` would make the level registry import the taxonomy and the hash, and the viewer would keep reading versions off `LEVELS`
- **`Versions` carries the code's half only; the model rides on the call** — rejected a `Stamper(versions, model)`: the model already lives on `client.model`, and a second copy is a way for a quote and a pass to disagree
- **`stale` is a module function over two maps; the store only reads** — rejected keeping `stale_keys` on the store delegating to `stamp.stale`: one more hop, and the store would own a comparison it does not define
- **`moved_past` lives in the stamp module** — rejected leaving the viewer its two-line compare against `Versions.current()`: that is still the viewer spelling the rule; the partial rule belongs beside the full one with the docstring that says why it is partial
- **Hand-written DDL, derived projections** — rejected generating the DDL from `PAYLOAD_COLUMNS`: it loses the column comments (S24)
- **`Versions.current()` built per call, not cached at import** — three dict entries; a module-level snapshot is hidden state for nothing
- **Replace the four-field pin with the DDL parity test** — rejected keeping both: they guard the same drift, and the parity test names the table that must change

## Out of scope

- Adding `input_hash` or the uncut `model` to `view_enrichment.sql` so the viewer could hold a whole `Stamp` — a reader still cannot know today's hash or the pass's model, so the row would carry two fields nothing judges
- S29 (dry run as the real run), S31, C29, C30 — beside this code but their own changes; S29 already landed in `_pass`
- A staleness readout in `hp enrich --dry-run` per axis (how many rows are stale by hash vs version) — useful, separate
- Bumping any version. `prompt_version` stays declared on `LevelSpec`, `TAXONOMY_VERSION` in `taxonomy.py`; the bump instructions in `docs/enrichment.md` do not move

## Open questions

- Should `Versions.prompt` be `Mapping[Level, int]` or should `Versions` hold a `LevelSpec` map and read `.prompt_version` itself? The mapping is smaller and keeps the viewer off `LevelSpec`; the alternative would let a test bump a level with `replace(spec, prompt_version=99)` as today. I chose the mapping; a reviewer who wants the test to read like today's may prefer the spec
- Whether `PAYLOAD_COLUMNS` and `_SCHEMA` become public for the parity test, or the test lives beside the store and reads the underscored names as the export tests do. I lean public for the tuple, underscored for the DDL

## Glossary changes

Change the `CONTEXT.md` line for **Stamp** to:

> - **Stamp** — the input hash, versions and model a row was written under; a mismatch with today's is what `stale` means

Today's line omits the model, which is the fourth axis.
