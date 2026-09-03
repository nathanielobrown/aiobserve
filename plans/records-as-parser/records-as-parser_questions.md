# Phase 1: the shape of the seam

Facts settled by exploring, so they are not questions:

- **Validation is cheap.** Over every fixture transcript, `model_validate` on the modelled records ran at a third of the `json.loads` cost. The canonical store holds 703,766 records (2.79 GB of raw text, 627 sessions), so the whole corpus re-parses in seconds of extra time, not minutes.
- **The models already cover every field the parser reads.** The drift test enforces that today, so parsing through them loses no field.
- `Line` **survives.** It keeps `line_no` and `raw` (the archive needs the verbatim text) and its `record` becomes a typed record instead of a dict.
- **Raw field names stay.** The models spell fields the way Claude Code writes them (`isMeta`, `toolUseResult`), because the model is the schema document and the raw name is what a reader greps a transcript for. Readers will write `record.isMeta`.
- **Archived types get one catch-all model.** The sixteen `ArchiveRecordType` kinds are read by nothing, so one `ArchivedRecord` carrying only `type` parses them, and the registry stays the closed world that rejects an unknown type.
- **An unknown shape still crashes with session and line.** Pydantic's error is wrapped into `TranscriptSchemaError` where `_check_type` raises today.



## 1. Should "every documented field is one the parser reads" survive, or does the model become free to document fields nothing reads?

Today the drift test holds the models to the parser in both directions. One direction is structural once the parser reads through the models: a field the parser reads must be declared, or the read is an attribute error. The other direction, "a documented field is one the parser reads", has no structural form: nothing in Python can cheaply say which attributes a reader touched. It exists today as a source grep for `"fieldName"` in the parser modules, and `OBSERVED_UNREAD` is the excuse list for the eight fields it would otherwise fail on.

The rule's purpose, in its own words: a row describing a field nothing reads is a claim about Claude Code that the extractor cannot notice going wrong. That stays true after the change. But every field carries a `Cited` recording, and once the parser validates the model against every record in the corpus, every declared field's *type* is checked on every extract, read or not. A wrong claim about shape now crashes an extract. A wrong claim about *meaning* was never caught by either mechanism.

Two options. **Drop the rule**: delete `OBSERVED_UNREAD` and the grep; the model documents what Claude Code writes, cited, and validation checks the shape on every extract. **Keep it by attribute grep**: replace the `"fieldName"` string grep with a `.fieldName` grep over the same parser source, keep `OBSERVED_UNREAD`, keep the vacuity gates around it.

Stakes: low and reversible. The grep is about 60 lines of test that can come back.

### Recommendation: drop the rule

Corpus-wide validation is a stronger check on the claims that matter than a source grep was, and the grep's vacuity gates (three tests that exist only to keep the grep honest) go with it. What we give up: the ratchet that says "you documented this field, now go read it or say why not". If you value that nudge, say so and it stays as an attribute grep.

### User Response:

Agree with recommendation

## 2. Do the registry enums stay hand-written with the models bound to them, or are the registries derived from the models?

`records/registry.py` holds the closed world as `StrEnum`s: `RecordType`, `ArchiveRecordType`, `SystemSubtype`, `ContentBlock` and four smaller ones. The models bind to them through class variables (`RECORD_TYPE = RecordType.USER`). Once the models parse, each model's `type` field becomes a discriminator (`type: Literal[RecordType.USER]`), and pydantic dispatches on it. That makes two lists of the same names: the enum, and the set of models. Today's drift test has one leaf keeping them equal, with `UNMODELLED` as the excuse list for registered shapes that have no model (the archive types, six thin `system` subtypes, the `image` block).


| Option                                  | What is the source of truth                                                                                                  | What happens to `UNMODELLED`                                                                                          |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Hand-written enums, models bind to them | The enum. A new type is added there first; a record of that type crashes at parse until a model (or the catch-all) claims it | Stays, shrunk: only the six thin subtypes and `image` need a reason, and one drift leaf keeps it honest               |
| Derive the enums from the models        | The models. `RecordType` becomes whatever `Literal`s the union declares; the registry module goes                            | Goes: a shape is registered by having a model. The thin `system` subtypes need a model each, even if it adds no field |


The enums are used outside `records/` in three places: the parser's tag checks, `tools/gen_schema.py`, and the tests. `MachineTag` and `TurnTag` are not record shapes at all and stay enums either way.

