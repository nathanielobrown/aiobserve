-- Every session in the corpus, with the counts a reader ranks and selects on.
-- Reads `corpus_rollups`, so a resumed session is valued at the work no earlier session
-- already holds. `in_window` is the trailing window the runner bound `$as_of` for.
SELECT
    p.session_id,
    r.started_at,
    p.in_window,
    r.turns,
    r.api_calls,
    r.tool_calls,
    r.agent_runs,
    r.compactions,
    round(r.cost_usd, 4) AS cost_usd,
    r.unpriced_api_calls
FROM project_sessions p
JOIN corpus_rollups r USING (session_id)
ORDER BY r.started_at, p.session_id;
