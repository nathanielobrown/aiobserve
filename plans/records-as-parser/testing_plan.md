# Testing plan: read the transcript through the record models

The obligations for `design.md` beside this file, one leaf per behaviour, each naming the evidence that discharges it. Written against the revised design (8 slices) and verified against the worktree's code on 2026-09-03.

Nothing is unreachable through the design's seam. The two obligations the first draft of this plan could not place — strict mode against the fixtures' undeclared fields, and the census as a slice gate — are resolved by the boundary rules in "Where the walk stops" and by slice 1 declaring what the fixtures carry.

## Findings against the revised design

Three, none blocking.

1. **Question 8 records no user response.** The design is written to the designer's recommendation (scope strict by owner). The whole boundary — the opaque marker, the dict leaves, the thin-subtype rerouting — rests on an answer that is not there yet. Every leaf below assumes it stands.
2. **The field grouping is close but not exact.** Re-walking every `tests/fixtures/**/*.jsonl` through `model_for` + `model_validate` and bucketing by owner gives **39** `toolUseResult` paths, **17** on the six thin `system` subtypes and **62** envelope paths (118 by this split; 115 as distinct paths, since a thin-subtype path and an envelope path can share a name under `SystemRecord`). The design says 45 / 17 / ~53. The direction holds and the boundary rules are unaffected, but the envelope side is larger than the design's "~30 declarations once shared fields land on mixins" assumes. Slice 1's clean-corpus leaf (obligation 9) is the thing that settles the real number, which is what the design intends.
3. **`ArchivedRecord`'s mixin is genuinely open.** The design's own open question flags it, and `test_records.py:test_a_record_type_with_no_uuid_does_not_inherit_one` is the leaf that will object: `Identified` would hand every archived kind `sessionId` and `parentUuid` it has no evidence for. Obligation 4 below makes the collision explicit rather than leaving it to the implementer to notice.

Verified true against the code: `raw_record` reads `line.uuid`, `timestamp_of(line.record)` and `line.record["type"]` on *every* line, so `ArchivedRecord` does need `uuid` and `timestamp` or 24k `raw_records` rows lose two columns. `agent_runs.py:67` is `meta["agentType"]` on the `.meta.json` sidecar, not a transcript record, so scoping the zero-bracket pin to `transcript.py` is right. `Described` in `evidence.py` is the single base with `extra="allow"`, so `OPAQUE` lands in one place. `ToolUseResult` already carries exactly `persistedOutputPath` and `runId`. `SystemSubtype` has ten members and four have models, so "six thin subtypes" is exact.

## Environment facts these obligations assume

- `pytest-env` is **not** a dependency today (`pyproject.toml` has `pytest`, `pytest-timeout`, `pytest-xdist` and no `[tool.pytest.ini_options] env`). Slice 2 adds the dependency and `env = ["UNIT_TESTING=1"]`; obligation 40 pins that it reaches a bare `uv run pytest`
- The live store is `/Users/nob/repos/hyphae/data/traces.duckdb` (16.3 GB on 2026-09-03). Never open it directly: copy it with its WAL, the way `tests/enrich/test_prompts__budget.py:live_store_copy` does. `raw_records` is `(session_id, source, line_no, uuid, timestamp, type, raw)` — `src/hyphae/export/duckdb.py:171`
- The invented fixture is a **file** under `tests/fixtures/invented/`, as the revised design now says. `tests/conftest.py:corpus_transcripts` globs every fixtures subdirectory *except* `invented/`, so a new top-level directory would join the shared corpus store and crash every tier that builds one
- The existing invented fixtures carry the tripwire string `SUPER-SECRET-PAYLOAD-9f2a` in the offending record (`tests/fixtures/invented/README.md`). The new one carries it too, which is how the never-print-the-value leaves get their evidence

---

## unit — record models, dispatch and the walk's boundary (`tests/extract/test_records.py`)

Real recorded fixtures in, model instances out. No I/O beyond reading a fixture file. Slice 1 unless noted.

