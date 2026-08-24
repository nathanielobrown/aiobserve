-- What one turn was asked, whole. A per-value query: the unit *is* the value, so the column
-- is selected untruncated and exactly one row comes back. The turn's page shows the head and
-- links here for the rest.
--
-- Null for a slash turn, matching `view_turn_header`: what was typed is the `<command-…>`
-- wrapper, whose two facts the turn's page already shows as their own values, so no page
-- links here for one. The wrapper stays whole in the thread's transcript.
SELECT CASE WHEN t.command_name IS NULL THEN t.prompt END AS value
FROM live_turns t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $turn_id;
