---
template_version: 3
iteration: YYYY_MM_DD
# A run is keyed by both: an agent id is unique inside its session, not across the corpus.
session_id:
agent_id:
agent_type:
# Where this run came from: `run-errors` or `run-cost` from `select_runs`, the session
# stratum when the session's reader flagged it, or `synthesis-draw` when neither drew it and
# a person or a synthesis pass named it — say in the Narrative what it was chosen for.
stratum:
extract_fingerprint:
cost_usd:
unpriced_api_calls:
turns:
tool_calls:
tool_errors:
---

<!-- Same shape as the session report. Category tags come from the closed vocabulary in
     `src/aiobserve/analyze/templates/session.md` — open it and use those slugs exactly.
     Do not invent tags; `other` is the escape valve, with a note in the item.

     Body cap: 30 lines, counting what you write, not the guidance comments you delete. A run
     report exists to say what this agent definition did well or badly; the session around it
     is the session report's job.

     Numbers come from `aiobserve query run_timeline --param session_id=<id> --param
     source=<agent_id>`. Delete this block when you fill the template. -->

## Narrative

<!-- What the run was dispatched to do and what it came back with. At most 3 bullets. -->

-

## Friction

<!-- One line each: what happened, a category slug, and `(source, first_line–last_line)`,
     where `source` is this run's agent id. -->

- `category` — what happened (`source`, 40–52)

## Improvement candidates

<!-- What would have prevented it — often a change to the agent definition or its brief. -->

- `category` — the change — the friction it addresses

## Context spent

<!-- Roughly what you loaded to write this report. One line. -->

-

## Not examined

<!-- What you did not read, and why. -->

-