1. Every member of `RecordType`, `ArchiveRecordType` and `SystemSubtype` resolves to a model through `model_for`, which never returns `None`. *Evidence:* parametrized over the registry enums; `tests/fixtures/registry_zoo/registry-zoo-0000-0000-0000-000000000000.jsonl` holds one record of every registered kind, and each validates against the class `model_for` returned. Migrates in from `test_records__drift.py:test_every_registered_shape_has_a_model_or_a_stated_reason` at slice 8.
2. Every `ArchiveRecordType` member and every thin `SystemSubtype` has an `ARCHIVED_UNREAD` reason, and every `ARCHIVED_UNREAD` key is a live registry member with no model of its own. *Evidence:* both directions asserted over `ArchiveRecordType | SystemSubtype` and the dict, so a reason cannot rot in either direction — the shape `test_no_reason_is_left_for_a_shape_that_no_longer_exists` has today.
3. An `ArchiveRecordType` record parses as `ArchivedRecord` carrying `type`, `uuid` and `timestamp`, and its other keys survive as extras. *Evidence:* the `attachment` and `file-history-snapshot` records in `registry_zoo`; assert all three declared values, and assert `model_extra` is non-empty.
4. **`raw_record` still fills `uuid` and `timestamp` for every archived line, and no archived kind inherits a field it has no evidence for.** *Evidence:* `tests/extract/test_claude_code__archive.py`'s existing row assertions run unedited (obligation 24), and `test_a_record_type_with_no_uuid_does_not_inherit_one` — already in this file at line 115 — passes against whichever mixin `ArchivedRecord` extends. Bolded: this is the design's open question, and these two leaves together are what decides it.
5. A thin `system` subtype routes to `ArchivedRecord`, not `SystemRecord`; `SystemRecord` is the base of the four modelled subtypes and is no longer a fallback. *Evidence:* the `away_summary`, `api_error`, `informational`, `scheduled_task_fire`, `agents_killed` and `stop_hook_summary` records in `registry_zoo`; assert `model_for` returns `ArchivedRecord` for each, and `SystemRecord` for none of them. This amends the phase-2 note in the questions file, so the leaf is the record of the amendment.
6. **The opaque set is exactly `{ArchivedRecord, ToolUseResult}`.** *Evidence:* a leaf collecting every subclass of `Described` whose `OPAQUE` is non-empty and comparing the set whole. Bolded: `OPAQUE` silences the walk, so a third model gaining it is how strict mode stops meaning anything, and this leaf is the only thing standing in the way.
7. Every opaque model states a non-empty reason. *Evidence:* the same leaf asserts each `OPAQUE` string is non-empty — a marker with no reason is `UNMODELLED` without its excuse.
8. `ToolUseResult` keeps exactly the two fields readers open, `persistedOutputPath` and `runId`, each with its description and `Cited` recording. *Evidence:* the existing `test_every_documented_field_carries_its_meaning_and_its_evidence` and `test_every_citation_shows_the_field_in_the_fixture_it_names` leaves cover both; assert the declared field set is those two, so the opaque model does not quietly become the 45-field grab-bag the design rejected.
9. **The `UnknownFields` walk over `tests/conftest.py:corpus_transcripts` in lax mode reports nothing.** *Evidence:* a new leaf reading every corpus transcript through `read_lines` with a lax `UnknownFields` and asserting `report() == ""`; the failure message is the list of paths still to declare. Bolded: this is slice 1's definition of done, the design deliberately does not enumerate the fields, and this leaf is the enumeration. Marked `@pytest.mark.xdist_group` with the corpus so one worker pays for it once.
10. Every field slice 1 declares carries its description and its `Cited` recording, and the citation shows the field in the fixture it names. *Evidence:* the two existing leaves at `test_records.py:123` and `:169` extend over the new declarations with no edit — they are parametrized over `field_tables.documentation()`, so a declaration with no evidence fails them automatically.
11. An unread object is declared as `dict[str, Any] | None` with a citation, and the walk treats it as a leaf. *Evidence:* `thinkingMetadata`, `stop_details`, `context_management`, `usage.server_tool_use` and `compactMetadata.preservedSegment` — assert each field's annotation is a dict type, and assert obligation 9 stays green with fixtures that carry keys inside them (`spine/`, `4208c1bd-…` for `context_management`). Without this leaf, the dict rule is indistinguishable from a model nobody wrote.
12. **A `user` message's content list dispatches to `TextBlock` and `ToolResultBlock` by its `type` discriminator, and a `tool_result`'s own content list dispatches to `TextResult` / `ImageResult` / `ToolReferenceResult`.** *Evidence:* `tests/fixtures/spine/4208c1bd-….jsonl` (Claude Code 2.1.221) for the text and tool-result forms, `tests/fixtures/server_tools/` for the advisor forms; assert the concrete class of each parsed block, not just that parsing succeeded. Slice 2. Bolded: this replaces `_check_type`'s block loop, and `server_tool_use` is the kind that once produced no row and no crash.
13. A block kind outside the union raises `TranscriptSchemaError`, not a bare pydantic `ValidationError`. *Evidence:* `tests/fixtures/invented/invented-unknown-block.jsonl` (`type: "clairvoyance"`); assert the exception type and that `SUPER-SECRET-PAYLOAD-9f2a` is absent from `str(error)`. Slice 2.
14. An unknown record `type` and an unknown `system` subtype each raise `TranscriptSchemaError` naming the session and line. *Evidence:* `invented-unknown-type.jsonl` and `invented-unknown-subtype.jsonl`; assert session id and line number present, tripwire absent. These behaviours exist today in `_check_type` and must survive the move into `model_for` — including for a subtype outside both the four models and `ARCHIVED_UNREAD`.
15. The leaves already in this file that describe the models — extras ride along, a shared field lives on one mixin, every nested field names one container, a field inside a block is named from the block — still pass unchanged. *Evidence:* `test_records.py` lines 92-224 green with no edit; they were always tests of the parser's types, and the mixin leaf is what keeps the ~30 shared declarations from being written five times.

