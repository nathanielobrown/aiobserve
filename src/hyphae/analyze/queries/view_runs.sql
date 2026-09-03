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
    cut(a.agent_type, $chip_chars) AS agent_type,
    cut(a.brief, $chip_chars) AS brief,
    cut(a.model, $chip_chars) AS model,
    a.spawn_depth,
    a.is_fork,
    a.parent_agent_id,
    a.tool_use_id,
    a.started_at,
    a.ended_at,
    -- What a reader ranks runs by, gathered by the three joins below rather than by a
    -- correlated subquery apiece. Each is the run's own thread and not its subtree: a run it
    -- spawned has a source of its own, and its numbers belong to its own row. A run no group
    -- covers takes the zero, so a thread that answered nothing prices at nothing.
    coalesce(calls.cost_usd, 0) AS cost_usd,
    coalesce(calls.unpriced_api_calls, 0) AS unpriced_api_calls,
    coalesce(tools.tool_errors, 0) AS tool_errors,
    coalesce(compacted.compactions, 0) AS compactions,
    -- Where the run left the context window of its own thread, and the window it answered in.
    -- What a run added is the whole of what it holds: a run starts on an empty window and
    -- fills it while it runs, so the fill and the tip are one number said twice. The struct
    -- is built here rather than in the join, so a run with no call still answers a struct of
    -- nulls — a bar the viewer does not draw — instead of no struct at all.
    {'fill': calls.tip, 'added': calls.tip, 'window': calls.window} AS context,
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
-- this thread does not hold; the NavTree would then hang the run off a node no level renders.
LEFT JOIN live_turns st
    ON st.session_id = c.session_id AND st.source = c.source AND st.id = c.turn_id
-- One grouped pass per family the numbers above come from, keyed on the thread they belong
-- to. `synthetic` calls are excluded from the context tip alone: they carry no window, and
-- they are still calls the run made and priced.
LEFT JOIN (
    SELECT n.session_id, n.source,
        round(sum(n.cost_usd), 4) AS cost_usd,
        count(*) FILTER (n.cost_usd IS NULL) AS unpriced_api_calls,
        max_by(context_fill(n), n."index") FILTER (NOT n.synthetic) AS tip,
        max_by(context_window(n.model), n."index") FILTER (NOT n.synthetic) AS window
    FROM live_api_calls n GROUP BY n.session_id, n.source) calls
    ON calls.session_id = a.session_id AND calls.source = a.id
LEFT JOIN (
    SELECT t.session_id, t.source, count(*) FILTER (t.is_error) AS tool_errors
    FROM live_tool_calls t GROUP BY t.session_id, t.source) tools
    ON tools.session_id = a.session_id AND tools.source = a.id
LEFT JOIN (
    SELECT k.session_id, k.source, count(*) AS compactions
    FROM live_compactions k GROUP BY k.session_id, k.source) compacted
    ON compacted.session_id = a.session_id AND compacted.source = a.id
WHERE a.session_id = $session_id
ORDER BY a.started_at NULLS LAST, a.id;
