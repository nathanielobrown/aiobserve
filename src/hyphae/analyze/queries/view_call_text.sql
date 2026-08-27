-- What one api call said, whole. A per-value query: the unit *is* the value, so this is one
-- of the few places a fat column is selected untruncated, and it returns exactly one row.
-- The bound is the store's largest single `text`; there is no page size to apply to one value.
SELECT c.text AS value
FROM live_api_calls c
WHERE c.session_id = $session_id AND c.source = $source AND c.id = $api_call_id;