## unit — `TranscriptSchemaError` from validation (`tests/extract/test_transcript__unknown.py`)

New file mirroring `transcript.py`. `read_lines` at its own interface. Slice 2.

16. **A record whose declared field has the wrong type raises `TranscriptSchemaError` naming the session, the line, the model class and the pydantic field path — and never the value.** *Evidence:* a new invented fixture line (`invented-wrong-field-type.jsonl`) whose `usage.input_tokens` is the string `"SUPER-SECRET-PAYLOAD-9f2a"`; assert the message contains the session id, the line number, `AssistantRecord` and `message.usage.input_tokens`, and assert the tripwire string is absent. Invented by necessity: no recorded session carries a wrong-typed field. Bolded: the error contract is the whole promise `.claude/rules/python.md` makes, and a leaked value is a privacy incident.
17. The formatter uses `errors(include_input=False, include_url=False)`, so no `input=` fragment reaches the message. *Evidence:* the same fixture; assert `"input"` and the URL prefix `https://errors.pydantic.dev` are both absent from the rendered message.
18. A missing required field raises the same shape. *Evidence:* `invented-no-timestamp.jsonl` (a `pr-link` with no `timestamp`) already exists and already crashes; assert the message now names `PrLinkRecord` and `timestamp`.
19. A truncated final line is still dropped with a warning rather than raised, and a broken line anywhere earlier still stops the run. *Evidence:* `invented-truncated-tail.jsonl` and `invented-corrupt-middle.jsonl`, read as a pair; the tolerance sits before validation and must not widen.

## unit — `UnknownFields` (`tests/extract/test_transcript__unknown.py`)

Its own interface, driven by one invented fixture, because a field nobody has recorded cannot come from a recorded session. Slice 1.

