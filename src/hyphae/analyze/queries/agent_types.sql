-- What each agent definition is actually used for and what it costs. `agent_type` is the
-- name the caller asked for, so this is the usage side of the agent library: which
-- definitions carry the work, which get spawned once and never again, which come back with
-- errors. Per-run averages sit beside the totals because a type spawned four hundred times
-- and one spawned twice are not comparable on totals alone.
WITH run AS (
    SELECT
        p.period,
        a.agent_type,
        a.session_id,
        a.is_fork,
        -- A run's own rows sit at `source = agent_id`; a run it spawned has a source of its
        -- own, so nothing here fans out over a subtree.
        (SELECT count(*) FROM corpus_tool_calls t
            WHERE t.session_id = a.session_id AND t.source = a.id) AS tool_calls,
        (SELECT count(*) FILTER (t.is_error) FROM corpus_tool_calls t
            WHERE t.session_id = a.session_id AND t.source = a.id) AS tool_errors,
        (SELECT count(*) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS api_calls,
        (SELECT coalesce(sum(c.cost_usd), 0) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS cost_usd,
        (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS unpriced_api_calls
    FROM session_period p
    JOIN corpus_agent_runs a USING (session_id)
)
SELECT
    period,
    agent_type,
    count(*) AS runs,
    count(DISTINCT session_id) AS sessions,
    count(*) FILTER (is_fork) AS forks,
    sum(api_calls) AS api_calls,
    sum(tool_calls) AS tool_calls,
    sum(tool_errors) AS tool_errors,
    round(avg(api_calls), 2) AS api_calls_per_run,
    round(sum(cost_usd), 4) AS cost_usd,
    round(avg(cost_usd), 4) AS cost_usd_per_run,
    sum(unpriced_api_calls) AS unpriced_api_calls
FROM run
GROUP BY period, agent_type
ORDER BY period, runs DESC, agent_type;
