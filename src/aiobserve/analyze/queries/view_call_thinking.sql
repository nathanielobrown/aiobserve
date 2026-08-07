-- What one api call thought, whole. `view_call_text`'s twin: a separate file rather than one
-- query returning both, because a per-value fetch that ships two values has twice the bound.
SELECT c.thinking AS value
FROM live_api_calls c
WHERE c.session_id = $session_id AND c.source = $source AND c.id = $api_call_id;