20. **Strict mode raises `TranscriptSchemaError` on the first undeclared envelope field, naming the model path, the session and the line, and never the value.** *Evidence:* record 1 of `tests/fixtures/invented/invented-unknown-field.jsonl` — a spine-shaped `assistant` record with an invented top-level field whose value is `SUPER-SECRET-PAYLOAD-9f2a`; assert the path `assistant.<field>`, the session, the line, and the tripwire's absence. Invented, and labelled so in the fixture README.
21. **The walk descends into nested models, so an invented key inside `message.usage` is caught with its full dotted path.** *Evidence:* record 2 of the same fixture; assert the reported path is `assistant.message.usage.<field>`. Bolded: `usage` is where Claude Code adds fields most often, and a shallow walk would pass this design while proving nothing.
22. **The walk stops at an opaque model: an invented key inside `toolUseResult` raises nothing and tallies nothing, in strict mode and in lax.** *Evidence:* record 3 of the same fixture — a `user` record whose `toolUseResult` carries an invented key — read once with `strict=True` (no exception) and once with `strict=False` (`report() == ""`). Bolded: this is the boundary the design's Q8 answer buys, and a walk that ignored `OPAQUE` would still pass obligations 20 and 21.
23. An undeclared key on an `ArchivedRecord` is likewise not noticed. *Evidence:* the `attachment` and thin-`system` records in `registry_zoo`, read in strict mode; assert no exception. 24k `attachment` records carry keys no model declares by construction, so this is the leaf that keeps the archive out of the tally.
24. Lax mode tallies instead of raising: one entry per model path, holding the first `(session, line)` seen and a count of distinct sessions. *Evidence:* `invented-unknown-field.jsonl` read twice under two session ids; assert one entry per path, the first sighting names the *first* session and its line, and the session count is 2.
25. `report()` is empty when nothing was seen, and a tally line names the path, the first sighting and the session count when something was. *Evidence:* assert `report() == ""` after `tests/fixtures/model_only/`, and assert the rendered line's fields on the invented one. The tripwire assertion runs on the report too.
26. The tally is per-run state that survives across sessions and is reset when the run is. *Evidence:* one `UnknownFields` fed two sessions; the count aggregates rather than resetting.

## integration — the extractor over the recorded fixtures (`tests/extract/test_claude_code*.py`, `test_layout.py`, `test_store.py`)

`ClaudeCodeExtractor.extract` over real redacted transcripts, producing a `SessionTrace`. **These files do not change.** They are the seam that proves reader slices 3-7.

27. **Every existing leaf in `test_claude_code.py`, `__agents.py`, `__archive.py`, `__forks.py`, `__texture.py` and `__tools.py` passes unedited after each of slices 3-7.** *Evidence:* `uv run pytest tests/extract -q` green at every slice boundary, with `git diff --stat main -- tests/extract/test_claude_code*.py` empty. Bolded: an edit to one of these files during a reader slice is the signal that the refactor changed behaviour, and the diff is the only thing that catches it.
28. Every tier that builds a store from the fixture corpus stays green, under `UNIT_TESTING=1`. *Evidence:* `mise run check` at each slice boundary — `tests/view/`, `tests/enrich/`, `tests/export/` and `tests/analyze/` all read stores built by `tests/conftest.py:build_store`, so all of them run the strict walk. Obligation 9 is what makes this reachable.
29. `Line.record` is a `Record` and `Line.fields` is gone. *Evidence:* slice 7 deletes the attribute; pyrefly under `mise run check-fast` fails on any surviving reader, so the type check is the evidence.
30. `timestamp_of` returns `None` only for a record whose model declares no `timestamp`, and its two callers in `agent_runs.py` and `replays.py` still place their rows. *Evidence:* `test_claude_code__agents.py`'s run-tree assertions and `test_claude_code__forks.py`'s replay assertions, unedited; plus obligation 3, since `ArchivedRecord` gaining `timestamp` is what keeps the archive rows timed.

## integration — the CLI (`tests/test_cli.py`)

`hp extract` end to end over a temporary projects root, as `make_projects_root` already builds one. Slice 2.

31. `hp extract` prints the unknown-field tally after its `"N session(s) extracted"` summary, one line per path with its first sighting and session count. *Evidence:* a projects root holding `invented-unknown-field.jsonl`, run with `UNIT_TESTING` unset so lax is in force; assert both the summary line and the tally line in captured stdout, and assert the tripwire absent.
32. A clean extract prints no tally line at all. *Evidence:* the same run over `tests/fixtures/model_only/`; assert stdout is exactly the summary line.
33. `hp extract` exits 0 when the tally is non-empty. *Evidence:* assert the return code from the run in obligation 31 — the design rejects an exit-code flag, so this leaf pins the decision.
34. The extractor owns one `UnknownFields` per instance and exposes it, so the CLI can read the tally after `refresh()`. *Evidence:* obligation 31 is only reachable if the seam exists; additionally assert two `ClaudeCodeExtractor` instances hold independent tallies.

## corpus — the census (`tests/extract/test_records__census.py`, `HYPHAE_LIVE_STORE`-gated)

