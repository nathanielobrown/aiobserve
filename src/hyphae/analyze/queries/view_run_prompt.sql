-- What one agent run was asked to do, whole — the prompt of the call that spawned it,
-- read by the rule `view_run_header` previews it by. A per-value query keyed by the run:
-- a run's id is its source, so the run alone names it. NULL where the store holds no
-- spawning call, or where the tool that spawned the run was asked in other words.
SELECT json_extract_string(tc.input, '$.prompt') AS value
FROM live_agent_runs a
JOIN live_tool_calls tc
    ON tc.session_id = a.session_id AND tc.id = a.tool_use_id AND tc.source <> a.id
WHERE a.session_id = $session_id AND a.id = $run_id;
