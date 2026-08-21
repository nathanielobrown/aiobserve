-- What followed one turn's slash command, whole. A per-value query: the unit *is* the value,
-- so the column is selected untruncated and exactly one row comes back. The turn's page shows
-- the head beside the command name and links here for the rest.
SELECT t.command_args AS value
FROM live_turns t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $turn_id;