Every `raw_records` row of the canonical store through its model. Skipped in CI and `mise run check`; run by hand. Slice 2.

35. **Every row of `raw_records` in the live store validates against the model `model_for` returns for it.** *Evidence:* `HYPHAE_LIVE_STORE=/Users/nob/repos/hyphae/data/traces.duckdb uv run pytest tests/extract/test_records__census.py`, over a copy taken with its WAL; the assertion is on counts and a grouped `(type, model, field path)` summary, **never on a row's text** — a `raw_records` row reprs as transcript content and a failing assertion prints its operands. Marked `@pytest.mark.slow` with a why-comment. Bolded: 703,766 records against 253 modelled ones in the fixtures, and the memory of this project says the real-corpus leaves are what catch the shapes fixtures cannot.
36. No live-store row carries an undeclared envelope field, reported grouped by record type, field path and Claude Code version. *Evidence:* the same sweep, collecting rather than raising; the failure message is the declaration list. **Expected red when first run and green before slice 3** — a Claude Code version the fixtures do not cover will carry envelope fields they do not show, and the design routes each one to a declaration with a fixture citation or a `Cited(scan=…)`.
37. The census reports keys inside `toolUseResult`, archived kinds and dict leaves separately, rather than failing on them. *Evidence:* the same sweep prints an opaque-side section whose count is non-zero and which no assertion reads. The design promises "the census still prints every one of them when asked", and a section nobody can see is not that.
38. `ToolUseResult`'s string and list forms both validate across the corpus. *Evidence:* the same sweep; a per-form count asserted non-zero, so a form that never appears is a finding rather than a silent pass.
39. `UserMessage.content` holds no block kind beyond `text` and `tool_result` anywhere in the corpus. *Evidence:* the same sweep; a discriminator error on a `user` record is what falsifies the hypothesis. Bounded absence: the sweep reads every row, so the claim carries the count it read.
40. The census is skipped, not failed, when `HYPHAE_LIVE_STORE` is unset. *Evidence:* `uv run pytest tests/extract/test_records__census.py -q` with the variable unset reports skipped; this keeps `mise run check` offline and private.

## structural — the pins and the retirement

41. **`rg '\["[a-zA-Z_]+"\]' src/hyphae/extract/` finds nothing after slice 7, bar the `.meta.json` sidecar read.** *Evidence:* a leaf in `test_records.py` reading every module under `src/hyphae/extract/` and asserting no bracket-string read survives, with `meta["…"]` — `agent_runs.py:67`, on the sidecar the design keeps — subtracted first. The scope is the package rather than `transcript.py`, because the readers moved into `parse.py` when the file hit its budget and a pin on one module follows them nowhere. Bolded: it is the one thing that keeps a reader from quietly going back to the dict.
42. `tests/extract/test_records__drift.py` is deleted, and nothing imports it. *Evidence:* the file is absent at slice 8 and `mise run check` is green — the four leaves it loses (the read-direction grep and its three vacuity gates) are what the Q1 answer retires, and obligations 1, 2 and 41 carry what survives.
43. `OBSERVED_UNREAD` and `UNMODELLED` are gone from `shapes.py`, replaced by `ARCHIVED_UNREAD`. *Evidence:* `rg 'OBSERVED_UNREAD|UNMODELLED' src tests` finds nothing; obligation 2 covers the replacement.
44. `UNIT_TESTING=1` reaches a bare `uv run pytest` on one file, not only `mise run test`. *Evidence:* a leaf asserting `hyphae.settings.UNIT_TESTING is True`, run as `uv run pytest tests/extract/test_transcript__unknown.py::<that leaf>`; it fails if the variable is set anywhere but `[tool.pytest.ini_options] env`. This makes the design's rejection of `conftest.py` and `mise.toml` real.
45. `mise run mutate` inherits the variable too. *Evidence:* the mutation task over `hyphae.extract.records.unknown.*` reports a survivor count rather than a run of errors — a leaf that never sees strict mode kills nothing.
46. `docs/schema.md` regenerates cleanly and its cog blocks are fresh after slice 1's declarations and again after slice 8. *Evidence:* `mise run cogs` then `mise run cogs-check` green; the four `tools.gen_schema` blocks at lines 21, 43, 74 and 95 are what grow.
47. `CONTEXT.md` gains *record model* and `.claude/rules/python.md`'s "record what you relied on" rule is restated as structural, with the opaque boundary named — a reader has to learn that `toolUseResult` is not covered. *Evidence:* `mise run check-fast` passes the doc gates, and the `doc-sync` pass names both files plus `docs/schema.md` in the PR.

