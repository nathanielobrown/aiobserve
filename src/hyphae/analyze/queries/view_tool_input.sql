-- What one tool call was asked, whole. A per-value query: the unit *is* the value, so the
-- column is selected untruncated and exactly one row comes back. The tool's page shows the
-- head and links here for the rest.
SELECT t.input AS value
FROM live_tool_calls t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
