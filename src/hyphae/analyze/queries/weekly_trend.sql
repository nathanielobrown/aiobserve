-- The corpus bucketed by ISO week — the trend form of `session_counts`, over the same rows.
-- A session with no start time goes to the `undated` bucket rather than a NULL one: the
-- weeks have to sum to the corpus total, and a bucket nobody can see swallows sessions.
SELECT
    coalesce(strftime(r.started_at, '%G-W%V'), 'undated') AS week,
    count(*) AS sessions,
    sum(r.turns) AS turns,
    sum(r.api_calls) AS api_calls,
    sum(r.tool_calls) AS tool_calls,
    sum(r.agent_runs) AS agent_runs,
    sum(r.compactions) AS compactions,
    round(sum(r.cost_usd), 4) AS cost_usd,
    sum(r.unpriced_api_calls) AS unpriced_api_calls
FROM project_sessions p
JOIN corpus_rollups r USING (session_id)
GROUP BY week
ORDER BY week;