## Mutation obligations

48. The `UnknownFields` walk, the `OPAQUE` stop and the error formatter survive `mise run mutate`. *Evidence:* `mise run mutate 'hyphae.extract.records.unknown.*'` and `'hyphae.extract.errors.*'` run cold and serial, with every survivor either killed by a new assertion or named as a branch nothing can reach. Expect the recursion guard, the `OPAQUE` truthiness check and the first-sighting bookkeeping to be where survivors land — a mutant that inverts the opaque test should die on obligations 22 and 9 together.

## Deliberately not covered

- **The 0.3×-`json.loads` cost claim.** A wall-clock ratio is not a suite obligation: machine-dependent and flaky. The `--durations=10` footer on `mise run test` is the standing signal
- **The interior of `toolUseResult`, of an archived kind, or of a dict leaf.** Not covered by design, not by omission. Obligations 22, 23 and 37 pin that the silence is deliberate and visible on request
- **Live delivery, enrichment and OTLP.** Untouched; their existing leaves are the regression net (obligation 28)
- **A model per thin `system` subtype.** Out of scope, so no leaf asserts fields on one beyond obligations 3 and 5
- **The meaning of a documented field.** Neither the retired grep nor corpus validation ever checked it. Only a person opening a transcript does, and the `Cited` recording is where that check is written down

## Count

49 obligations, the last added while implementing. None unreachable through the design's seam.

## As built

Written after the eight slices landed. Each note is a place the code and this plan diverged; every other obligation was discharged as written.

- **The parser is two modules.** `transcript.py` hit its 700-line budget during slice 4 and split into `transcript.py` (the file as lines, and what the file says about the session) and `parse.py` (`parse` and its four readers). `required` and `required_timestamp` are shared, so they lost their underscore. Obligation 41 is rewritten above to pin the package instead of the one module
- **Obligation 27's freeze ends at slice 7, as written.** `git diff --stat main -- tests/extract/test_claude_code*.py` was empty through slice 7; slice 8 adds obligation 49's leaf to `test_claude_code.py` and nothing else
- **49. A `pr-link` record missing `prNumber`, `prUrl` or `prRepository` crashes naming that field.** *Evidence:* three invented fixtures, one per field, since the reader stops at the first field it cannot read; the assertion names the field, the kind and the line, and asserts the tripwire absent. INVENTED — all 3,096 `pr-link` records on the recording machine carry all four fields (scanned 2026-09-04). Slice 3 added the `required()` calls behind `PrLink` and obligation 27 froze the file they belong in, so the leaf lands in slice 8
- **`UNCITED_BLOCKS` sits beside `ARCHIVED_UNREAD`.** The design named one excuse list; `ContentBlock.IMAGE` is a registered block kind with no model, so obligations 2 and 43 read both
- **`AssistantMessage.content` is declared as a list, not `str | list`.** All 390,236 assistant records in the store write a list (scanned 2026-09-04). Slice 5 narrows the model rather than adding an unreachable crash path to `_api_calls`, which moves that failure onto obligations 16 and 17
- **`ArchivedRecord` extends `SessionContext`, not `Identified`.** Obligations 4 and 5 pass
  under either mixin, which is how the first build shipped a session that lost its project
  (`handoff_2026_09_04_audit-records-as-parser.md`, finding 1). `tests/fixtures/system_sited/`
  is the recording that decides it — a session sited only by a `system/informational` record —
  and `test_claude_code__archive.py::test_a_session_sited_only_by_an_archived_record_still_reports_where_it_ran`
  is the leaf. It lands in the archive file rather than beside the other session-fact leaves
  because `test_claude_code.py` sits at its 700-line budget
- **`session_of` skips a record carrying `"cwd": null`.** The dict version tested for the key and would have chosen it, yielding a null project. Inferred harmless: the census found no such record
