-- And the friction the pass saw in that run.
SELECT e.friction AS value
FROM agent_run_enrichments e
WHERE e.session_id = $session_id AND e.agent_run_id = $run_id;
