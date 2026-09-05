-- The same line for one agent run. Keyed by the session rather than by a thread: a run
-- belongs to the session however the page reading it got there.
SELECT e.description AS value
FROM agent_run_enrichments e
WHERE e.session_id = $session_id AND e.agent_run_id = $run_id;
