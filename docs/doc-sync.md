# Doc sync

Bring the documentation into agreement with a changeset so the docs land in the same PR as the change they describe (the rule: [Keep docs in step with the change](documentation.md#keep-docs-in-step-with-the-change)). Run it when the branch's code is finished and **before the PR description is written** — the description puts the doc diff first in its reading order. Normally, a dispatched `doc-writer` subagent (`.claude/agents/doc-writer.md`) runs it, so the audit gets fresh eyes; the `pr` skill composes it into the PR flow. Read [documentation.md](documentation.md) first — it's the house style this process enforces.

## Contract

- **Docs only.** Never change code, tests, or behavior.
- **Reference, don't duplicate.** Every edit follows [documentation.md](documentation.md): one source of truth, discovery phrasing over enumerations. Always-loaded files stay a gloss plus a link; depth lives in the owning doc.
- **No silent gaps.** The report lists every doc you changed **and** every place you considered and left alone, with the reason. The reviewer can then judge coverage, not just see edits.
- **Conservative, and flag judgment calls.** Update what the change *requires*; don't rewrite docs that are merely nearby. When something needs a call you can't confidently make, flag it for a human instead of guessing.

## Procedure

1. **Scope the change.** Diff the branch against its merge base: `git diff origin/main...HEAD`. Read the diff and the changed files. Note new, renamed, and deleted files; new public symbols; new or shifted vocabulary; changed behavior; and any decision the change embodies.

2. **Map change → docs.** For each kind of change, consult the table in [Find the right home first](documentation.md#find-the-right-home-first) and open the owning doc to check it. Common triggers:
   - A new top-level module or package → its line in the `CLAUDE.md` Layout tree
   - A new or changed telemetry attribute, span name, or transcript field → `docs/schema.md`, with the recorded session that proves it
   - A new or changed extraction or analysis command → the README's usage section and `mise.toml`'s task description
   - New or changed component relationships or flows → a ` ```mermaid ` block in the doc that owns the topic (authoring rules: [mermaid-guide.md](mermaid-guide.md)). Refresh a diagram the change has outrun — a stale diagram misleads worse than none.
   - A finding that changes what we believe about an agent's behavior → the owning report under `reports/`
   - Why a config setting or dependency exists → a comment at the setting itself, not a doc

   If a mapped owning doc is a stub or `TODO`, don't force content into it. Flag it in the report so a human can decide whether to populate it now.

3. **Apply updates in house style.** Prefer a link to the source of truth over a restated fact. Keep `CLAUDE.md` and what it `@`-imports scannable — a gloss plus a link, not depth. When a doc needs more than a sentence or two of new prose, invoke the `writing` skill before drafting: you own the facts, it owns the sentences.

4. **Validate.** Check that every link and path you touched still resolves and that the case matches.

5. **Report** using the template below.

## Audit-only mode

Sometimes you only need the assessment — when reviewing someone else's PR or checking coverage without changing anything. Then run **steps 1–2 and report**, skipping steps 3–4. Say "audit-only" in the report so the reader knows nothing was changed.

## Report

```
## doc-sync report

**Scope:** <branch> vs <base> — <n files; one line on the nature of the change>

**Updated**
- `path` — <what changed and why>

**Considered, left unchanged**
- `path` — <why no change was needed>

**Flagged for a human**
- <judgment calls>
```
