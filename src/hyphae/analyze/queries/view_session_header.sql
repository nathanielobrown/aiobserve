-- One session's header row: what it was, when it ran, and what it cost.
-- Keyed, so it reads the `live_*` family through `session_rollups` — the same rule
-- `session_overview` states, and the reason a session's numbers here can exceed its row in a
-- corpus count. `session_overview` answers a report's front matter; this answers the page,
-- which needs the `sessions` columns a reader identifies the session by.
-- Everything a transcript wrote is cut here: each string one character past `$head_chars` so
-- the page can mark it (`view/text/format.py:cut`), and each list to `$head_items` members of
-- `$item_chars + 1` with a count of what was left. A header is the part of the page no size a
-- reader types bounds, and `pr_links` grows with every PR the session opened — 32 on the
-- busiest session recorded — so a page's ceiling needs the number bound.
WITH skill AS (
    SELECT coalesce(list_sort(list(DISTINCT c.attribution_skill)), []) AS names
    FROM live_api_calls c
    WHERE c.session_id = $session_id AND c.attribution_skill IS NOT NULL
), held AS (
    -- Where the session's main thread left the context window: the fill of its last call that
    -- went to a model, in the window that model answers in. The main thread alone, because
    -- that is the window a reader of the session is in — a run holds its own, and says so on
    -- its own row. Synthetic replies are out for the reason `view_nav_tree_turns` states.
    SELECT
        max_by(context_fill(c), c."index") AS fill,
        max_by(context_window(c.model), c."index") AS window_tokens
    FROM live_api_calls c
    WHERE c.session_id = $session_id AND c.source = 'main' AND NOT c.synthetic
), pr AS (
    SELECT coalesce(list_sort(list(DISTINCT p.pr_url)), []) AS urls
    FROM pr_links p
    WHERE p.session_id = $session_id
)
SELECT
    s.id AS session_id,
    cut(s.title, $head_chars) AS title,
    cut(s.project_dir, $head_chars) AS project_dir,
    -- The same path, whole, for the one surface that filters by it rather than printing it:
    -- the crumb above the session opens the list narrowed to this project, and the filter
    -- matches a path prefix, so a cut path would open a list of nothing. NULL where the whole
    -- one is longer than the head — a link that cannot be minted is a crumb printed as text.
    CASE WHEN length(s.project_dir) <= $head_chars THEN s.project_dir END AS project_filter,
    cut(s.git_branch, $head_chars) AS git_branch,
    cut(s.version, $head_chars) AS version,
    cut(s.entrypoint, $head_chars) AS entrypoint,
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
    list_transform(list_slice(skill.names, 1, $head_items),
        name -> cut(name, $item_chars)) AS skills,
    greatest(len(skill.names) - $head_items, 0) AS skills_cut,
    list_transform(list_slice(pr.urls, 1, $head_items),
        url -> cut(url, $item_chars)) AS pr_urls,
    greatest(len(pr.urls) - $head_items, 0) AS pr_urls_cut,
    -- The session's bar reads fullness alone: there is no turn before the session for it to
    -- have added anything to, so the tip is left unsaid.
    {'fill': held.fill, 'added': NULL::BIGINT, 'window': held.window_tokens} AS context
FROM sessions s
JOIN session_rollups r ON r.session_id = s.id
CROSS JOIN skill
CROSS JOIN held
CROSS JOIN pr
WHERE s.id = $session_id;
