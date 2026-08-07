-- One tool call, whole: the arguments it was given and what it returned. A per-value query,
-- and the largest single fetch the viewer makes — the store's biggest recorded `result`.
-- `result` NULL means the tool returned nothing, which a call that returned "" did not.
-- `offload_file` names the file the result was written to when it was too large to inline;
-- that file has its own page rather than riding in this row.
SELECT
    t.id AS tool_call_id,
    t.name,
    t.server_side,
    t.is_error,
    t.incomplete,
    t.offload_file,
    t.started_at,
    t.ended_at,
    t.input,
    t.result
FROM live_tool_calls t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
