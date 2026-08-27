---
template_version: 2
iteration: YYYY_MM_DD
session_id:
# The tag `select_sessions` gave this session — cost, tool-errors, compactions,
# skill:<name>, or discovery. It says why this session was read, which is what keeps a
# finding from being read back as a corpus-wide rate.
stratum:
# From `aiobserve query session_overview --param session_id=<id>`, which is also where every
# number below comes from. A later iteration compares it to skip re-reading an unchanged
# session, so a wrong value costs a read rather than a finding.
extract_fingerprint:
cost_usd:
unpriced_api_calls:
turns:
tool_calls:
tool_errors:
compactions:
skills:
commands:
---

<!-- Categories. The closed vocabulary for every tag below, defined here and nowhere else:

     confusing-tool, failing-tool, false-positive-error, doc-read-unneeded, doc-missed,
     workflow-mismatch, layout-confusion, lintable-mistake, bloated-tool-output,
     unneeded-context, other

     `false-positive-error` is a tool result marked is_error that reports no real failure —
     a no-match grep exit, a shell artifact after correct output, a pending-check nonzero.

     `other` is the escape valve. An `other` that recurs across sessions is a category the
     next iteration should add — say so in the item.

     Body cap: 60 lines, counting what you write, not the guidance comments you delete.
     Synthesis loads every session report at once, so a report that runs long spends the
     budget of the pass that reads it. Cut whole items; never squeeze sentences.

     Delete this block when you fill the template. -->

## Narrative

<!-- What the session set out to do and how it went. At most 5 bullets. -->

-

## Friction

<!-- Where the work went wrong or went slowly. One line each: what happened, a category
     slug, and the records that show it as `(source, first_line–last_line)`. `source` is
     `main` or an agent id; the line numbers come from `aiobserve query records_slice`.
     An item with no evidence ref is a hypothesis — say so in the line. -->

- `category` — what happened (`source`, 120–138)

## Improvement candidates

<!-- What would have prevented the friction above: a repo change, a guidance change, a tool
     fix. Same vocabulary. A candidate with no friction item behind it is a guess. -->

- `category` — the change — the friction it addresses

## Context waste

<!-- What got loaded that the work did not need: a doc read that went unused, tool output
     that swamped the window, a file pasted where a path would have done. -->

-

## Context spent

<!-- Roughly what you loaded to write this report: timeline calls, records_slice ranges and
     caps, anything that dominated. One or two lines; the process review reads it. -->

-

## Not examined

<!-- What you did not read, and why. This is what stops a later reader taking silence for
     absence — an absence is only ever established by a corpus query. -->

-
