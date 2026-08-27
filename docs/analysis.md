# Analyze agent behavior

Follow this process to turn the trace store into evidence-backed findings about how an AI coding agent behaved on a project. Read it before each pass; reader subagents should read it again before opening a session. [The analysis design](../plans/mycelia-analysis/design.md) explains why the process works this way and how its reading budget was set.

Commit one report under `reports/` for each pass. Keep every other artifact in gitignored `data/analysis/<YYYY_MM_DD>/`. Session notes may repeat private transcript text and must not enter the repository.

## Make one pass

1. **Refresh and stamp the corpus.** Run `hp extract`. Write the session count, `max(started_at)`, `meta.schema_version`, and distinct `extract_state.extractor_version` values to `data/analysis/<YYYY_MM_DD>/stamp.txt`. This **corpus stamp** defines the data behind the findings, so every artifact must cite it.
2. **Survey the corpus.** Run the count and cluster queries that apply to the pass from `src/hyphae/analyze/queries/`. Save their CSV output under `data/analysis/<YYYY_MM_DD>/counts/`, with each result's citation line. The citation must name the query and every resolved binding.
3. **Select what to read.** Run `hp query select_sessions` and `hp query select_runs`. Both queries make deterministic draws under the rules below.
4. **Read the sample.** Assign each selected session to one reader subagent. The reader writes a session report from `src/hyphae/analyze/templates/session.md` and a run report from `src/hyphae/analyze/templates/run.md` for every run it flags. Use the same run template for runs drawn by `select_runs`. Save the reports under the pass's `sessions/` and `runs/` directories. If synthesis chooses another run to answer the pass's question, read it the same way and tag it `synthesis-draw`.
5. **Synthesize the findings.** In a high-effort pass, read the session and run reports, count tables, and cluster output. Promote candidates under the evidence rules below, then write the committed report according to [the report guide](../reports/README.md).
6. **Review the process.** Answer the fixed checklist below in the report's final section. Land any fixes to the queries, templates, or this guide in the same PR as the report.

## Make the sample reproducible

`select_sessions` draws a stratified sample from the trailing window. Given the same store and bindings, it returns the same sessions. Anyone can therefore rerun and challenge the draw.

The strata claim sessions in this order: cost, tool errors, compactions, one slot for each major skill, then seeded discovery. Each stratum walks down its ranking and skips sessions already claimed. This walk-down keeps the cost, error, and compaction strata from collapsing onto the same few large sessions. Each selected session carries the stratum that claimed it, and its report records that tag.

Every quota is a bound parameter. `src/hyphae/analyze/manifest.py` defines the production defaults that committed reports quote. Change a pass's reading budget with `--param`, not by editing the query.

Interpret the draw by these rules:

- The pool contains in-window sessions that did work of their own: at least one turn or agent run, plus the bound minimum number of API calls. This excludes empty sessions and command-only turns such as `/model`
- A ranked stratum considers only sessions with a nonzero metric. If that population runs out, the stratum stops rather than giving a false tag such as `tool-errors` to an error-free session
- Unused ranked slots remain available to discovery, but discovery applies its own substance floor. The reading budget is a cap, not a promised sample size. Report the realized count and composition
- Discovery uses a seeded draw because it has no metric to justify a choice. Its substance floor avoids spending reading slots on sessions too small to support careful analysis

`select_runs` supplements the session sample. For each `agent_type` that meets its usage floor, it draws the highest-error runs, then the highest-cost runs not already selected for errors. Session strata rank whole sessions and may otherwise miss a commonly used agent definition for several passes.

## Bound each reader's context

Give a reader only the session id, selection stratum, template path, and a link to this guide. Don't paste transcript content into the brief. That would spend the context this protocol is meant to protect.

Readers start with `hp query` timelines and use `records_slice` as their only route to raw transcript text. Its required line range and character cap bound both context and exposure to private data. The citation records the cap, so the report shows how much raw text the reader opened.

Follow these working rules:

- Work in a directory created by `mktemp -d`; concurrent readers have collided in shared `/tmp` paths
- Start with `view_runs` to list and rank the session's runs by cost, tool errors, and compactions
- Run `error_records` before opening raw records; it identifies each error's thread, tool, and line without a transcript scan
- Estimate context use in the report's **Context spent** field so the process review can assess the reading cost

These bounds are conventions, not access controls. A reader still has Bash and can open the store directly. The brief keeps raw text out of the initial context, and the process review checks whether readers stayed within the timelines.

Keep all session and run reports in gitignored `data/`. Commit only the synthesized report, under the quoting rules below.

## Promote claims only as far as the evidence allows

Give every finding one of these evidence levels in the report:

- **Counted** — a corpus query corroborates the finding; cite the query, bindings, and window
- **Recurring** — at least three independent session reports show the finding, and no query can count it
- **Anecdote** — one named session shows the behavior; state it as a hypothesis

Use the saved query that matches the claim:

- **`error_signatures`** groups recurring error text and counts its occurrences, sessions, and threads in both the trailing window and full corpus. Bind a phrase when the first line is generic, such as `Exit code 1`. The query replaces absolute paths in its signature so checkout paths don't split one error into many groups; the report's redaction rules still apply.
- **`command_failures`** handles errors whose text is only a bare exit code. It groups calls by command shape, stripping wrappers, flags, and paths, and places failures beside successful calls of the same shape.
- **`path_failures`** answers which directory a failed file operation targeted. It groups by the path tail so the same directory can count together across a worktree, sandbox copy, and primary checkout. Its output names directories, so redact it before publication.
- **`missing_file_recovery`** counts what a thread did after a missing-file failure: list the same directory, list another directory, or list neither. Every failure belongs to one group, which supplies the denominator.
- **`agent_compactions`** counts context exhaustion by agent definition and reports the main thread as the baseline.
- **`context_reloads`** counts mid-thread API calls that appear to rebuild context they already held. It also marks reloads that an idle gap or compaction may explain. Treat those marks as bounds on interpretation, not filters.
- **`idle_gaps`** lists each silence, its duration, and whether the next call rebuilt context. Use it to size the population behind a recommendation that depends on wait length.
- **`reload_cost_split`** splits those gaps at a required, caller-supplied duration and reports the share of reloads and rebuilt tokens on each side, grouped by thread kind. Use both shares: short and long reloads may rebuild different amounts of context.

### Count absences across the corpus

Only a corpus-wide query can promote an absence to **counted**. The read sample is deliberately biased toward costly, error-prone, compacted, and skill-heavy sessions. At the production defaults, only the eight discovery draws are random, and only within the eligible remainder. Even if treated as a simple random sample, zero sightings there gives a rule-of-three upper bound of roughly three in eight. The pool also excludes sessions that did no work of their own.

A reader who sees no instance of a behavior should file the absence as a hypothesis for synthesis to count or drop. The mirror claim follows the same rule: "Every session I read did Y" describes the sample, not the corpus. Restate it as a corpus count or label it sample-only.

Tie each recommendation to a finding and scope it to the corpus that produced the evidence. One person's sessions on one codebase support a recommendation about that codebase's guidance.

## Cite and redact transcript quotes

Before a transcript quote enters a committed report, it must:

- cite `(session_id, source, first_line-last_line)` so a reviewer can find the source
- pass rule-based redaction that removes paths outside the analyzed repository, personal names, and secret-shaped text such as tokens, environment values, and PEM headers

Synthesis checks every quote for a citation. Nathaniel compares each quoted line with the redaction rule during PR review; the citation makes that a check rather than a judgment call.

## Improve the next pass

Give each pass a new dated report and leave old reports unchanged. Their process-review sections record how the method evolved.

Answer this checklist in the process review:

- Which strata produced findings, and which produced none?
- Which template fields stayed empty or always received `other`?
- Which candidates failed corroboration, and why?
- Roughly how much context did each reader spend?
- Which queries misled?

Land the resulting fixes with the report. Update this guide and the query library as needed. If a template changes, update it and bump `template_version`.
