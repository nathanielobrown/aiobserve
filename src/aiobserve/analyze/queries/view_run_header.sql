-- One agent run's header: what it was asked to do, where it sits, and what it spent.
-- A run's id is also the `source` its rows carry, so its counts are the session's rows at
-- that source — the same rule `run_digest` reads by.
-- Both parent rules are selected rather than resolved here, because a page needs to know
-- which one it used: `parent_agent_id` is what the transcript names, and `spawn_source` is
-- the thread the spawning call was made from. `tc.source <> a.id` keeps a fork's own
-- un-replayed copy of that call from resolving the fork to itself, the exclusion `view_runs`
-- and `enrich/store.py:item_parents` both state.
SELECT
    a.id AS run_id,
    a.session_id,
    -- The three strings a header carries from the transcript, each cut to the same head:
    -- whatever the spawning agent typed in the Agent tool's `description`, the definition it
    -- named, and the model the run answered on. Nothing on the far side of any of them bounds
    -- what it holds — an agent definition is named by whoever writes it.
    substr(a.agent_type, 1, $head_chars) AS agent_type,
    substr(a.description, 1, $head_chars) AS description,
    substr(a.model, 1, $head_chars) AS model,
    a.spawn_depth,
    a.is_fork,
    a.parent_agent_id,
    a.tool_use_id,
    a.started_at,
    a.ended_at,
    date_diff('millisecond', a.started_at, a.ended_at) AS wall_ms,
    c.source AS spawn_source,
    c.turn_id AS spawn_turn_id,
    (SELECT count(*) FROM live_turns t
        WHERE t.session_id = a.session_id AND t.source = a.id) AS turns,
    (SELECT count(*) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS api_calls,
    (SELECT count(*) FROM live_tool_calls l
        WHERE l.session_id = a.session_id AND l.source = a.id) AS tool_calls,
    (SELECT count(*) FROM live_tool_calls l
        WHERE l.session_id = a.session_id AND l.source = a.id AND l.is_error) AS tool_errors,
    (SELECT count(*) FROM live_compactions k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS compactions,
    (SELECT coalesce(sum(k.output_tokens), 0) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS output_tokens,
    -- Sums only the calls our price table prices; the count beside it says how many it left
    -- out, so a total is never read as complete without checking.
    (SELECT round(coalesce(sum(k.cost_usd), 0), 4) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS cost_usd,
    (SELECT count(*) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id
          AND k.cost_usd IS NULL) AS unpriced_api_calls
FROM live_agent_runs a
LEFT JOIN live_tool_calls tc
    ON tc.session_id = a.session_id AND tc.id = a.tool_use_id AND tc.source <> a.id
LEFT JOIN live_api_calls c
    ON c.session_id = a.session_id AND c.source = tc.source AND c.id = tc.api_call_id
WHERE a.session_id = $session_id AND a.id = $run_id;
