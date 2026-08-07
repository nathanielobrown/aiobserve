# Analysis

How to turn the trace store into findings about how an AI coding agent behaved on a project. Read this before running an iteration, and read it again as a reader subagent before opening a session. The design behind it, and the numbers that sized it, are in `plans/mycelia-analysis/design.md`.

An iteration ends in one committed report under `reports/`. Everything else it produces is working paper in gitignored `data/analysis/<YYYY_MM_DD>/`, because a per-session note written from a private transcript sometimes carries a piece of one.

## One iteration

1. **Refresh and stamp.** `aiobserve extract` brings the store current. Record the **corpus stamp** — session count, `max(started_at)`, `meta.schema_version`, and the distinct `extract_state.extractor_version`s — in `data/analysis/<YYYY_MM_DD>/stamp.txt`. Every artifact of the iteration cites it. The store grows daily, so "the data you looked at" is a claim with a date on it.
2. **Broad counts and clusters.** Run the query library — every `.sql` in `src/aiobserve/analyze/queries/` — and write the CSVs to `counts/`. Each result's citation line names the query and every resolved binding; keep it with the CSV.
3. **Select.** `aiobserve query select_sessions` and `select_runs` choose what gets read. Both are deterministic; see below.
4. **Careful reading.** One reader subagent per selected session, writing a report from `src/aiobserve/analyze/templates/session.md` into `sessions/`, plus a report from `src/aiobserve/analyze/templates/run.md` into `runs/` for each run it flags and each run `select_runs` drew.
5. **Synthesis.** A high-effort pass loads the session reports, the count tables, and the cluster output; promotes candidates to findings under the evidence ladder; writes the committed report per [the report guide](../reports/README.md).
6. **Process review.** The report's last section reviews the iteration itself, against the checklist below. Fixes to the queries, the templates, and this document land in the same PR as the report.

## Selection

`select_sessions` draws a stratified sample over the trailing window, and it draws the same one every time it runs against the same store with the same bindings. That is the point: a selection anyone can re-run is a selection anyone can criticize.

Strata fill in order — cost, tool errors, compactions, one slot per major skill, then a seeded remainder for discovery — and each walks down its own ranking taking only sessions no earlier stratum took. The walk-down is load-bearing: the top sessions by cost, by errors, and by compactions are largely the same sessions, so without it the read set collapses onto a few monsters. Each selected session carries the tag of the stratum that took it, and its report records the tag.

Every quota is a bound parameter, and `src/aiobserve/analyze/queries.py` holds the production defaults a committed report quotes. Resetting an iteration's reading budget is a `--param`, not a query edit.

Three properties decide how a selection may be read:

- The pool is in-window sessions that did work of their own. Sessions with no turns and no agent runs are excluded, and so are sessions whose turns made no api call — a `/model` turn reads as work and is not. Together they are a large minority of any window
- A ranked stratum takes only sessions whose metric is nonzero, and stops short when the metric runs out. A `tool-errors` tag on an error-free session would be a lie, so the tags stay honest and the stratum's realized size does not
- Unused ranked slots pass to discovery, so the realized set is the smaller of the quota sum and the pool. What varies between iterations is the composition, not the count — report the realized composition, never the target

`select_runs` adds agent runs on top: the highest error count and the highest cost per `agent_type`. Session strata rank whole sessions, so a rarely used agent definition can go unread for iterations; this draw is what gets every commonly used definition looked at.

## Reader protocol

A reader's brief is bounded: the session id, its stratum, the template path, and a pointer to this document. No transcript content goes in a brief. A reader that is handed content has already spent the context the process exists to protect.

Readers work through `aiobserve query` digests. `records_slice` is the only route to raw transcript text, and its line range and character cap are the context and privacy control — the cap is in the citation, so a report says how much raw text it opened.

Three working rules, each bought by an iteration that lacked it:

- Work in a `mktemp -d` scratch directory — concurrent readers sharing `/tmp` paths have collided
- Enumerate the session's runs with `view_runs` before digging; a session's cost usually lives in its runs, not its main thread
- Record roughly what context you spent in the report's "Context spent" line — the process review depends on it

Both are convention rather than mechanism: a reader has Bash and could open the store directly. The mitigations are the bounded brief and the process-review checklist, which asks of each reader whether it stayed inside the digests and roughly what context it spent.

Session and run reports stay in gitignored `data/`. Only the synthesized report is committed, under the quoting contract below.

## The evidence ladder

Synthesis promotes a candidate to a finding at one of three stated levels, and the report says which:

- **Counted** — a corpus query corroborates it. The query and the window go in the report
- **Recurring** — three or more independent session reports show it, and no query can count it
- **Anecdote** — reported as a hypothesis, with its one session named

**An absence is only ever counted.** "No session did X" has to come from a corpus-wide query whose filter demonstrably could have matched X. It never comes from the read sample: zero sightings across roughly thirty read sessions bounds prevalence only at about one in three, over a pool that already excluded the sessions that did no work. A reader who notices an absence files it as a hypothesis for synthesis to count or to drop.

The mirror claims ride the same rule. "Every session I read did Y" is a statement about a deliberately biased sample; it reaches a finding only restated as a corpus-wide count, or labeled sample-only in the report.

Every recommendation ties to a finding and is scoped to the corpus that produced it — one person's sessions on one codebase is evidence about that codebase's guidance.

## Quoting a transcript in a committed report

A transcript records everything the analyzed agent read. Any quote in a committed report:

- carries its citation `(session_id, source, first_line-last_line)`, so the quoted line can be re-read at its source
- passes a rule-based redaction: strip file paths outside the analyzed repository, personal names, and anything secret-shaped — tokens, env values, PEM headers

Synthesis checks that every quote has its citation. Nathaniel's PR review checks each quoted line against the redaction rule; the citation is what makes that check mechanical rather than a matter of taste.

## Iteration and process review

A new iteration is a new dated report. Old reports stay put as the version history, and their shortcoming sections read as a record of how the process improved.

The process-review section answers a fixed checklist:

- which strata produced findings, and which produced none
- which template fields went unfilled, or were always tagged `other`
- which candidates failed corroboration, and why
- roughly what context each reader spent
- which queries misled

Fixes edit this document, the templates — bumping `template_version` — and the query library, in the same PR as the report.
