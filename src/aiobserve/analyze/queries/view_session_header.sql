-- One session's header row: what it was, when it ran, and what it cost.
-- Keyed, so it reads the `live_*` family through `session_rollups` — the same rule
-- `session_overview` states, and the reason a session's numbers here can exceed its row in a
-- corpus count. `session_overview` answers a report's front matter; this answers the page,
-- which needs the `sessions` columns a reader identifies the session by.
SELECT
    s.id AS session_id,
    s.title,
    s.agent_name,
    s.project_dir,
    s.git_branch,
    s.version,
    s.entrypoint,
    r.started_at,
    r.ended_at,
    -- Wall time counts the gaps the user spent away; `active_ms` is what Claude Code
    -- reported working. The page shows both, because the difference is the reading.
    r.wall_ms,
    r.active_ms,
    r.turns,
    r.api_calls,
    r.tool_calls,
    r.agent_runs,
    r.compactions,
    (SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = s.id AND t.is_error) AS tool_errors,
    round(r.cost_usd, 4) AS cost_usd,
    r.unpriced_api_calls,
    r.input_tokens,
    r.output_tokens,
    r.cache_read_tokens,
    r.cache_creation_tokens,
    (SELECT list_sort(list(DISTINCT c.attribution_skill)) FROM live_api_calls c
        WHERE c.session_id = s.id AND c.attribution_skill IS NOT NULL) AS skills,
    (SELECT list_sort(list(DISTINCT p.pr_url)) FROM pr_links p
        WHERE p.session_id = s.id) AS pr_urls
FROM sessions s
JOIN session_rollups r ON r.session_id = s.id
WHERE s.id = $session_id;
