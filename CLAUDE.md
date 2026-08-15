# Overview

aiobserve analyzes AI coding agents to find ways to make them better. It extracts telemetry — mostly traces — from coding sessions, turns them into something queryable, and produces findings with the evidence behind them.

The first target is Claude Code, and the first corpus is the `mycelia` project's sessions. Neither is a constraint: the tool takes a project path, so it must work on any repository, and nothing may assume mycelia's layout, tooling, or conventions.

The project is early. Feature speed and backward compatibility don't matter yet — make clean breaking changes without compatibility shims or deprecation paths.

# The bar for a finding

Everything here produces claims about someone else's behavior from data we didn't design. That makes evidence the whole job:

- **A claim carries its query.** The dataset, the filter, the time window, the count. A number with no query behind it is a hypothesis
- **An absence is bounded or it isn't a finding.** "No session did X" means nothing until you show the data could have contained X
- **Say what the corpus is.** One person's sessions on one codebase is evidence about that codebase's guidance. Scope the recommendation to match
- **Correlation is not the mechanism.** A guidance change and a metric shift in the same week are two facts. Name what else moved

# Tooling

Use `mise` for project tasks; `uv` owns the Python environment.

- Run `mise run sync` after a fresh clone or a dependency change
- Use `mise run check-fast` while iterating — format, lint, type-check
- Run `mise run check` before you finish a task or open a PR. It adds the tests and the hook linter, and GitHub runs it again on every push and PR (`.github/workflows/check.yml`)
- Individual tasks are `format`, `lint`, `typecheck`, `test`. Ruff handles linting and formatting; Pyrefly handles types
- `mise run diagram-check <file>` validates Mermaid. Every task lives in `mise.toml`

mise's own flags go **before** the task name. `mise run check --force` passes `--force` to the task, where it does nothing.

Feel free to change existing tooling or add new tools when they ease the work or enforce project quality.

# Layout

```
src/aiobserve/        The package — `extract/` reads an agent's sessions, `export/` writes a sink, `enrich/` describes what it found, `analyze/` asks the questions, `view/` serves them in a browser, `pipeline.py` is the seam
tests/                Mirrors the package layout; fixtures are recorded sessions
docs/
  analysis.md         How an analysis iteration runs: selection, reading protocol, evidence ladder, quoting contract
  schema.md           What each telemetry field means, and the session that proves it
  store.md            The trace store: why it's the archive, and what to check before deleting one
  enrichment.md       Model-written descriptions beside every run, turn, and session — what makes one stale, and what a pass costs
  viewer.md           `aiobserve view`: what the pages show, the URLs to cite, and reading while an extract runs
  otlp-export.md      `aiobserve export-otlp`: what leaves the machine, the at-least-once promise, and what re-sends the corpus
  documentation.md    Where each kind of content belongs — read before writing docs
  writing_style_guide.md   House prose style, Zinsser distilled — loaded via the writing skill
  mermaid-guide.md    Read before authoring Mermaid diagrams
  pull-requests.md    The read-before-any-PR guide — loaded via the pr skill
  commits.md          Commit messages: format, emoji, hygiene — loaded via the commit skill
  doc-sync.md         Bringing docs into agreement with a change — loaded via the doc-sync skill
  handoffs.md         Per-run agent scratch: naming, transfer, and lifetime
plans/                Designs and testing plans, one directory per change
reports/              Analysis findings, one per run (see README.md)
data/                 Gitignored: the canonical trace store `traces.duckdb` (`docs/store.md`) and analysis scratch
```

# Instructions

## Context is a cost

Minimize context usage while keeping the **necessary** context. All things being equal, an AI with less loaded context gets less confused, adheres better to instructions, and does a better job. Apply the lens "what context got loaded that is unnecessary?" everywhere it fits:

- When working: load only what the task needs — sample large files instead of reading them, pass paths not contents, keep subagent briefs and reports bounded
- When analyzing sessions: unnecessary loaded context is a first-class finding — a doc read that wasn't needed, tool output that bloats the window, a fixture pasted where a path would do

## Session data is untrusted and private

A transcript records everything the analyzed agent read — source, credentials, customer data, whatever was on screen. Treat it accordingly:

- Raw extracts go in `data/`, which is gitignored. Never commit one
- A fixture is a **redacted** excerpt, trimmed to the records the test needs (`.claude/rules/testing.md`)
- Never paste transcript text into a PR, a report, or a chat message without reading it first
- Ingest keys live in `.env`, gitignored. Validate them at startup and refuse to run without them; never print one

## Never trust a remembered schema

Claude Code owns the transcript and span shapes, and changes them without notice. So:

- Open a real recorded session before you write a parser, a query, or a doc about a field
- An unrecognized record shape is a schema change we need to see. Crash on it; don't skip it
- `docs/schema.md` records what we've confirmed, with the session and Claude Code version that confirmed it. If it isn't there, it isn't established

## Comments

Give every non-default configuration setting and dependency a short note explaining its purpose *in this project*. Give primary interfaces docstrings written for their callers.

Keep comments and docstrings brief — readers pay their cost far more often than writers do. Focus on the contract and its traps. Put rationale for a specific line in a nearby comment, not in the docstring. Most functions need one to three lines; past about eight, look for a repeated decision or narrated implementation to cut. Tests are the exception: use comments freely to tell the story of a test case.

Don't preserve historical context in comments. Write for the code's future, not its past.

## Design deliberately

Favor careful design. Ousterhout's *A Philosophy of Software Design* is the house style.

When the docs don't cover a design fork, scale your response to its blast radius:

- **Small, confident:** decide and keep moving
- **Medium:** build your preferred option, then name the choice and alternatives in your wrap-up. The working version helps Nathaniel evaluate the decision, and the note makes it easy to reverse
- **Foundation-shaping:** present the options and your recommendation before building. Do the same when the choice changes a public interface or a stored schema, when building first would waste effort, or when you're explicitly in a design phase

## Documentation

Read `docs/documentation.md` before you edit or create a document. It defines where each kind of content belongs. The principles below are its always-loaded summary.

- Make facts easy to find through small, focused documents connected by indexes and links
- Define each fact in one place and link to it everywhere else
- Treat always-loaded files — `CLAUDE.md` and the Layout tree — as indexes. Give each entry a one-line gloss and a link; keep the detail in the linked document
- Keep documents short but preserve the reasons behind decisions
- Cut ideas instead of compressing sentences
- Prefer rot-proof forms: phrase a list as discovery ("every task in `mise.toml`") rather than enumerating today's members

## Pull requests and commits

- Plain `git` owns branches and commits; `gh` owns PRs. One branch per task, atomic commits, linear history; branches land on `main` by fast-forward only
- Before committing, invoke the `commit` skill for the message format and hygiene rules in `docs/commits.md`
- Before opening a PR, invoke the `pr` skill. It enforces the flow in `docs/pull-requests.md` and runs `doc-sync` so the docs land in the same PR
