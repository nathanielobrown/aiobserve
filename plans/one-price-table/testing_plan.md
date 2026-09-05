# Testing plan: one price table

Obligations for `plans/one-price-table/design.md` — deleting `enrich/cost.py:PRICES`, adding
the Sonnet 4.5 row to `extract/pricing.py:MODELS`, and refusing an unknown `--model` at parse
time. Every repository claim the design makes was re-checked against the working tree; the
report at the bottom says which held.

The seam is the design's: `cost.estimate` for the arithmetic, `cli.main` for the door.

## unit (price table) — `tests/extract/test_pricing.py`, no I/O, table in and dollars out

- The table states one spec per model, and the placeholder is the only entry without a
  context window. *Evidence:* `test_the_placeholder_is_the_only_model_that_declares_no_context_window`
  already sweeps `MODELS.items()`; the added Sonnet 4.5 row falls under it with no edit, and a
  row added with `context_window=None` turns it red
- **The added row moves no stored cost, which is what lets `EXTRACTOR_VERSION` stand.**
  *Evidence:* no fixture under `tests/fixtures/` records `claude-sonnet-4-5-20250929` as a
  `message.model` — the recorded models are `claude-fable-5` (101), `claude-opus-4-8` (26),
  `claude-opus-5` (9), `claude-sonnet-5` (4), `claude-opus-4-1-20250805` (1) — so every
  existing cost assertion in `tests/extract/` is itself the red-check: one of them moving
  means the row priced a model the corpus records, which is the case the design says needs a
  version bump
- `PER_MILLION` is the one definition of the divisor. *Evidence:* `split_cost` and
  `compute_cost` keep passing their existing assertions in
  `test_a_reply_is_priced_by_its_model_and_its_four_token_kinds` and
  `test_the_cache_write_splits_by_ttl` after the rename from `_PER_MILLION`; those tests redo
  the division by hand against the literal, so the rename cannot hide behind a shared symbol

## unit (quote arithmetic) — `tests/enrich/test_cost.py`, no network, rates read from `MODELS`

- **A quote is the rendered characters, the level instructions, the transport scaffold and
  the table's rates — arithmetic a reader can redo.** *Evidence:* update
  `test_an_estimate_is_multiplication_a_reader_can_redo` to import `MODELS` and `PER_MILLION`
  from `hyphae.extract.pricing` and build `full` from `spec.input` / `spec.output` where it
  built it from `rates.input_usd` / `rates.output_usd`; the `pytest.approx` compare on
  `quote.usd` and the whole-object compare on `Estimate` both stay. The number must not move:
  Haiku 4.5 is priced $1/$5 in both tables today, so a changed quote means the wrong row was
  read
- The two measured constants stay pinned to the 2026-08-13 probe. *Evidence:*
  `test_the_measured_constants_are_pinned_to_their_probe` is untouched by this change and
  still asserts `(TRANSPORT_TOKENS, OUTPUT_TOKENS) == (700, 230)`
- An empty plan quotes zero rather than a floor price. *Evidence:*
  `test_an_empty_plan_costs_nothing`, unchanged — it still names a model, so it also proves
  the `MODELS[model]` lookup happens on a plan with nothing in it
- The module's docstring no longer claims to hold a price table. *Evidence:* the file header
  of `tests/enrich/test_cost.py` (which today says "a price table in this file") reads against
  `pricing.py`; prose only, checked by eye in review

`test_an_unpriced_model_crashes` is deleted in slice 2, per the design. Its obligation — "an
unknown model refuses to quote" — moves whole to the door leaf below, which is a stronger
statement: the refusal now happens before a prompt is rendered.

## integration (the door) — `tests/enrich/test_enricher__cli.py`, real store fixture, fake client, autouse subprocess guard

The guard is `tests/enrich/conftest.py:refuse_subprocess`, which monkeypatches
`subprocess.run` and `subprocess.Popen` to raise. It is what makes "spent nothing" an
assertion rather than a hope.

- **An unknown `--model` exits at parse time and names the model, spending nothing.**
  *Evidence:* new leaf in `tests/enrich/test_enricher__cli.py` — `cli.main("enrich", "--db",
  str(store.path), "--model", "claude-opus-9", "--dry-run")` raises `SystemExit`, `capsys`
  stderr carries `claude-opus-9`, and the autouse guard proves no `claude` process started.
  Assert `stored(store) == []` too, so the exit is also shown to have written no row
- **The refusal precedes preflight, the store read and any rendered prompt.** *Evidence:*
  the same leaf runs without the `logged_in` fixture, which every other run-path test needs
  because `preflight` shells out. A check inside `_enrich` — the alternative the design
  rejected — would trip the guard on `preflight` first and fail this leaf
