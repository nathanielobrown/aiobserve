# Handoffs

Use a handoff to pass scratch from one agent to another during a run. Handoffs are temporary notes, not part of the repository's record.

## Name each handoff with its date

When an instruction says **write a handoff `<description>`**, create `handoffs/handoff_<YYYY_MM_DD>_<description>.md` at the working tree's root. Use the local date and a short kebab-case description.

## Pass one authoritative copy

Return the handoff's absolute path in the bounded inline report. Give later agents the path instead of copying the contents. This keeps one copy authoritative during the run.

Mark what you **verified** and what you **inferred**. Later agents must verify any repository claim they rely on: a handoff records another agent's reading of the repository, not the repository itself.

## Keep handoffs out of the durable record

The gitignored `handoffs/` directory holds per-run scratch. Never commit a handoff or put its local path in a PR. When [the PR guide](pull-requests.md) calls for a design sketch or test checklist, copy that material into the PR body.

Put conclusions that must survive the run in the doc, code comment, test, or report that owns them. Later runs must not treat a handoff as authority.

Delete a handoff only after every intended consumer has finished with it.
