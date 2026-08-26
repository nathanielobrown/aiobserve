# Sync documentation with a change

Use this process after the code is done and before writing the PR description. It brings the owning docs into the same PR as the change they describe, as required by [the documentation guide](documentation.md#update-documentation-with-the-code). A dispatched `doc-writer` (`.claude/agents/doc-writer.md`) normally runs the sync for a fresh review; the `pr` skill includes it in the PR flow.

Read [the documentation guide](documentation.md) before you start. It defines where facts belong and how project docs should refer to them.

## Change docs, not behavior

- Edit documentation only; never change code, tests, or behavior
- Keep each fact in one place and link to it elsewhere; prefer instructions for finding current facts over copied lists
- Keep always-loaded files brief: a gloss and a link belong there, while the owning doc holds the detail
- Name every doc you changed and every plausible doc you checked but left alone, with the reason
- Make only the updates the change requires; flag uncertain calls for a human instead of guessing

## Work from the branch diff

1. **Scope the change.** Read `git diff origin/main...HEAD` and inspect the changed files. Look for added, renamed, or deleted files; public interface changes; new terms; changed behavior; and decisions the code now embodies.

2. **Find the docs that own those facts.** Use [Give each fact one home](documentation.md#give-each-fact-one-home), then open each likely owner and check whether the diff has made it stale. These changes often require a doc update:
   - A new top-level module or package needs a docstring whose first sentence reads as its gloss: the `CLAUDE.md` Layout tree is generated from those, so run `mise run cogs` rather than editing the tree
   - A changed telemetry attribute, span name, or transcript field belongs in [the schema reference](schema.md), backed by the recorded session and Claude Code version that prove it
   - A changed extraction or analysis command may affect the README usage section and its task description in `mise.toml`
   - A changed component relationship or flow may require a Mermaid diagram in the doc that owns the topic; follow [the Mermaid guide](mermaid-guide.md), and update diagrams the change has made stale
   - A finding that changes what the project believes about an agent's behavior belongs in the owning report under `reports/`
   - The reason for a configuration setting or dependency belongs in a comment beside that setting, not in a prose doc; if the comment is missing, flag it rather than changing code during doc sync

   If an owning doc is a stub or contains only a `TODO`, don't fill it by assumption. Flag it for a human.

3. **Update only stale docs.** Link to sources of truth instead of repeating them. Keep `CLAUDE.md` and its imports scannable. If you need more than a sentence or two of new prose, invoke the `writing` skill before drafting.

4. **Validate your edits.** Run `mise run check-fast`: it rejoins any paragraph you hard-wrapped and reports a link, anchor, or path that doesn't resolve. Run `mise run diagram-check <file>` for each changed Mermaid diagram.

5. **Report the audit.** Use the template below so the reviewer can see both the edits and the coverage.

## Audit without editing

For review or a coverage check, run steps 1–2 and report what you found. Skip the edits and validation, and label the report `audit-only`.

## Report template

```
## doc-sync report

**Scope:** <branch> vs <base> — <number of files and the nature of the change>

**Updated**
- `path` — <what changed and why>

**Considered, left unchanged**
- `path` — <why no change was needed>

**Flagged for a human**
- <judgment calls>
```