- `<synthetic>` is not an accepted model, so the placeholder cannot be spent against.
  *Evidence:* the same leaf parametrized over `("claude-opus-9", SYNTHETIC_MODEL)`; both raise
  `SystemExit`. This is the assertion that distinguishes `choices=[m for m in MODELS if m !=
  SYNTHETIC_MODEL]` from the rejected `choices=MODELS`
- A named, priced model still runs. *Evidence:* the existing dry-run leaves
  (`test_a_dry_run_asks_no_auth_question` and the quote leaf at
  `tests/enrich/test_enricher__cli.py:194`) drive `hp enrich` on the default model and assert
  `"at most 7 item(s) would be sent"`; they are the green side of the door and need no edit

## integration (argparse surface) — `tests/test_cli.py`, parser only, no store

- Adding `choices` and `metavar` changes no parsed default. *Evidence:* the `enrich` entry in
  `SURFACES` already pins `{"db", "project", "model": DEFAULT_MODEL, "dry_run", "limit",
  "concurrency"}`; it stays green unedited. `DEFAULT_MODEL` is
  `claude-haiku-4-5-20251001` (`src/hyphae/enrich/client.py:35`), which is in `MODELS`, so a
  default outside the choices would fail here at parse time

## documentation and glossary — the gates, not pytest

- `docs/enrichment.md` gives `pricing.py` the rates and says the door refuses an unpriced
  model. *Evidence:* lines 120 and 126 of the current file are the two the design names —
  line 120 reads "`src/hyphae/enrich/cost.py` owns the rates and arithmetic" and line 126
  "Asking for an unpriced model crashes instead of returning a zero quote". `mise run
  check-fast` reports a path that does not resolve; the wording is a review read
- The **Price table** glossary line lands under **Pipeline** in `CONTEXT.md`. *Evidence:*
  `mise run check` runs the freshness check over every generated block; the line itself is a
  review read against the design's wording

## not covered

- **Whether the numbers match Anthropic's published prices.** No seam reaches the pricing
  page, and a test asserting a constant against itself proves nothing. `pricing.py` records
  the read date; the Sonnet 4.5 window of 200,000 rests on the published figure alone, since
  the corpus records no call on that model to check it against
- **`estimate`'s bare `KeyError` on a model the table lacks.** Once
  `test_an_unpriced_model_crashes` is deleted, the library-level crash has no leaf. It is
  reachable only by calling `estimate` past the door, and `cli.py:374` is its one caller — see
  the report below
- `CliClient` accepting any model string. The design keeps it that way on purpose;
  `tests/enrich/fake_cli.py:OTHER_MODEL` (`claude-sonnet-4-5-20250929`) reaches the client
  directly in `test_client.py` and `test_client__pool.py`, and those leaves are about the
  envelope's `modelUsage` key, not about pricing
- `claude-fable-5-1` and its 0.025x cache-read rate. Out of scope by the design; pricing it
  needs a per-model field on `ModelSpec` and an `EXTRACTOR_VERSION` bump

## Report

Verified against the working tree, not taken from the design:

- Both tables and their dates are as described: `PRICES` prices two models at 2026-08-07,
  `MODELS` prices seven plus the `<synthetic>` placeholder at 2026-08-30. The design's "eight
  models" counts the placeholder
- `docs/enrichment.md` lines 120 and 126 are the two lines named. `--model` exists on the
  `enrich` subcommand only (`src/hyphae/cli.py:266`), so one door covers it
- `analyze/macros.py:16` and four view test modules read `MODELS` directly, which supports the
  design's rejection of `rates()` / `window()` accessors
- `_PER_MILLION` is private today (`pricing.py:33`) and `cost.py:80` spells the divisor as a
  literal — the export is a real DRY fix, not cosmetic
- No cog block or generated document prints the table, so adding a row splices nothing

Findings, neither papered over:

1. **One obligation goes uncovered by choice, and the design does not say so.** After slice 2
   deletes `test_an_unpriced_model_crashes`, nothing asserts that `estimate` refuses an
   unpriced model. The design's reasoning holds — the door is where the refusal happens now,
   and `cli.py:374` is `estimate`'s only production caller — but the seam does leave the
   library able to be handed a bad model with no leaf watching. Accepting this is the cheaper
   trade; the alternative is a one-line `pytest.raises(KeyError)` leaf that would restate the
   deleted test against a different message. Listed under "not covered" above rather than
   silently dropped
2. **The design's `EXTRACTOR_VERSION` argument is narrower than the risk.** It checks the
   canonical store (`data/traces.duckdb`, 2026-09-03) but not the fixture corpus, which is
   what `mise run check` actually prices. I checked: no fixture records
   `claude-sonnet-4-5-20250929` as a `message.model`, so the conclusion stands. The claim
   should cite both corpora
