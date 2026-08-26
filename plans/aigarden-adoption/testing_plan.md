# Testing plan: adopt aigarden — cog generators, record models, doc gates

Binds `plans/aigarden-adoption/design.md`. Obligations only; the implementer discharges each and the auditor traces it to the named evidence.

Twenty-two committed obligations across five levels, six manual probes, three upstream summaries. Two obligations are unreachable through the design's seam and are marked **UNREACHABLE** with what to do instead.

## unit (generators) — `tests/tools/`, no store I/O; the world is the live package: `aiobserve.view.app`, `aiobserve.view.bounds`, `aiobserve.view.nodes`, and the repo's own tree. Tests call `generate()` and assert properties, never golden strings

### `tools/gen_routes.py`

- **Every reader-facing GET route the app serves appears in the generated table.** Bolded: this is the whole point of cogging the table — a new page must not be able to ship undocumented. *Evidence:* build the app over a temp store, collect `{r.path for r in app.routes if "GET" in r.methods}`, subtract the generator's module-level exclusion constant, and assert the remainder equals the set of route strings parsed out of `generate()`; the assertion message names the missing paths.
- Every entry in the generator's exclusion list is a route that still exists. *Evidence:* assert the exclusion set is a subset of the collected route paths, so a deleted fragment route can't leave a stale suppression behind.
- Fragment routes (`nodes.BODY_URL`, `nodes.KIN_URL` prefixes) are excluded by rule, not by enumeration. *Evidence:* assertion that no generated row's path starts with either constant, imported from `nodes.py` rather than spelled in the test.
- A reader-facing handler with no docstring crashes the generator rather than emitting a blank description. *Evidence:* `pytest.raises` over a stub route whose endpoint has `__doc__ = None`; the raised message names the handler.

### `tools/gen_bounds.py`

- **Every number in the two generated tables equals the `bounds.py` constant it cites.** Bolded: the bounds prose is the viewer's payload contract, and a table that drifts from the constant is worse than no table. *Evidence:* parse the integers out of `generate()` and compare each against the imported symbol (`bounds.SESSIONS.default`, `bounds.KIN.ceiling`, `bounds.HIGHLIGHT_CHARS`, …) — the test names the constant, never the literal.
- Every public symbol in `bounds.py` appears in a table or in the generator's explicit exclusion list. *Evidence:* introspect `vars(bounds)` for `int` and `Bound` values with non-underscore names; assert the set is covered; the failure names the uncovered symbol.
- The label map (if the generator needs one — open question in the design) has no key that isn't a live `bounds.py` symbol. *Evidence:* assert label-map keys ⊆ the introspected symbol set. Skip this leaf if introspection alone carries the labels.
- The URL-knob table lists exactly the knobs the app reads, with the ceilings from `bounds.py`. *Evidence:* the `?nav=` rows compare against `nodes.Preset` members; the `?kin=`/`?log=`/`?detail=` rows against `bounds.KIN.ceiling`, `bounds.LOG.ceiling`, `bounds.DETAIL.ceiling`.

### `tools/gen_layout.py`

- Every path in the curated entry list exists in the repo. *Evidence:* `Path(entry).exists()` per entry, run from the repo root; the failure names the dead path.
- Every tracked top-level directory is either in the entry list or in the generator's explicit exclusion list. *Evidence:* compare the entry list against the top-level directories from `git ls-files`; the failure names the undocumented directory.
- A gloss is lifted from the source, and a missing source crashes. *Evidence:* assert the generated gloss for `src/aiobserve/extract/` equals the first line of `aiobserve.extract.__doc__`; a second case uses a stub package with `__doc__ = None` and asserts `pytest.raises` naming the module.

## unit (record models) — `tests/extract/`; the world is recorded fixtures under `tests/fixtures/`, not invented records

- **Every registered record type validates against a real recorded record of that type.** Bolded: the models claim to describe Claude Code's shapes, and only a recording can support that claim. *Evidence:* parametrize over the records in `tests/fixtures/registry_zoo/` (which the current `docs/schema.md:21` already cites as holding one record of every registered type); each record's `type` selects its model and `model_validate` succeeds.
- A field with no `description` crashes `gen_schema.generate()`. *Evidence:* a throwaway model defined in the test with a bare `Field()`; `pytest.raises` whose message names the model and the field.
- **A field with no evidence metadata (fixture path + CC version) crashes `gen_schema.generate()`.** Bolded: this is the mechanism that turns schema.md's "every claim needs a recording" rule into code. *Evidence:* same throwaway-model construction with `description` present and `json_schema_extra` absent; the raised message names the field.
- Every fixture path cited in a field's evidence metadata exists on disk. *Evidence:* walk every model's fields, assert each cited path resolves under `tests/fixtures/`; the failure names the field and the missing path.
- A shared field is defined on exactly one mixin, and the generated Records column is derived from inheritance. *Evidence:* assert `"uuid"` is declared in the base mixin's `model_fields` and absent from each subclass's own `__annotations__`, and that the generated row for `uuid` names more than one record type.
- An unrecorded extra field validates and is preserved (`extra="allow"`). *Evidence:* validate a real `assistant` record from `tests/fixtures/spine/` after adding one unknown key; assert `model_extra` carries it and no error is raised.

## drift (models ↔ parser) — `tests/extract/`; both sides are live source, no fixtures

