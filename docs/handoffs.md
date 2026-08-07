# Handoffs

Handoffs carry per-run scratch between agents without adding it to the repository's durable record.

## Write one dated handoff

**Write a handoff `<description>`** means create `handoffs/handoff_<YYYY_MM_DD>_<description>.md` at the working tree's root. Use the local calendar date and a short kebab-case description.

## Pass the path

Return the handoff's absolute path in the bounded inline report. Give later agents that path, not the file's contents, so one copy remains authoritative during the run.

A handoff reports what its author **verified** and what they **inferred**. A later agent verifies any repository claim it relies on; a handoff is another agent's reading of the repository, not the repository.

## Leave no durable authority

`handoffs/` is gitignored scratch. Never commit a handoff or put its local path in a PR. Copy a design sketch or test checklist into the PR body when [the PR guide](pull-requests.md) requires it.

Put conclusions that must survive the run in their owning doc, code comment, test, or report. No later run treats a handoff as authority.

A handoff may be deleted after every intended consumer has finished with it.
