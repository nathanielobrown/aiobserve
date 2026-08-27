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
- While iterating, run `mise run check-fast` for formatting, linting, and type checks. It formats prose too, and reports a link or path that doesn't resolve
- Before you finish a task or open a PR, run `mise run check`. It also runs the tests, the hook linter, and the freshness check on every generated block; GitHub runs it on every push and PR (`.github/workflows/check.yml`)
- Run any individual task listed in `mise.toml` with `mise run <task>`. Ruff formats and lints Python and djLint formats the viewer's templates (`docs/ui-development.md`); Pyrefly checks types; aigarden holds the docs to `aigarden.toml` and splices their generated blocks (`docs/documentation.md`)
- Run `mise run diagram-check <file>` to validate Mermaid and `mise run mutate` to score the suite against mutants (`.claude/rules/testing.md`)

Put `mise` flags before the task name. `mise run check --force` passes `--force` to the task, where it does nothing.

Change the tooling or add tools when they make the work easier or enforce project quality.

# Layout

<!-- aigarden:cog sh "uv run python -m tools.gen_layout" -->
```
src/aiobserve/            Analyze AI coding agents from their telemetry
  extract/                Extractors: recorded agent sessions in, `SessionTrace` out
  export/                 Exporters: `SessionTrace` in, rows in a sink out
  enrich/                 The enrichment layer: what a model wrote about each run, turn and session in the store
  analyze/                The analysis layer: a versioned SQL library and the runner that binds and cites it
  view/                   The trace viewer: a local web app serving every node of a session as its own page
  pipeline.py             The seams: what an extractor and an exporter owe each other, and the loop that drives them
tests/                    The suite, mirroring the package layout; fixtures are recorded sessions, and `gallery/` serves them as pages (`docs/ui-development.md`)
tools/                    The repo's own generators: the tables the docs cite, written from the code that owns them
docs/
  analysis.md             Follow this process to turn the trace store into evidence-backed findings about how an AI coding agent behaved on a project
  schema.md               Every Claude Code telemetry field aiobserve reads, what it means, and the recording that proves it
  store.md                The trace store is one DuckDB file, `data/traces.duckdb`: the archive `aiobserve extract` writes to and every query reads
  enrichment.md           Enrichment describes every agent run, main turn, and session in the trace store
  viewer.md               `aiobserve view` opens the trace store in a local browser
  ui-development.md       Edit a viewer template or stylesheet and see it in the browser without touching the browser
  otlp-export.md          `aiobserve export-otlp` sends sessions from the trace store to an OTLP/HTTP backend as spans
  documentation.md        Use this guide to decide where project documentation belongs and how to keep it current
  writing_style_guide.md  How to write effectively; based on William Zinsser's *On Writing Well*
  mermaid-guide.md        Use this guide to write Mermaid diagrams that stay small, render on GitHub, and share one visual language
  pull-requests.md        Use this guide to open a PR that a reviewer can understand before reading the diff
  commits.md              Each commit is a review unit
  doc-sync.md             Use this process after the code is done and before writing the PR description
  handoffs.md             Use a handoff to pass scratch from one agent to another during a run
plans/                    Designs and testing plans, one directory per change — committed on the implementing branch, not left untracked on main (`docs/documentation.md`)
reports/                  One analysis pass, written down
handoffs/                 Gitignored: scratch one agent run leaves for the next (`docs/handoffs.md`)
data/                     Gitignored: the canonical trace store `traces.duckdb` (`docs/store.md`) and analysis scratch
```
<!-- aigarden:end -->

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
- A confirmed fact is declared on its record model in `src/aiobserve/extract/records/`, with the session and Claude Code version that proved it; `docs/schema.md` prints what the models carry. Anything absent from that document isn't established

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
- Treat `CLAUDE.md` as an index. Its Layout tree is generated: an entry's gloss is the package's own docstring or the document's opening sentence, so write the gloss there and run `mise run cogs`
- Keep documents short without losing the reasons behind decisions
- Cut ideas instead of compressing sentences
- Prefer forms that resist rot: describe how to discover a fact or point to its source instead of copying a list that will change

## Keep branches and commits focused

- Plain `git` owns branches and commits; `gh` owns PRs. Work on one branch per task, make atomic commits, keep history linear, and land branches on `main` by fast-forward only
- A plan (or any file) the branch will add must not remain untracked on `main` after it is committed on the branch — leftover copies block the fast-forward
- Before committing, invoke the `commit` skill for the message format and hygiene rules in `docs/commits.md`
- Before opening a PR, invoke the `pr` skill. It enforces the flow in `docs/pull-requests.md` and runs `doc-sync` so the docs land in the same PR
