-- And for the session itself, whose row the session id alone names.
SELECT e.description AS value
FROM session_enrichments e
WHERE e.session_id = $session_id;
