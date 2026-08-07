-- Every session in the store, one row: what the viewer's list ranks and drills from.
-- Reads `session_rollups`, so each row says what that session's own files hold — the numbers
-- a reader opening the session will see, resume copies included.
-- The viewer wraps this SELECT to sort and filter it (`view/app.py`): the file stays the
-- citable core, and no user-supplied value is ever interpolated into it.
SELECT
    r.session_id,
    r.started_at,
    r.title,
    r.project_dir,
    r.turns,
    r.api_calls,
    r.tool_calls,
    r.agent_runs,
    r.compactions,
    round(r.cost_usd, 4) AS cost_usd,
    r.unpriced_api_calls,
    r.input_tokens,
    r.output_tokens,
    r.cache_read_tokens,
    r.cache_creation_tokens,
    r.wall_ms,
    r.active_ms,
    (SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = r.session_id AND t.is_error) AS tool_errors,
    -- Names, never content: which skills ran, which agent types were spawned, which PRs the
    -- session opened. Sorted, so two runs of the same query print the same row.
    (SELECT list_sort(list(DISTINCT c.attribution_skill)) FROM live_api_calls c
        WHERE c.session_id = r.session_id AND c.attribution_skill IS NOT NULL) AS skills,
    (SELECT list_sort(list(DISTINCT a.agent_type)) FROM live_agent_runs a
        WHERE a.session_id = r.session_id) AS agent_types,
    (SELECT list_sort(list(DISTINCT p.pr_url)) FROM pr_links p
        WHERE p.session_id = r.session_id) AS pr_urls
FROM session_rollups r
ORDER BY r.started_at DESC NULLS LAST, r.session_id;
