-- One tool call, whole: what it was asked and what it answered, cut to a pane's width.
-- The header of a tool call's node page. Both fat columns are cut one character past
-- `$detail_chars` — the protocol `view/format.py:cut` reads — with their whole lengths
-- beside them; the rest is fetched as one value (`view_tool_value`), and
-- a result the transcript offloaded to a file has a page of its own instead.
SELECT
    t."index" AS tool_index,
    t.id AS tool_call_id,
    -- The session, which the pane needs to link an offloaded result to its own page.
    t.session_id,
    -- The call that made it, which is this node's parent — and the turn that call answers,
    -- which is its grandparent. Both here so the path down to a tool needs no second read;
    -- read through the join for the reason `view_call_header` states, and NULL where the call
    -- answers no turn of this thread, which puts it in this thread's unattributed bucket.
    t.api_call_id,
    n.id AS turn_id,
    substr(t.name, 1, $head_chars) AS name,
    t.server_side,
    t.is_error,
    t.incomplete,
    t.offload_file,
    -- The run this call started, where it started one. A `Task` call is where an agent run
    -- begins, so its node view leads with the way there; NULL for every other tool. Joined by
    -- the same rule the rest of the library reads the spawning edge by — `tc.source <> a.id`
    -- keeps a fork's own copy of the call that spawned it from answering here.
    a.id AS run_id,
    t.started_at,
    t.ended_at,
    date_diff('millisecond', t.started_at, t.ended_at) AS wall_ms,
    substr(t.input, 1, $detail_chars + 1) AS input_head,
    length(t.input) AS input_chars,
    -- NULL where the tool returned nothing at all, which is not the same as returning "".
    substr(t.result, 1, $detail_chars + 1) AS result_head,
    length(t.result) AS result_chars
FROM live_tool_calls t
JOIN live_api_calls c
    ON c.session_id = t.session_id AND c.source = t.source AND c.id = t.api_call_id
LEFT JOIN live_turns n
    ON n.session_id = c.session_id AND n.source = c.source AND n.id = c.turn_id
LEFT JOIN live_agent_runs a
    ON a.session_id = t.session_id AND a.tool_use_id = t.id AND t.source <> a.id
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