Stakes: medium. Reversible, but it decides where the next Claude Code version's new record type gets typed first, which is the edit this package absorbs most often.

### Recommendation: hand-written enums, models bound to them

The registry's job is to be the closed world, and an enum is the plainest way to state one; the models describe fields, which is a different job. It also keeps `tools/gen_schema.py` and the `SystemRecord` fallback for thin subtypes as they are. What we give up: the two lists stay two, held equal by one test leaf rather than by construction.

### User Response:

Agree with recommendation

## 3. When a known record carries a field no model declares, should the extract stay silent, count it, or crash?

The models allow extra keys on purpose: Claude Code adds fields without notice, and the package docstring says a validation error would be a worse answer than an undocumented field. Parsing through the models does not change that. But it creates a cheap place to *notice* a new field: pydantic keeps the extras on each instance, so the parser can see them for the first time.


| Option           | Behaviour                                                                                        | Cost                                                                                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Silent           | As today: extras ride along and nobody sees them                                                 | A new field is found only when someone opens a transcript                                                                                               |
| Count and report | Each extract tallies `(record type, field)` pairs it did not know and prints them in its summary | A few lines in the extractor and one line in the CLI's report                                                                                           |
| Crash            | `extra="forbid"`: a new field stops the run until it is declared                                 | Every Claude Code release blocks extraction until the model is edited, which contradicts the docstring's reason and the "still being written" tolerance |


Stakes: low. Any option is a one-line config change away from another. The interesting one is whether the count is worth the noise: 24k `attachment` records alone will carry fields no model declares, so the tally must be per *modelled* type only.

### Recommendation: count and report, modelled types only

It turns "verify schemas against recordings" from a rule into a signal the extract hands you, at the cost of one tally and a report line. What we give up: nothing today, except that the first run on the canonical store will print a list you then have to triage.

### User Response:

Crash if in tests, log error if in production. You may have to get creative to make this work, but I've used this pattern in other projects and compromise in my opinion

## 4. Land it as one branch with ordered commits, or run the corpus census as its own PR first?

The safe order is fixed: first validate every record in the canonical store against its model and fix whatever claim the corpus contradicts, then switch the parser. The question is whether the census is its own PR.

**One branch.** Commit 1 adds the census as an `hp` subcommand or a test gated on the canonical store, commits 2 to n fix model claims it surfaces, then the parser switches reader by reader (session, turns, api calls, tool calls, compactions), and the drift test goes last. One review unit, one story.

**Census PR first.** The census and the model fixes land on `main` before the parser moves. If the corpus surfaces many wrong claims, the fixes are their own reviewable diff and `docs/schema.md` regenerates once, cleanly, before the risky change.

Stakes: process only. The second costs one more PR; the first risks a review that mixes "the doc was wrong about `usage`" with "the parser now reads `usage` as an attribute".

### Recommendation: census PR first

I expect the corpus to contradict a few claims (a field typed `str` that is null on some version, a block kind the models list but a message never holds), and those fixes are a schema-doc change worth reading on their own. What we give up: a day of latency before the deepening itself starts.

### User Response:

On branch with one PR

## Pending questions (depends on answers above)

- Whether the census stays as a permanent `hp` subcommand or a test gated on the canonical store — depends on Q4
  - Make it a test or temporary script. Let's not polute the `hp` command - that is for end users
- What the one surviving drift leaf checks (enum members ⇔ models, or nothing) — depends on Q1, Q2
- Whether `SystemRecord` stays as the fallback for the six thin subtypes, or each gets an empty model — depends on Q2
- Where the unknown-field tally is printed, and whether `hp extract` fails when it is non-empty under a flag — depends on Q3
- Which term `CONTEXT.md` gains: today it defines *Record* as the verbatim line; the typed shape needs a name ("record model") — no dependency, will settle when the design does



# Phase 2: noticing what the models don't know, and proving them on the corpus

Settled by phase 1's answers:

- **The drift test shrinks to one leaf.** "Every registered shape has a model or a stated reason" stays, with `UNMODELLED` down to the six thin `system` subtypes and the `image` block. The read-direction grep, `OBSERVED_UNREAD` and the three vacuity gates go.
- `SystemRecord` **stays the fallback** for the thin subtypes, so a `system` record with no model of its own still parses.
- `model_for` **becomes the parser's dispatch**, which retires the audit's note that it was test-only machinery (C15).
- **The census reads the store, not the disk.** `raw_records` holds every line of every session, including the ones Claude Code has pruned from disk; the transcripts on disk are a few weeks of the same thing.



