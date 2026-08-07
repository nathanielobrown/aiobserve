-- One session's agent runs, each against the turn its spawning call sits under.
-- `spawn_turn_id` NULL means the join failed, which is the whole definition of an unattached
-- run: the page lists it in its own section whatever the cause — no `tool_use_id`, a
-- spawning call naming a tool call the store lacks, or the exclusion below.
-- `tc.source <> a.id` is load-bearing. A fork's own transcript holds an un-replayed copy of
-- the call that spawned it, so without the exclusion the join matches that copy and the fork
-- chips onto a turn of its own timeline — listing itself as its own child.
-- `enrich/store.py:item_parents` applies the same rule for the same reason.
SELECT
    a.id AS run_id,
    a.agent_type,
    a.description,
    a.model,
    a.spawn_depth,
    a.is_fork,
    a.parent_agent_id,
    a.tool_use_id,
    a.started_at,
    a.ended_at,
    -- The thread the spawning call was made from, and the turn inside it. A run spawned from
    -- another run resolves to a turn of that run's timeline, not of `main`.
    c.source AS spawn_source,
    c.turn_id AS spawn_turn_id
FROM live_agent_runs a
LEFT JOIN live_tool_calls tc
    ON tc.session_id = a.session_id AND tc.id = a.tool_use_id AND tc.source <> a.id
LEFT JOIN live_api_calls c
    ON c.session_id = a.session_id AND c.source = tc.source AND c.id = tc.api_call_id
WHERE a.session_id = $session_id
ORDER BY a.started_at NULLS LAST, a.id;
