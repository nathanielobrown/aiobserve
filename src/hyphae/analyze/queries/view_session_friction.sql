-- And the friction the pass saw across the session.
SELECT e.friction AS value
FROM session_enrichments e
WHERE e.session_id = $session_id;
