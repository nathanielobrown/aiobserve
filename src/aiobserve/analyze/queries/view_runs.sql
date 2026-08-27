-- One session's agent runs, each against the turn its spawning call sits under.
-- `spawn_turn_id` NULL means the join failed, which is the whole definition of an unattached
-- run: the page lists it in its own section whatever the cause — no `tool_use_id`, a
-- spawning call naming a tool call the store lacks, or the exclusion below.
-- `tc.source <> a.id` is load-bearing. A fork's own transcript holds an un-replayed copy of
-- the call that spawned it, so without the exclusion the join matches that copy and the fork
-- chips onto a turn of its own timeline — listing itself as its own child.
-- `enrich/store.py:item_parents` applies the same rule for the same reason.
-- The three display columns are cut to `$chip_chars`, one character past it: a run is a chip
-- on someone else's page, and a page's size is arithmetic over its rows rather than an
-- observation about the corpus. The extra character is what tells a value that ended from one
-- that was stopped — whoever binds the width cuts again at it and marks what it cut.
SELECT
    a.id AS run_id,
    substr(a.agent_type, 1, $chip_chars + 1) AS agent_type,
    substr(a.brief, 1, $chip_chars + 1) AS brief,
    substr(a.model, 1, $chip_chars + 1) AS model,
    a.spawn_depth,
    a.is_fork,
    a.parent_agent_id,
    a.tool_use_id,
    a.started_at,
    a.ended_at,
    -- What a reader ranks runs by. Each is the run's own thread and not its subtree: a run
    -- it spawned has a source of its own, and its numbers belong to its own row.
    (SELECT coalesce(round(sum(c.cost_usd), 4), 0) FROM live_api_calls c
        WHERE c.session_id = a.session_id AND c.source = a.id) AS cost_usd,
    (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM live_api_calls c
        WHERE c.session_id = a.session_id AND c.source = a.id) AS unpriced_api_calls,
    (SELECT count(*) FILTER (t.is_error) FROM live_tool_calls t
        WHERE t.session_id = a.session_id AND t.source = a.id) AS tool_errors,
    (SELECT count(*) FROM live_compactions k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS compactions,
    -- Where the run left the context window of its own thread, and the window it answered in.
    -- What a run added is the whole of what it holds: a run starts on an empty window and
    -- fills it while it runs, so the fill and the tip are one number said twice.
    (SELECT {
        'fill': max_by(context_fill(c), c."index"),
        'added': max_by(context_fill(c), c."index"),
        'window': max_by(context_window(c.model), c."index")
     } FROM live_api_calls c
        WHERE c.session_id = a.session_id AND c.source = a.id AND NOT c.synthetic) AS context,
    -- The thread the spawning call was made from, and the turn inside it. A run spawned from
    -- another run resolves to a turn of that run's timeline, not of `main`.
    c.source AS spawn_source,
    st.id AS spawn_turn_id,
    -- The call itself, which is where the run hoists: a run renders after the api call that
    -- spawned it, under whichever node that call sits in.
    c.id AS spawn_call_id
FROM live_agent_runs a
LEFT JOIN live_tool_calls tc
    ON tc.session_id = a.session_id AND tc.id = a.tool_use_id AND tc.source <> a.id
LEFT JOIN live_api_calls c
    ON c.session_id = a.session_id AND c.source = tc.source AND c.id = tc.api_call_id
-- The turn the spawning call answers, resolved on the call's own thread. A fork's transcript
-- replays calls whose `turn_id` names a turn of its parent, so the raw column can name a turn
-- this thread does not hold; the tree would then hang the run off a node no level renders.
LEFT JOIN live_turns st
    ON st.session_id = c.session_id AND st.source = c.source AND st.id = c.turn_id
WHERE a.session_id = $session_id
ORDER BY a.started_at NULLS LAST, a.id;
