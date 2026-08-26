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
| The meaning and source of a telemetry field | Its field on a record model in `src/aiobserve/extract/records/`, which `docs/schema.md` prints |
| A table restating something the code already holds | A generator in `tools/`, spliced into the document; see [Generate a table from the code that owns it](#generate-a-table-from-the-code-that-owns-it) |
| A picture of how parts connect or data moves | A ` ```mermaid ` block in the document that owns the topic; see [the Mermaid guide](mermaid-guide.md) |
| A finding about an AI coding agent and its evidence | `reports/`; see [the report guide](../reports/README.md) |
| A bug, feature, or design question that needs action | A GitHub issue |
| A design or test plan that should remain after the change lands | `plans/<change>/`, committed on the implementing branch |
| Scratch passed between agent runs | `handoffs/`; see [the handoff guide](handoffs.md) |

Keep details about a module, function, or configuration beside the code in comments or docstrings.

## Prefer facts that update themselves

Choose the first form that fits:

1. **Generate the fact from the code that owns it.** A table restating code is written by a generator and spliced in, so the code and the document cannot disagree. See [Generate a table from the code that owns it](#generate-a-table-from-the-code-that-owns-it).
2. **Explain how to find the fact.** Write "every task in `mise.toml`" instead of copying the task list.
3. **Link to the source of truth.** Point to the code for behavior and to `mise.toml` for commands.
4. **State the fact in prose.** Do this only when the other forms would hide it or make it harder to understand.

A plan may list callers, files, or commands to make a design concrete. Treat those lists as claims to check during implementation, not as lasting records.

Telemetry schemas need stronger evidence because the harness owns them and may change them without notice. Never list transcript fields from memory. Cite a recorded session and the Claude Code version that produced it.

## Generate a table from the code that owns it

A table a reader needs spelled out — the viewer's routes, the fields a record carries — comes from a generator in `tools/` and is spliced into the document by `mise run cogs`. The document holds the markers and the generator holds the text:

```markdown
<!-- aigarden:cog sh "uv run python -m tools.gen_routes" -->
| Page | Route | Description |
| --- | --- | --- |
<!-- aigarden:end -->
```

The command runs from the repository root, and everything between the markers is replaced by what it prints. Run `mise run cogs` after changing a generator or the code it reads; `mise run check` runs the same command and fails when what the document holds is not what it prints, so a generated table cannot be stale and green at once. Never edit between the markers by hand — the next write erases it, and until then the check is red.

A generator exposes `generate()`, which returns the block's body with no trailing newline, and a `main()` that prints it. Everything the block needs to say goes in the generator, including any heading or fence.

## Make references work where readers find them

Write references as backticked paths or Markdown links:

- In `CLAUDE.md` and `.claude/rules/`, use short backticked paths. Use a Markdown link for an anchor, an external URL, or a name without a path
- In other prose documents, link to prose by name, as in `[the PR guide](pull-requests.md)`. Use backticked paths for source artifacts such as code and diagrams

Markdown links resolve from the file that contains them. Backticked paths resolve from the repository root. Paths in code comments, docstrings, TOML, and YAML also resolve from the repository root and must match the target's case. `mise run check` holds the whole repository to this, so a reference that no longer resolves is a red gate rather than something the next reader discovers.

To move a document, run `mise run mv-doc <src> <dst>`: it moves the file and rewrites every reference to it, in either form, then re-checks that the repository's links still resolve.

## Keep a document short enough to load

Every file carries a size budget: code in readable lines, prose in the context tokens it costs a reader who loads it. `mise run check` reports a file over its budget, and `aigarden explain file-length` prints the budgets. A document past its budget is one a reader skims and an agent loads whole to reach the paragraph it needed.

`aigarden.toml` names every file over budget today, each held where it is rather than the budget being raised for everyone, so a new document meets the rule. An entry comes out when the file it names is split. Get under by cutting ideas or moving a topic to its own document, not by compressing sentences.

## Keep each source paragraph on one line

Write each paragraph and list item on one physical line, and let the editor wrap it: a reworded sentence is then a one-line diff, and an AI author writing beside our prose mirrors the convention it reads. Fenced code blocks and tables keep their own line breaks. `mise run check` holds prose to this, and `mise run check-fast` rejoins a paragraph someone wrapped.

## Keep shared agent guidance in one place

`.claude/rules/` and `.claude/skills/` are the sources for project rules and skills used by our AI coding agents.

`@` imports work in rules and skills loaded by a harness, but they don't work in agent prompts. Tell a subagent to read a path from the repository root instead.

A skill should usually import the document that owns its procedure and keep only enough text to orient the agent. Keep a short procedure in the skill itself when no separate document needs it.

## Update documentation with the code

After implementation, dispatch the `doc-writer` agent in `.claude/agents/doc-writer.md`. Its `doc-sync` skill checks the branch and reports what to update or why no update is needed. The `pr` skill runs this step.

After an analysis run, update the report under `reports/` and any guidance changed by the finding in the same change.
