---
template_version: 1
iteration: YYYY_MM_DD
# A run is keyed by both: an agent id is unique inside its session, not across the corpus.
session_id:
agent_id:
agent_type:
# `run-errors` or `run-cost` from `select_runs`, or the session stratum when the session's
# reader flagged this run rather than the run draw picking it.
stratum:
extract_fingerprint:
cost_usd:
unpriced_api_calls:
turns:
tool_calls:
tool_errors:
---

<!-- Same shape as the session report and the same category vocabulary, which
     `src/aiobserve/analyze/templates/session.md` defines — do not restate it here.

     Body cap: 30 lines, counting what you write, not the guidance comments you delete. A run
     report exists to say what this agent definition did well or badly; the session around it
     is the session report's job.

     Numbers come from `aiobserve query run_digest --param session_id=<id> --param
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

## Not examined

<!-- What you did not read, and why. -->

-
