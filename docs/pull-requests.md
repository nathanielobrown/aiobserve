# Pull requests

How to open a PR a reviewer understands fast. AI writes much of the code here, so it is **CRITICAL** that a human can understand each PR — the orientation and context most of all.

## Mechanics

Plain `git` owns branches and commits; `gh` owns PRs. Sessions are non-interactive — no `-i` flags. `.claude/settings.json` is the source of truth for which `git` and `gh` verbs are allowed.

**The flow:**

- Shape: one branch per task, cut from `origin/main`. Each commit is one reviewable change — small, atomic, message per [Commits](commits.md). The commit is the review unit; the PR is the branch's cover letter.
- History stays linear: when `main` moves, rebase onto `origin/main`, never merge it in
- Push once, when the branch is ready for review. `mise run check` is the local gate while you iterate.
- Docs before description: when the branch's code is finished, dispatch a `doc-writer` subagent (`.claude/agents/doc-writer.md`) to run its doc-sync process over the branch and fold the edits in — docs land in the same PR ([the rule](documentation.md#keep-docs-in-step-with-the-change))
- Open the PR with `gh pr create --title <title> --body-file <file>`; draft is a deliberate choice for a branch that isn't ready to be seen
- Review feedback becomes **fixup commits** (`git commit --fixup=<sha>`), so the reviewer re-reads just the round instead of the branch
- Before landing, fold the fixups (`git rebase --autosquash origin/main`). For a bigger reshape, prefer `git reset --soft origin/main` and recommitting over surgical splitting.
- Land by fast-forward (`git switch main && git merge --ff-only <branch>`), never the GitHub UI: "Rebase and merge" mints new, untested SHAs

## What makes a PR done

Three things, all in the same PR:

