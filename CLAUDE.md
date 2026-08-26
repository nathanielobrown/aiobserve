# Project Overview

aiobserve turns AI coding-agent telemetry — mostly traces — into queryable data and evidence-backed findings about how to improve agents. Claude Code is the first target.

The project is early. Don't optimize for feature speed or backward compatibility yet. Prefer clean breaking changes to compatibility shims and deprecation paths.

# Glossary

@CONTEXT.md is the project glossary: the canonical name and one-line meaning of every domain and viewer concept. When a change coins or bends a term, update the glossary in the same change.

# Every finding needs evidence

We make claims about other people's behavior from data we didn't design, so each finding stands or falls on its evidence:

- **A claim carries its query.** Include the dataset, filter, time window, and count. A number without its query is a hypothesis
- **An absence is bounded or it isn't a finding.** "No session did X" means nothing until you show that the data could have contained X
- **State the corpus.** One person's sessions on one codebase support claims about that codebase's guidance. Scope the recommendation to match
- **Correlation doesn't prove causation.** A guidance change and a metric shift in the same week are two facts, but need to considered with other changes

# Tooling

Use `mise` to run project tasks. `uv` owns the Python environment.

- After a fresh clone or dependency change, run `mise run sync`
- While iterating, run `mise run check-fast` for formatting, linting, and type checks
- Before you finish a task or open a PR, run `mise run check`. It also runs the tests and hook linter; GitHub runs it on every push and PR (`.github/workflows/check.yml`)
- Run any individual task listed in `mise.toml` with `mise run <task>`. Ruff formats and lints; Pyrefly checks types
- Run `mise run diagram-check <file>` to validate Mermaid and `mise run mutate` to score the suite against mutants (`.claude/rules/testing.md`)

Put `mise` flags before the task name. `mise run check --force` passes `--force` to the task, where it does nothing.

Change the tooling or add tools when they make the work easier or enforce project quality.

# Layout

```
src/aiobserve/        The package — `extract/` reads an agent's sessions, `export/` writes a sink, `enrich/` describes what it found, `analyze/` asks the questions, `view/` serves them in a browser, `pipeline.py` is the seam
tests/                Mirrors the package layout; fixtures are recorded sessions, and `gallery/` serves them as pages (`docs/ui-development.md`)
docs/
  analysis.md         How an analysis iteration runs: selection, reading protocol, evidence ladder, quoting contract
  schema.md           What each telemetry field means, and the session that proves it
  store.md            The trace store: why it's the archive, and what to check before deleting one
  enrichment.md       Model-written descriptions beside every run, turn, and session — what makes one stale, and what a pass costs
  viewer.md           `aiobserve view`: what the pages show, how a node is titled, the URLs to cite, and reading while an extract runs
  ui-development.md   `mise run gallery` and `aiobserve view --dev`: the edit-save-watch loop for the viewer's own pages
  otlp-export.md      `aiobserve export-otlp`: what leaves the machine, the at-least-once promise, and what re-sends the corpus
  documentation.md    Where each kind of content belongs — read before writing docs
  writing_style_guide.md   House prose style, Zinsser distilled — loaded via the writing skill
  mermaid-guide.md    Read before authoring Mermaid diagrams
  pull-requests.md    The read-before-any-PR guide — loaded via the pr skill
  commits.md          Commit messages: format, emoji, hygiene — loaded via the commit skill
  doc-sync.md         Bringing docs into agreement with a change — loaded via the doc-sync skill
  handoffs.md         Per-run agent scratch: naming, transfer, and lifetime
plans/                Designs and testing plans, one directory per change — committed on the implementing branch, not left untracked on main (`docs/documentation.md`)
reports/              Analysis findings, one per run (see README.md)
handoffs/             Gitignored: scratch one agent run leaves for the next (`docs/handoffs.md`)
data/                 Gitignored: the canonical trace store `traces.duckdb` (`docs/store.md`) and analysis scratch
```

# Instructions

## Load only the context the task needs

Extra context can confuse an AI and weaken its instruction-following. Minimize what you load without omitting what the task needs:

- While working, sample large files instead of reading them whole, pass paths instead of contents, and keep subagent briefs and reports bounded
- While analyzing sessions, treat unnecessary loaded context as a finding: an unneeded doc read, bloated tool output, or a fixture pasted where a path would do

This is also a heuristic for getting a coding agent to work better: context that doesn't contribute to the solution degrades output quality and drives up cost.

## Keep session data private

Transcripts can contain anything an agent read, including source, credentials, and customer data. Treat session data as private and untrusted:

- Raw extracts belong in gitignored `data/`. Never commit one
- The store keeps everything. Every input, output, tool result, and file read stays intact and reachable in the viewer; we are not redacting the store for now (`docs/store.md`)
- Fixtures must be redacted excerpts trimmed to the records a test needs (`.claude/rules/testing.md`). That rule is about the repository, not the store
- Don't paste transcript text into a PR, report, or chat message until you've read it
- Keep ingest keys in gitignored `.env`. Validate them at startup, refuse to run when they're missing or empty, and never print them

## Verify schemas against recordings

Claude Code controls the transcript and span schemas and can change them without notice. Never rely on memory:

- Open a real recorded session before writing a parser, query, or documentation about a field
- Follow `.claude/rules/python.md` when a parser encounters an unexpected shape
- `docs/schema.md` records each confirmed fact with the session and Claude Code version that proved it. Anything absent from that document isn't established

## Write comments for future readers

Give each non-default configuration setting and dependency a short note explaining its purpose in this project. Give primary interfaces docstrings written for their callers.

Keep comments and docstrings brief. Focus on the contract and its traps, and put line-specific rationale beside the line. Most function comments and docstrings need one to three lines; if one runs past eight, look for repeated decisions or narrated implementation to cut. Tests are the exception: use comments freely to tell the test's story.

Write for the code's future, not its past. Don't preserve historical context in comments.

## Match design process to blast radius

Ousterhout's *A Philosophy of Software Design* is the house style. When the docs don't settle a design fork, match your response to its blast radius:

- **Small and clear:** Decide and keep moving
- **Medium:** Build your preferred option, then name the choice and alternatives in your wrap-up. The working version helps Nathaniel assess the decision and makes it easy to reverse
- **Foundation-shaping:** Present the options and your recommendation before building. Do the same for changes to a public interface or stored schema, choices that would waste effort if built first, and explicit design phases

## Define each fact in one document

Read `docs/documentation.md` before editing or creating documentation. It defines where each kind of content belongs. Follow these principles in every session:

- Make facts easy to find through small, focused documents linked by indexes
- Define each fact in one place and link to it everywhere else
- Treat `CLAUDE.md` as an index. Give each entry in its Layout tree a one-line gloss and link to the document that holds the details
- Keep documents short without losing the reasons behind decisions
- Cut ideas instead of compressing sentences
- Prefer forms that resist rot: describe how to discover a fact or point to its source instead of copying a list that will change

## Keep branches and commits focused

- Plain `git` owns branches and commits; `gh` owns PRs. Work on one branch per task, make atomic commits, keep history linear, and land branches on `main` by fast-forward only
- A plan (or any file) the branch will add must not remain untracked on `main` after it is committed on the branch — leftover copies block the fast-forward
- Before committing, invoke the `commit` skill for the message format and hygiene rules in `docs/commits.md`
- Before opening a PR, invoke the `pr` skill. It enforces the flow in `docs/pull-requests.md` and runs `doc-sync` so the docs land in the same PR
