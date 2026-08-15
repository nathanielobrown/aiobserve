# Documentation

Use this guide after the documentation summary in `CLAUDE.md` when you create or edit project documentation. It adds placement rules and repository mechanics. Follow the [writing style guide](writing_style_guide.md) for prose.

## Find the right home first

| Content | Home |
| --- | --- |
| Repo-wide conventions and context worth loading every session | `CLAUDE.md` |
| Conventions scoped to particular files, such as tests | `.claude/rules/` |
| A guide to one project topic | `docs/`, linked from the `CLAUDE.md` Layout tree |
| This documentation guide | `docs/documentation.md` |
| What a telemetry field means and where it comes from | `docs/schema.md` |
| A picture of how parts connect or data moves | a ` ```mermaid ` block in the doc that owns the topic; see [the Mermaid guide](mermaid-guide.md) |
| A finding about an AI coding agent, with the evidence behind it | `reports/`; see [the report guide](../reports/README.md) |
| An actionable bug, feature, or design question | a GitHub issue |
| A design or testing plan for one change, kept after it lands | `plans/<change>/`, committed |
| Scratch passed between agent runs | `handoffs/`; see [the handoff guide](handoffs.md) |

Put module, function, and configuration details in comments or docstrings beside the code.

## Keep it from rotting

Use the first suitable form:

1. **Describe how to discover it.** Write "every task in `mise.toml`" rather than listing the current tasks.
2. **Reference the source of truth.** Point to code for behavior and `mise.toml` for commands.
3. **State it in prose.** Use this only when the other forms would make the fact harder to find or understand.

Treat enumerated facts in a plan as claims to verify during implementation. References survive changes better than copied lists of current callers, files, or commands.

Telemetry schemas rot fastest of all, because the harness owns them and changes them without telling us. Never enumerate a transcript's fields from memory — point at a real recorded session, and say which version of Claude Code produced it.

## Cross-references

Write references as either backticked paths or Markdown links:

- In `CLAUDE.md` and `.claude/rules/`, use compact backticked paths. Use a Markdown link for an anchor, an external URL, or a slashless name.
- In other prose documents, link to prose with descriptive text, such as `[the PR guide](pull-requests.md)`. Use a backticked path for source artifacts such as code or diagrams.

Markdown links resolve relative to the current file. Backticked paths resolve from the repository root. References in code comments, docstrings, TOML, and YAML also resolve from the repository root and must match the target's case.

## One line per paragraph

Write each paragraph and list item on one physical line and let the editor wrap it. Fenced code blocks are exempt.

## Rules and skills across harnesses

`.claude/rules/` and `.claude/skills/` are the sources of project rules and skills for the AI coding agents we use.

`@` imports work in harness-loaded rules and skills, but not in agent prompt bodies. Tell a subagent to read a repo-root path; an `@` in its prompt is inert text.

A skill should normally import the document that owns its procedure. Keep only enough text in the skill to orient the agent. A skill may carry its own short procedure when no separate document needs it.

## Keep docs in step with the change

Run doc sync after implementation by dispatching the `doc-writer` agent in `.claude/agents/doc-writer.md`. Its `doc-sync` skill checks the branch and reports either the updates or why none are needed. The `pr` skill includes this step.

After an analysis run, update the record in the same change: the report under `reports/` and any guidance the finding revises.
