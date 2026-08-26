# Documentation

Use this guide to decide where project documentation belongs and how to keep it current. Start with the documentation summary in `CLAUDE.md`, then follow the [writing style guide](writing_style_guide.md) as you write.

## Give each fact one home

| Content | Home |
| --- | --- |
| Repository-wide context and conventions that every session needs | `CLAUDE.md` |
| The canonical name and one-line meaning of a domain or viewer concept | `CONTEXT.md`, imported into every session by `CLAUDE.md` |
| Conventions for a set of files, such as tests | `.claude/rules/` |
| A guide to one project topic | `docs/`, linked from the `CLAUDE.md` Layout tree |
| This guide | `docs/documentation.md` |
| The meaning and source of a telemetry field | `docs/schema.md` |
| A picture of how parts connect or data moves | A ` ```mermaid ` block in the document that owns the topic; see [the Mermaid guide](mermaid-guide.md) |
| A finding about an AI coding agent and its evidence | `reports/`; see [the report guide](../reports/README.md) |
| A bug, feature, or design question that needs action | A GitHub issue |
| A design or test plan that should remain after the change lands | `plans/<change>/`, committed on the implementing branch |
| Scratch passed between agent runs | `handoffs/`; see [the handoff guide](handoffs.md) |

Keep details about a module, function, or configuration beside the code in comments or docstrings.

## Prefer facts that update themselves

Choose the first form that fits:

1. **Explain how to find the fact.** Write "every task in `mise.toml`" instead of copying the task list.
2. **Link to the source of truth.** Point to the code for behavior and to `mise.toml` for commands.
3. **State the fact in prose.** Do this only when the other forms would hide it or make it harder to understand.

A plan may list callers, files, or commands to make a design concrete. Treat those lists as claims to check during implementation, not as lasting records.

Telemetry schemas need stronger evidence because the harness owns them and may change them without notice. Never list transcript fields from memory. Cite a recorded session and the Claude Code version that produced it.

## Make references work where readers find them

Write references as backticked paths or Markdown links:

- In `CLAUDE.md` and `.claude/rules/`, use short backticked paths. Use a Markdown link for an anchor, an external URL, or a name without a path
- In other prose documents, link to prose by name, as in `[the PR guide](pull-requests.md)`. Use backticked paths for source artifacts such as code and diagrams

Markdown links resolve from the file that contains them. Backticked paths resolve from the repository root. Paths in code comments, docstrings, TOML, and YAML also resolve from the repository root and must match the target's case.

## Keep each source paragraph on one line

Write each paragraph and list item on one physical line. Let the editor wrap it. Fenced code blocks are exempt.

## Keep shared agent guidance in one place

`.claude/rules/` and `.claude/skills/` are the sources for project rules and skills used by our AI coding agents.

`@` imports work in rules and skills loaded by a harness, but they don't work in agent prompts. Tell a subagent to read a path from the repository root instead.

A skill should usually import the document that owns its procedure and keep only enough text to orient the agent. Keep a short procedure in the skill itself when no separate document needs it.

## Update documentation with the code

After implementation, dispatch the `doc-writer` agent in `.claude/agents/doc-writer.md`. Its `doc-sync` skill checks the branch and reports what to update or why no update is needed. The `pr` skill runs this step.

After an analysis run, update the report under `reports/` and any guidance changed by the finding in the same change.