1. **It works and is verified** — `mise run check` is green, and the description flags whatever *isn't* instead of dumping every gate that passed
2. **Its docs came along** — the owning docs updated in the same PR ([the rule](documentation.md#keep-docs-in-step-with-the-change))
3. **A reviewer can understand it without reading the diff first, and knows where their judgment is needed** — see [Write the description for a human](#write-the-description-for-a-human)

## Write the description for a human

The diff shows *what* changed. The description carries what the diff can't — the *why*, and where a reviewer's judgment is needed. Review attention is the scarcest resource here: most of a diff is machine-written and gate-verified, so the description's job is to route the reviewer to the few decisions that need a human and to say why the rest is safe to skim.

A good description has:

- **Summary** — what changed and why, in one to three sentences. If the PR closes an issue, link to it. State intent, not mechanics: "Adds a span-tree importer so a session's tool calls are queryable by duration and cost (#12)" — not "adds `importer.py` with a `run_import` that loops over records."
- **The design it implements** — link the design doc, or copy the half-page sketch and test checklist from their [handoffs](handoffs.md) into the body. Never put a local handoff path in the PR. A reviewer needs to know how much design review already happened before the code existed. A change too small to design says so in a clause.
- **What the diff did to the design** — four short lists, so a reviewer who read the design learns what changed since: built as designed, deviations (the design said X, the code does Y, why), additions the design didn't call for, designed but not built and why. "None" is an answer; silence is not. The auditor writes these — it already reads the diff against the stated intent.
- **A diagram, when it earns its place** — see [Diagrams earn their place](#diagrams-earn-their-place)
- **Review guide** — the changes that need the reviewer's judgment, in the order to read them: for each, the files, the decision made, and the question the reviewer is settling ("is this the right boundary?", "does this hold when the transcript is truncated?"). Two to four items cover a typical PR. Close with one line naming what the rest of the diff is and which gate verified it, so skimming it is a decision, not a gamble. When the PR touches docs, put the doc diff first in the reading order — reading it is the fastest orientation a reviewer gets.
- **Verification** — the *exceptions*, not the routine `mise run check`. Cover anything the gates don't, any gate that is **not** green (say so; never let the diff imply green), any manual check you ran, and anything the reviewer should verify themselves. A behavior change also needs evidence, not a claim: paste the transcript of the run that shows it working. Evidence lets the reviewer judge the design instead of re-verifying the behavior.
- **Status / known issues** *(only when there are any)* — a known defect, an unresolved decision, or a not-ready part belongs up front. The description is where a reviewer learns this; never leave it to be discovered in the diff.
- **Links** *(optional)* — any reference not linked above that would help the reviewer

Keep it proportional: a one-line config bump needs a sentence, a new subsystem the full shape above.

Before drafting, invoke the `writing` skill — a description is prose a human reads, and the skill carries the house style guide.

**Avoid the traps AI descriptions fall into:**

- Narrating the diff file by file instead of stating intent — the diff already shows *what*; you owe the *why*
- Giving every change equal weight — narrating the mechanical bulk while the one decision that needs a human goes unmentioned
- A diagram that restates the file tree or re-draws the diff — it must add a view the diff can't (flow, ordering, state), or be skipped
- Leaving a known issue or open decision to be discovered in the diff — surface it under Status / known issues
- A wall of routine gate output ("format: clean, lint: clean, 92 passed…"), or its opposite, a bare "ran tests". Give the one-line green result, then only what is *not* green and what you verified by hand.
- Relative Markdown links copied from a doc — a PR body renders on github.com, not in the tree, so they 404. Use full URLs or backticked paths.
- Pasting real session data as evidence — transcripts carry whatever the agent read. Redact, or point at a fixture.

## Diagrams earn their place

GitHub renders fenced ` ```mermaid ` blocks in PR descriptions natively, so use them. A diagram of the new control or data flow, a component interaction, or a state machine saves the reviewer from reconstructing it out of the diff.

**Default to a diagram for any non-trivial change.** Skip it for a mechanical edit, a one-line fix, a pure rename, or a config change — and when the change rides a flow already drawn elsewhere, link to that diagram instead of redrawing it.

Pick the type by the question the PR answers:

| The PR changes…                               | Use                          |
| --------------------------------------------- | ---------------------------- |
| Control or data flow, pipeline stages, wiring | `flowchart`                  |
| Ordering / interaction across components      | `sequenceDiagram`            |
| A lifecycle or state machine                  | `stateDiagram-v2`            |
| A data-model shape                            | `classDiagram` / `erDiagram` |
| A refactor (show the move)                    | a before → after pair        |

Before creating a PR diagram, read [the Mermaid guide](mermaid-guide.md), the source of truth for syntax and local render checks. **One question per diagram** — if it mixes structure and flow, or pushes past ~20 nodes, split it.

**Promote durable diagrams into the tree.** When a PR diagram depicts lasting architecture — how components connect or how data flows, not just this PR's delta — commit it into the doc that owns the topic and link that doc from the PR body. A diagram left in the body dies with the PR. One in the tree stays maintained.

Before submitting a PR body with a Mermaid block, write the exact final body to a temporary Markdown file and validate it with `mise run diagram-check <file>`. PR descriptions are not committed files, so this is the only local gate that catches a Mermaid mistake before GitHub tries to render it.

## Before you submit

- [ ] `mise run check` green
- [ ] History is linear on `origin/main`, fixups folded, each commit an atomic reviewable change
- [ ] Docs synced into this PR
- [ ] If the description contains a Mermaid block, the exact final body was written to a Markdown file and passed `mise run diagram-check <file>`
- [ ] Description has: summary, the design (linked or inline, with its test checklist), what the diff did to that design, a diagram if it earns its place, review guide, verification — evidence for any behavior change, any gate **not** green noted — and links

After submitting, watch the PR's CI run to green: it runs the same `mise run check` on a Linux runner (`.github/workflows/check.yml`), so what it catches is what your machine hid. Wait on a predicate that terminates — take the workflow run id and poll `gh run view <id> --json status` until it reports `completed`. Never poll for the absence of pending checks; that state may never arrive.
