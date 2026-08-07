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
| `invented-novel-tag.jsonl` | a prompt string leading with `<sparkle-notice>` | the tag census closed over every main and subagent transcript |
| `invented-dup-content-diff.jsonl` | one uuid twice, with different `message.content` | 995 duplicate-uuid pairs exist and **none** differs in content; a difference would mean the conversation itself was rewritten under one uuid |
| `invented-no-cache-creation.jsonl` | an assistant `usage` with no `cache_creation` key | scanned every assistant record in the corpus: zero lack the key, so "absent, not zero" has no recorded example (see the note below) |

The first three carry the string `SUPER-SECRET-PAYLOAD-9f2a` in the offending record. That is the
test's tripwire: a crash message that names it has leaked private transcript content into a log.

## The `cache_creation` gap

`ApiCall.cache_5m_tokens` / `cache_1h_tokens` are `None` when `usage.cache_creation` is absent, so a
query can tell "this model never reported a TTL split" from "the split was zero". The design states the
behaviour; the corpus cannot evidence it. This fixture pins the behaviour we chose, not a shape Claude
Code was observed to emit — if the key turns out to be unconditional, the `None` branch is dead code
and should go.
