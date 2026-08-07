-- The corpus in both windows: one row for the whole corpus, one for the trailing window the
-- runner measured back from the bound as-of date. Reads `corpus_rollups`, so a resumed
-- session is valued at the work no earlier session already holds.
-- One pass over one table produces both rows, so the window can never be an independently
-- written count that drifted from the total it is supposed to restrict.
SELECT
    w.period,
    count(*) AS sessions,
    sum(r.turns) AS turns,
    sum(r.api_calls) AS api_calls,
    sum(r.tool_calls) AS tool_calls,
    sum(r.agent_runs) AS agent_runs,
    sum(r.compactions) AS compactions,
    round(sum(r.cost_usd), 4) AS cost_usd,
    sum(r.unpriced_api_calls) AS unpriced_api_calls
FROM (
    SELECT session_id, 'corpus' AS period FROM project_sessions
    UNION ALL
    SELECT session_id, 'trailing_window' AS period FROM project_sessions WHERE in_window
) w
JOIN corpus_rollups r USING (session_id)
GROUP BY w.period
ORDER BY w.period;
