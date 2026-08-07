---
name: testing-plan
description: The testing-plan format — the test-obligations tree and its evidence discipline. Invoke before writing or auditing a testing plan.
---

# The testing plan

A testing plan describes what automated tests should verify: obligations, written before any test code exists. The design binds the testing plan (the `design` skill carries the design's format).

Write it as a handoff `testing-plan-<topic>` ( @../../../docs/handoffs.md ), and carry it into the PR body beside the design.

A tree: top-level items are the level and location the tests run at, each described in a line — including what stands in for the world there: the real dependency, the fake, the recorded session. Under each, one leaf per behavior, ending with *Evidence:* naming what would prove it.

- Evidence names the artifact: the fixture, the assertion, the killed mutant, the recorded transcript. "It is tested" is not evidence, and a leaf without an *Evidence:* clause is not an obligation
- Pick the level closest to real behavior that the seam allows, and **prefer a recorded session to an invented record** ( `.claude/rules/testing.md` ). Say so on any leaf whose data is invented
- An obligation the design's test seam cannot reach is a design finding. Report it. Never drop the leaf, and never move it to a level where it proves less
- Bold the most critical or difficult leaves
- Say what is deliberately not covered, and why
- Leaves are obligations, not test code: the implementer discharges each one and the auditor traces it to the evidence the leaf named

## Example

The shape in miniature, for a hypothetical subagent-span design:

- **unit (parser)** — no I/O; transcript records in, span tree out
  - A sidechain's spans hang off the Agent call that spawned them, not off the session root. *Evidence:* redacted two-agent fixture from a real mycelia session; assertion on the parent id of each subagent span
  - An unrecognized record `type` raises rather than being skipped. *Evidence:* the assertion names the offending type in the error
- **integration (importer)** — real files on disk, a fake exporter collecting spans
  - **A session re-imported twice produces identical span ids** — bolded: idempotence is what makes the periodic re-scan safe. *Evidence:* two runs over the same fixture; the id sets compare equal
  - A truncated final line (a session still being written) imports the complete records and reports the truncation. *Evidence:* fixture with a half-written last line; the count and the warning are both asserted
- **not covered** — live delivery to a telemetry backend. It needs a key and a network, so it goes behind a marker and is exercised by hand
