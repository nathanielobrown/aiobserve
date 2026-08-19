-- What one agent run was asked to do, whole — the `description` the spawning agent typed
-- into the Agent tool. A per-value query, keyed by the run rather than by a thread: a run's
-- id is its source, so the run alone names it.
SELECT a.description AS value
FROM live_agent_runs a
WHERE a.session_id = $session_id AND a.id = $run_id;
