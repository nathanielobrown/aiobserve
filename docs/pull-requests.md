# Pull requests

Use this guide to open a PR that a reviewer can understand before reading the diff. Because AI writes much of this project's code, the PR must make the intent, design, and open questions clear to a human.

A finished PR has working code, a green `mise run check`, current owning docs, and a description that tells the reviewer where to spend judgment. If you open the PR before then, say what's missing at the top of the description.

## Shape the branch for review

Plain `git` owns branches and commits; `gh` owns PRs. Sessions are non-interactive, so don't use `-i` flags. `.claude/settings.json` defines the allowed `git` and `gh` commands.

1. Create one branch per task from `origin/main`.
2. Shape the work into atomic commits. Each commit should be one reviewable change with a message that follows [the commit guide](commits.md). The commit is the review unit; the PR is the branch's cover letter.
3. Keep history linear. If `main` moves, rebase onto `origin/main`; never merge `main` into the branch.
4. Run `mise run check`.
5. Sync the docs before writing the PR description. Dispatch the `doc-writer` subagent in `.claude/agents/doc-writer.md` to run doc-sync over the finished branch, then fold its edits into the branch. The docs belong in the same PR as the code; see [the documentation rule](documentation.md#keep-docs-in-step-with-the-change).
6. Push once, after the branch is ready, then open the PR with `gh pr create --title <title> --body-file <file>`. Use a draft only when asking for review before the work is ready to land.

## Start the title with the change type

Start the title with emoji from the table in @commits.md, then a plain statement of the change. Multiple emoji are allowed when multiple would apply, but focus on what's important to convey. For example, documentation with a feature does not need the documentation emoji.

## Write the description for a human

The diff shows what changed. The description explains why, relates the code to its design, and directs the reviewer to decisions that need human judgment. Machines can verify much of a diff; don't make a reviewer hunt for the parts they can't.

Before drafting, invoke the `writing` skill. Keep the description proportional: a one-line config change may need one sentence, while a new subsystem needs the full structure below.

### Put known problems first

If the PR has a defect, unresolved decision, failed gate, or unfinished part, open with **Status / known issues**. Don't let the reviewer discover it in the diff.

### State the intent

Under **Summary**, say what changed and why in one to three sentences. Link an issue when the PR closes one. Describe the result, not the files: “Adds a span-tree importer so a session's tool calls are queryable by duration and cost (#12),” not “Adds `importer.py` with a `run_import` loop.”

### Show the design and what changed

Under **Design**, link the design doc or copy the half-page sketch and test checklist from the [handoff](handoffs.md) into the PR body. Never link a local handoff path. If the change was too small to need design work, say so in a clause.

Then compare the finished diff with that design under four labels:

- **Built as designed**
- **Deviations** — what the design said, what the code does, and why
- **Additions** — what the code adds beyond the design
- **Not built** — what the design included but the code omits, and why

Write “None” where needed. Silence forces the reviewer to compare the plan and diff alone.

### Direct the review

Under **Review guide**, list the places that need judgment in reading order. For each item, name the files, the decision, and the question the reviewer should settle: “Is this the right boundary?” or “Does this hold when the transcript is truncated?” Two to four items suit most PRs.

Put documentation first when the PR changes it; the docs orient the reviewer faster than the code. End with one line naming the rest of the diff and the gate that checked it, so the reviewer can skim by choice rather than gamble.

### Report verification and evidence

Under **Verification**, give the one-line `mise run check` result and name every gate that isn't green. Don't dump routine gate output or write only “ran tests.” Report what the gates don't cover: manual checks, behavior the reviewer should verify, and other exceptions.

A behavior change needs evidence. Include redacted command output that shows the behavior or point to a safe fixture. Never paste real session data; transcripts may contain source, credentials, or customer data.

Add **Links** only for useful references not linked above. Use full URLs or backticked repository paths; relative Markdown links from a doc won't resolve in a PR body on github.com.

## Add a diagram only when it answers a question

GitHub renders Mermaid code fences in PR descriptions. Use a diagram when it saves the reviewer from reconstructing flow, ordering, state, or structure from the diff. Default to one for a non-trivial change; skip it for a mechanical edit, one-line fix, pure rename, or config change. Don't redraw the file tree or diff. If an existing diagram answers the question, link it instead.

Choose the type from the question:

| The PR changes… | Use |
| --- | --- |
| Control or data flow, pipeline stages, wiring | `flowchart` |
| Ordering or interaction across components | `sequenceDiagram` |
| A lifecycle or state machine | `stateDiagram-v2` |
| A data-model shape | `classDiagram` or `erDiagram` |
| A refactor | a before → after pair |

Read [the Mermaid guide](mermaid-guide.md) before drawing. Give each diagram one question. If it mixes structure with flow or grows past about 20 nodes, split it.

Put a lasting architecture diagram in the doc that owns the topic and link that doc from the PR. A diagram kept only in a PR body won't stay maintained.

Before submission, write the exact PR body to a temporary Markdown file and run `mise run diagram-check <file>`. PR descriptions aren't committed, so no other local gate will catch their Mermaid errors.

## Respond to review without hiding the round

Turn review changes into fixup commits with `git commit --fixup=<sha>`. This lets the reviewer read the new round instead of searching the whole branch.

Before landing, fold the fixups with `git rebase --autosquash origin/main`. For a larger reshape, prefer `git reset --soft origin/main` and recommit the branch rather than splitting commits surgically.

## Submit, verify, and land

Before opening the PR, check:

- [ ] The title starts with emoji from commits.md
- [ ] `mise run check` is green, or the description names every failure
- [ ] The branch is linear on `origin/main`, fixups are folded, and each commit is reviewable
- [ ] The owning docs are in the branch
- [ ] The description covers the summary, design, design comparison, review guide, and verification at a scale that fits the change
- [ ] Every behavior change has safe evidence
- [ ] Any Mermaid block in the exact final body passed `mise run diagram-check <file>`

After opening the PR, watch CI until it finishes green. CI runs `mise run check` on Linux through `.github/workflows/check.yml`, which may expose differences hidden by your machine. Poll a workflow run that can terminate: get its run ID, then run `gh run view <id> --json status` until the status is `completed`. Don't poll for the absence of pending checks; that state may never arrive.

Land with a fast-forward: `git switch main && git merge --ff-only <branch>`. Don't use GitHub's “Rebase and merge”; it creates new SHAs that weren't tested.
