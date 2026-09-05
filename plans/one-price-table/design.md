# Design: one price table

`hp enrich` quotes a pass from the table that prices every api call, and `--model` is checked against that table before a run renders anything. Audit items C10 and C28 in `plans/refactor-audit-2026-08-30/findings.md`: C10's merge inside `pricing.py` landed in `04994a7`; this finishes it.

## Problem

Two modules state what a model charges. `src/hyphae/extract/pricing.py:MODELS` prices eight models as `ModelSpec(input, output, context_window)`; `src/hyphae/enrich/cost.py:PRICES` prices two as `Rates(input_usd, output_usd)`, a second type for the same two numbers. Haiku 4.5 is in both; `claude-sonnet-4-5-20250929` is only in `PRICES`. Nothing holds the tables to each other, and their dates already differ (2026-08-30 against 2026-08-07).

The deletion test picks the survivor. Delete `PRICES` and the estimate asks `MODELS`, which already carries every number the quote needs: the quote is list price on input and output, prices caching at zero and applies no batch discount (`docs/enrichment.md`, "A dry run quotes money"), so `ModelSpec` needs no new field. Delete `MODELS` and the extract, the popover and the analyze `context_window` macro lose their source.

One table also gives away the list of models a person may spend on. Today a typo in `--model` reaches `claude -p` and fails there after the breaker cycle (C28). With one table the CLI can refuse the name at parse time.

## Call paths, current → proposed

Current: `cli._enrich` → `_report_plan` → `cost.estimate(prompts, model)` → `PRICES[model]`. A paid run consults no price. Extract: `transcript.py` → `pricing.compute_cost` → `MODELS.get(model)`.

Proposed: argparse checks `--model` against `MODELS` minus the placeholder, so a dry run and a paid run both stop at the door with a message naming the accepted models. `cost.estimate` reads `MODELS[model]` and multiplies by `spec.input` and `spec.output`. The extract path does not change.

## File-tree diff

```
src/hyphae/
  extract/pricing.py            ~ one row added (claude-sonnet-4-5-20250929); PER_MILLION exported; docstring names both readers
  enrich/cost.py                ~ Rates and PRICES deleted; estimate reads MODELS
  cli.py                        ~ --model takes choices from MODELS, metavar="MODEL"
tests/
  enrich/test_cost.py           ~ reads MODELS[MODEL]; test_an_unpriced_model_crashes deleted
  enrich/test_enricher__cli.py  + an unknown --model exits at parse time, spending nothing
docs/enrichment.md              ~ the two lines that make cost.py the owner of the rates
CONTEXT.md                      ~ one glossary line (below)
```

## Key contracts

```python
# extract/pricing.py — shape unchanged; one row, one public constant
MODELS["claude-sonnet-4-5-20250929"] = ModelSpec(input=3.0, output=15.0, context_window=200_000)
PER_MILLION = 1_000_000

# enrich/cost.py
def estimate(prompts: Sequence[Prompt], model: str) -> Estimate:
    spec = MODELS[model]   # KeyError is the crash; the CLI door names the choices first
    ...
    usd=(input_tokens * spec.input + output_tokens * spec.output) / PER_MILLION

# cli.py — refused before preflight, the store, or a rendered prompt
subcommand.add_argument(
    "--model", default=DEFAULT_MODEL, metavar="MODEL",
    choices=[model for model in MODELS if model != SYNTHETIC_MODEL], ...)
```

`EXTRACTOR_VERSION` stays. The fingerprint folds it in, but the added row changes no stored value: the canonical store holds zero api calls on `claude-sonnet-4-5-20250929` (`select model, count(*) from api_calls group by 1`, `data/traces.duckdb`, 2026-09-03). A row pricing a recorded model would need the bump.

## Chosen test seam

`cost.estimate` for the arithmetic: `tests/enrich/test_cost.py` keeps redoing the multiplication by hand, reading `MODELS[MODEL]` where it read `PRICES[MODEL]`. `cli.main` for the door: `tests/enrich/test_enricher__cli.py` asserts `hp enrich --model claude-opus-9 --dry-run` raises `SystemExit` with the model named on stderr, under the autouse subprocess guard that proves nothing was spent. `test_an_unpriced_model_crashes` goes rather than living on beside the door test: its obligation was "an unknown model refuses to quote", and the door is where that refusal now happens.

## Slices

Each slice is one commit and green under `mise run check` alone.

1. **One table.** Add the Sonnet 4.5 row and `PER_MILLION` to `pricing.py`; `estimate` reads `MODELS`; delete `Rates` and `PRICES`; `test_cost.py` imports `MODELS`; `docs/enrichment.md` line 120 gives `pricing.py` the rates and `cost.py` the arithmetic. Verified by `tests/enrich/test_cost.py` and `tests/extract/test_pricing.py`; `test_an_unpriced_model_crashes` still passes, since a bare `KeyError` names the model
2. **The door.** `choices` on `--model`; the new CLI test; delete `test_an_unpriced_model_crashes`; `docs/enrichment.md` line 126 says an unpriced model is refused before anything runs. Verified by `tests/enrich/test_enricher__cli.py`

## Decisions

- **`MODELS[model]` is the interface; no `rates()` / `window()` accessors.** Rejected the card's accessors: each would wrap one NamedTuple field read, and `analyze/macros.py` and the view tests already read `MODELS` directly
- **Sonnet 4.5 joins the table.** Rejected: dropping it, which leaves Haiku the only model a pass can name once the door exists. The row is one line; its price is on the pricing page read 2026-09-03, and its 200,000 window rests on the published figure alone, since the corpus has no call to check it against
- **Validate in argparse, with `choices` and a `metavar`.** Rejected: a check inside `_enrich`, which runs after parsing and rewrites a message argparse already prints; rejected: `choices=MODELS`, which would accept `<synthetic>`
- **`estimate` raises a bare `KeyError`.** Rejected: keeping the "add it to PRICES" message, which would name a table that no longer exists and repeat the door's list
- **The table stays in `extract/pricing.py`.** Rejected: moving it to `src/hyphae/pricing.py`, where a fact three packages read arguably belongs. The move is twelve import edits and doc references for no change in depth; it can follow as its own commit

## Out of scope

- `claude-fable-5-1`: 28 api calls in the canonical store are unpriced because the table lacks it, and the pricing page (2026-09-03) charges its cache reads at 0.025× where `_CACHE_READ` applies 0.1× to every model. Pricing it needs a per-model cache-read field on `ModelSpec` and an `EXTRACTOR_VERSION` bump: a separate change
- C11 (`_charges` returns per-million rates in the dollars type) and C32 (the `cost.Prompt` adapter) touch the same file and stay out
- `CliClient` keeps taking any model string; only the CLI door checks. `tests/enrich/fake_cli.py:OTHER_MODEL` reaches the client directly and is unaffected

## Glossary changes

Add under **Pipeline**, after **Fingerprint**, one line:

> **Price table** — what each model charges per million tokens and the window it answers in; one table, `src/hyphae/extract/pricing.py:MODELS`, read by the extract, the viewer, the analyze macros and the `hp enrich` quote

## Open questions

- Whether the move to `src/hyphae/pricing.py` rides along or waits. Nothing here depends on it
