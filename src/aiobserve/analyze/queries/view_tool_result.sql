-- What one tool call returned, whole — the largest single fetch the viewer makes, against the
-- store's biggest recorded `result`. NULL means the tool returned nothing, which a call that
-- returned "" did not; a result the transcript offloaded to a file is NULL here too, and that
-- file has its own page (`view_offload`) rather than riding in this row.
SELECT t.result AS value
FROM live_tool_calls t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
