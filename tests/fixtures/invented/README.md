# `invented/` — shapes the corpus does not contain

**Every file here is invented.** Each stands for a shape no recorded mycelia session exhibits, which is
exactly why it needs a test: the extractor's job on these shapes is to crash, and a recorded session
cannot show a crash that has never happened.

Each file opens with a real-shaped `mode` record so the fixture is a plausible transcript, and the
invented record follows on a known line.

| File | Shape | Why no real session stands in |
| --- | --- | --- |
| `invented-unknown-type.jsonl` | `type: "telepathy"` | every `type` in the corpus is in the registry — that is the registry's whole claim |
| `invented-unknown-subtype.jsonl` | `system` with `subtype: "quantum_flux"` | all nine live subtypes are registered |
| `invented-unknown-block.jsonl` | an assistant `message.content` block of `type: "clairvoyance"` | the corpus holds eight block kinds, all registered. This is the shape that went wrong once already: `server_tool_use` was unregistered and produced no row and no crash |
| `invented-unknown-field.jsonl` | an `assistant` record with an undeclared top-level field | the models declare every field the recorded corpus carries — that is what `test_records.py`'s corpus leaf asserts — so an undeclared field cannot come from a recording. One file per position, because strict mode stops at the first one it meets |
| `invented-unknown-nested-field.jsonl` | an undeclared key inside `message.usage`, behind a clean record | `usage` is where Claude Code adds fields most often, and it is three models down from the record |
| `invented-unknown-block-field.jsonl` | an undeclared key inside a `thinking` block | `message.content` is a list of models, so the walk reaches a block only by stepping through the list |
| `invented-unknown-opaque-field.jsonl` | an undeclared key inside `toolUseResult` | the one that must report **nothing**: a tool's own report is an open set nobody here claims |
| `invented-wrong-field-type.jsonl` | an assistant record whose `isSidechain` and `message.usage.input_tokens` both hold a string | every recorded record validates — the census over the canonical store found zero failures in 705,431 records — so a wrongly typed field cannot come from a recording. Two of them in one record, because the message has to join several faults |
| `invented-novel-tag.jsonl` | a prompt string leading with `<sparkle-notice>` | the tag census closed over every main and subagent transcript |
| `invented-dup-content-diff.jsonl` | one uuid twice, with different `message.content` | 995 duplicate-uuid pairs exist and **none** differs in content; a difference would mean the conversation itself was rewritten under one uuid |
| `invented-no-cache-creation.jsonl` | an assistant `usage` with no `cache_creation` key | scanned every assistant record in the corpus: zero lack the key, so "absent, not zero" has no recorded example (see the note below) |
| `invented-truncated-tail.jsonl` | a final line cut mid-JSON | a transcript Claude Code is still appending to. The extractor **warns and drops the line** here rather than crashing — the one file in this directory whose expected outcome is not a crash |
| `invented-no-timestamp.jsonl` | a `pr-link` record with no `timestamp` key | scanned every `user`, `assistant`, `system` and `pr-link` record on the recording machine — 678,793 of them, none missing the key. Only those four kinds reach the raise; the bookkeeping types that do carry no timestamp and need none |
| `invented-no-pr-number.jsonl`, `invented-no-pr-url.jsonl`, `invented-no-pr-repository.jsonl` | a `pr-link` record missing one of the three fields a `PrLink` row is built from | all 3,096 `pr-link` records on the recording machine carry all four fields (scanned 2026-09-04). One file per field, because the reader stops at the first one it cannot read |
| `invented-no-duration.jsonl` | a `system/turn_duration` record with no `durationMs` | all 3,592 `turn_duration` records on the recording machine carry one (scanned 2026-09-04). A default here would shorten a session's `active_ms` and still read as a number |
| `invented-corrupt-middle.jsonl` | the same broken line, with a complete record after it | corruption rather than a live write, so it crashes. The pair only means something read together: a tolerance that leaked to any line would turn a schema change into silent data loss |

Every file whose test asserts on a message carries the string `SUPER-SECRET-PAYLOAD-9f2a` in the offending record; `grep -L` names the two that do not. That is the test's tripwire — a crash message or a log line that names it has leaked private transcript content.

## The `cache_creation` gap

`ApiCall.cache_5m_tokens` / `cache_1h_tokens` are `None` when `usage.cache_creation` is absent, so a
query can tell "this model never reported a TTL split" from "the split was zero". The design states the
behaviour; the corpus cannot evidence it. This fixture pins the behaviour we chose, not a shape Claude
Code was observed to emit — if the key turns out to be unconditional, the `None` branch is dead code
and should go.