- **Every member of `RecordType`, `ArchiveRecordType`, `SystemSubtype`, and `ContentBlock` has a model or an entry in the generator's documented-values map.** Bolded: this is the tie that keeps the two artifacts honest while `claude_code.py` still parses dicts. *Evidence:* set comparison over the imported enums; the failure names the unmodelled member.
- Every raw field name the models document appears in `claude_code.py`, or in an explicit "observed, unread" list in `records.py`. *Evidence:* scan the parser source for each documented field name (the models use raw camelCase spellings by design); the failure names the field, so a documented-but-unused row is a deliberate list entry rather than a silent pass.

## contract (generator CLI) — `tests/tools/`; subprocess, because the cog seam invokes a command and splices stdout

- `main()` prints exactly what `generate()` returns. *Evidence:* for each generator, `uv run python tools/gen_X.py` stdout compared against the imported `generate()`; the framing newline is asserted once so aigarden's own framing isn't doubled.
- Generated output never hard-wraps. *Evidence:* assert every line of `generate()` is a complete markdown table row or block line — no continuation lines — for each generator; a wrapped generator would fight the never-wrap rule on every cog run.

## config assertion — `tests/tools/`; parses `aigarden.toml` as data. Low power on its own, but it guards two hazards a probe can only catch once

- **`src/aiobserve/analyze/templates/**` is ignored for `markdown-style`.** Bolded: a reflow of a prompt template changes model input and stales every enrichment stamp — the one config entry whose loss is expensive and invisible. *Evidence:* parse `aigarden.toml`, assert the per-file-ignore entry exists and includes `markdown-style`; the test's comment carries the reason.
- Every path pattern in `[per-file-ignores]` matches at least one file in the repo. *Evidence:* glob each pattern from the repo root and assert a non-empty match, so the file-length ratchet's 11 entries shrink as files are fixed rather than rotting in place.

## manual probes — one-time deliberate faults during implementation, not committed tests. Record each result in the PR body

- Slice 1: a broken relative link in a living doc fails `mise run check`. *Evidence:* the `anchor-resolves`/`link-target` finding in the captured `mise run check` output, then a clean run after reverting.
- Slice 1: a hard-wrapped paragraph in a living doc fails `mise run check`, and `mise run check-fast` unwraps it. *Evidence:* the captured failing finding plus the resulting one-line diff.
- Slice 1: `aigarden check --fix` leaves code fences and tables byte-identical across the repo. *Evidence:* `git diff --stat` over the normalize commit restricted to fenced and table lines — zero changes. The general property is upstream's to prove; this probe pins it on our corpus.
- Slice 1: `mise run check` fails loudly when the aigarden binary is missing, rather than skipping the way `lint-shell` does on a runner without shellcheck. *Evidence:* run `check` with the pin removed from `PATH`; capture the non-zero exit and the message.
- Slice 2: a hand-edited cog block fails `cogs-check`, and `mise run cogs` heals it. *Evidence:* the captured failing output, then a green `check` after `mise run cogs`, with `git diff` showing the block restored.
- Slice 5: `mise run mv-doc` on a scratch doc rewrites every reference and leaves `check` green. *Evidence:* the diff listing the rewritten referrers, and the green `check`.

## upstream (aigarden, slice 0) — recorded at summary level; the detailed obligations belong to aigarden's Rust suite and its own design

- The never-wrap knobs produce normalize-mode reflow at the configured line length. *Evidence:* aigarden `cargo test` case over an over-length paragraph, asserting it stays one physical line.
- Fences and tables are left untouched under never-wrap (`code-blocks = false` semantics). *Evidence:* aigarden `cargo test` case over a document with a long fence line and a wide table, asserting byte equality.
- A release exists and installs from the mise pin. *Evidence:* the probe run of the released binary against this repo, before slice 1 lands.

## Unreachable through this seam — design findings, not dropped leaves

- **The generated schema.md tables carry every field the hand-written tables did.** No committed test can reach this: once the hand tables are deleted the comparand is gone, and the models are the only remaining source. The design's answer is a one-time reviewed diff, which leaves no artifact an auditor can trace. *Do instead:* commit the pre-change field inventory (field name + records column, extracted from `docs/schema.md` before the cut) as a fixture and assert the generated tables cover it, or — cheaper — paste the inventory into the slice-7 commit message so the diff review has a checkable list. Recommend the fixture: it costs one file and survives the review.
- **Cog freshness on a contributor machine.** `cogs-check` proves freshness only where aigarden runs; the design leans on the mise pin plus CI running exactly `mise run check`. There is no test at any level that proves a stale block can't reach `main` through a path that skips `check`. Branch protection, not a test, is the control; note it in the PR rather than inventing a weaker test.

## Deliberately not covered

- **Link, anchor, and reflow rules themselves.** They are aigarden's behavior, tested upstream; re-testing them here would pin another tool's semantics. Our stake is that the gate runs, which the probes cover once and CI covers continuously.
- **The route table's description prose.** The generator's contract is that a docstring exists and is lifted; whether the sentence reads well is a review question.
- **`gen_layout.py`'s curated ordering.** The entry list is an editorial choice; the tests hold existence and coverage, not sequence.
- **Parsing through the record models.** Out of scope by design; the two drift obligations stand in until the parser adopts them.
- **External URL liveness.** aigarden is offline by design and the design defers lychee.
