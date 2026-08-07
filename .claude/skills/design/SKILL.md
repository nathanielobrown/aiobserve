---
name: design
description: The design-artifact format. Invoke before writing or auditing a design.
---

# Design artifacts

A design says what will be built and why that shape.

Write it as a handoff `design-<topic>` ( @../../../docs/handoffs.md ). It is per-run scratch: the PR body carries it forward, so the reviewer sees the design and the diff together ( @../../../docs/pull-requests.md ).

## The design

Half a page to two pages — a cap set for leverage, not coverage. The reader has to be able to agree, disagree, or re-steer without loading the whole change into their head.

- **Problem** — what is missing or wrong today, and the constraint that decides the shape
- **Call paths, current → proposed** — how the flow runs now and how it runs after, in real symbols and real file paths
- **File-tree diff** — what is added, changed, deleted
- **Key contracts** — types, signatures, schema changes. A reader who disagrees with one of these disagrees with the design
- **Chosen test seam** — the interface the tests drive, and the level they run at
- **Slices** — the build order: each slice crosses the layers it needs and is verifiable on its own by a check or a test you name. Slice one proves the seam and one representative behavior. Never shape slice N so that only slice N+1 can verify it
- **Decisions** — a line each, with the alternative rejected and the reason. A decision with no rejected alternative was not a decision
- **Out of scope** — what a reader would reasonably assume is included and isn't, and why. An unstated omission reads as an oversight
- **Open questions** — what you could not settle, and what would settle it

## Traps

- **Design against a real session, not a remembered schema.** Anything that reads Claude Code's transcripts or spans is designed against a shape we don't control. Name the recorded session the design assumes, and treat a field you haven't seen in one as an open question ( `docs/schema.md` )
- A design that only works on today's schema needs to say what happens when the schema moves — crash, or degrade, and where
