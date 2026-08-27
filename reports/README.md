# Reports

One analysis pass, written down. A report is the durable output of this project: a finding about how an AI coding agent behaves, with the evidence a reader needs to disagree with it.

Name a report `YYYY_MM_DD_<project>_<topic>.md` — the date it was run, the project analyzed, and what it was about.

[The analysis guide](../docs/analysis.md) is the process that produces one: how sessions are selected, how readers work, the evidence ladder a candidate has to climb, and the citation and redaction rules any transcript quote has to pass.

## What a report has to carry

- **The question** it set out to answer, and why that question was worth the pass
- **The window and the corpus** — which sessions, over which dates, for which project. A finding is about the data you looked at, not about agents in general.
- **The findings**, each with its evidence: the query, the count, the example session. A claim without a query behind it is a hypothesis; label it as one.
- **What you could not tell** from this data, and what would settle it. An absence you cannot bound is a finding about the query, not about the world.
- **Recommendations**, if any — each tied to the finding that motivates it, and each concrete enough to act on

## The trap

The corpus is one person's sessions on one codebase. Say so. A pattern that holds across 40 sessions of one project is evidence about that project's guidance, not about Claude Code — and the recommendation that follows should be scoped the same way.

Never paste a raw transcript excerpt without reading it first. Sessions contain whatever the agent read, including file contents and credentials.
