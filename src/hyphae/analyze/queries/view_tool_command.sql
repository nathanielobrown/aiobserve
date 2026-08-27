-- What one `Bash` call ran, whole. A per-value query: the unit *is* the value, so the column
-- is selected untruncated and exactly one row comes back. The tool's page shows the head of
-- the command and links here for the rest; every other tool answers NULL, and the pane of a
-- call that ran no command never offers the link.
SELECT
    CASE WHEN t.name = 'Bash' AND json_valid(t.input)
         THEN json_extract_string(t.input, '$.command')
         END AS value
FROM live_tool_calls t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