## 5. How does the parser learn it is under test, so an undeclared field crashes there and logs in production?

The models keep `extra="allow"`, so an undeclared field never fails validation. After validating a record, the parser walks it and its nested models (the message, its blocks, `usage`, `toolUseResult`) and collects every key no model declares. `usage` is where Claude Code adds fields most often, so the walk has to descend. What the parser does with the collection is the switch you asked for: raise under the suite, log in an `hp extract`.


| Option                                  | How tests flip it                                                                                                                                                         | Trade-off                                                                                                                                      |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| A module-level hook the suite replaces  | `records/` exposes `on_unknown_fields`, defaulting to a logger; `tests/conftest.py` sets it to a raiser at import, the way it already pins `duckdb.connect` to one thread | Follows a pattern the suite already uses; no environment reads in `src/`; the census can install a third hook that collects instead of raising |
| An environment variable read at startup | `HYPHAE_STRICT_RECORDS=1` in the test runner's env; the CLI validates it at startup like the ingest keys                                                                  | Visible in `mise.toml`, so a person running pytest by hand without it gets production behaviour and never sees the crash                       |
| Detect pytest                           | `"pytest" in sys.modules`                                                                                                                                                 | Works by accident; no seam for the census                                                                                                      |


Stakes: low, and the choice is local to one function. The one thing to get right is that the census must be able to collect rather than stop at the first unknown field, which rules out the third option.

### Recommendation: a module-level hook the suite replaces

It is the seam with three adapters already in sight: log, raise, collect. What we give up: a hand-run `pytest` outside `mise` still crashes, which is the behaviour we want, so nothing.

### User Response:

Just have some config constant like `UNIT_TESTING` and then pass env var `UNIT_TESTING` by default with tests. Set in pytest.ini or similar

## 6. When production logs an undeclared field, once per extract run or once per session?

An `hp extract` walks up to 627 sessions. If Claude Code shipped one new `usage` field last week, per-session logging prints the same error for every session extracted since, and the one line that matters drowns. Per-run logging means the hook keeps state across sessions and the CLI flushes it after the loop: one line per (record type, field), with how many sessions carried it.

**Per session.** The hook logs as it goes, with session id and line number. Simple, no state, and the first occurrence names a line you can open.

**Per run.** The hook tallies; `hp extract` prints one line per unknown field after its summary, with the session count and the first session and line seen. `refresh()` in `pipeline.py` would need to expose the tally, or the CLI reads it from the hook after the loop.

Stakes: low. Both are a logger call; the per-run form is a small dict.

### Recommendation: per run, with the first sighting's session and line

A new field is one fact about Claude Code, and it should print once, with a place to look. What we give up: the hook holds state, so it must be reset per run, which the CLI does where it prints.

### User Response:

Agree with recommendation

## 7. Is the census a permanent env-gated test, or a script deleted before merge?

Its job is to run every record in the canonical store through its model, collecting every validation failure and every undeclared field, and print them grouped by record type, field and Claude Code version. The suite already has the shape: `tests/enrich/test_prompts__budget.py` gates two leaves on `HYPHAE_LIVE_STORE`, copies the store it names, and skips when it is unset.

**A permanent env-gated test** beside those: skipped in CI and `mise run check`, run by hand with the store named. It stays useful after the branch: every Claude Code release is a reason to run it, and the memory of this project says the real-corpus leaves are what catch the shapes fixtures can't.

**A temporary script** under `plans/records-as-parser/`, run once to fix the models, deleted before the PR. Nothing to maintain, and nothing to run next release.

Stakes: low. The script becomes the test with a decorator; the test becomes a script by deletion.

### Recommendation: a permanent env-gated test

Every Claude Code release changes the shapes, and this is the one check that sees all 700k records instead of the fixtures' 350. What we give up: one more slow leaf that someone has to remember to run, which the release-time checklist in `docs/schema.md` can name.

### User Response:

Agree with recommendation

## Pending questions (depends on answers above)

- Whether `hp extract` should exit non-zero when the tally is non-empty under a flag, for a future CI extract — depends on Q6
- `CONTEXT.md` gains *record model*: the typed shape of one record kind, which the parser reads through and `docs/schema.md` prints — will write when the design is
- The commit order inside the one PR (hook and census first, then reader by reader, drift test last) — will go in the plan, not a question

