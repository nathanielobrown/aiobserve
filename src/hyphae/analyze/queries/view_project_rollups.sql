-- Every project the store holds sessions for, one row: what the viewer's landing page ranks.
-- Reads `corpus_rollups`, so a resume's copied api calls count once for the project rather
-- than once per session file — a project's spend is what the corpus spent, not the sum of
-- what its session files each claim.
-- Worktrees fold onto the checkout they were cut from: a worktree sits under its repository,
-- so every stored directory counts under the shortest stored directory it sits in. The `/` in
-- the match is what keeps a neighbouring checkout whose path merely begins with this one's
-- out of it (`sessions.project_predicate`, which the CLI's `--project` filters by).
-- Both trailing windows are computed from `$as_of`, which the caller binds and no clock here
-- reads: a page citing SQL's own clock would cite a line that answers something else tomorrow.
-- A path is shown cut to `$head_chars` and offered as a filter whole or not at all — a link
-- carrying a path cut to its head lands on a list holding nothing.
WITH roots AS (
    SELECT DISTINCT project_dir FROM corpus_rollups WHERE project_dir IS NOT NULL
),
folded AS (
    SELECT
        r.started_at,
        r.cost_usd,
        r.unpriced_api_calls,
        (SELECT min_by(a.project_dir, length(a.project_dir)) FROM roots a
         WHERE r.project_dir = a.project_dir
            OR starts_with(r.project_dir, a.project_dir || '/')) AS root,
        -- The window a session falls in, decided once and read by every count below: a
        -- second spelling of the boundary is a second rule, free to drift from the first.
        -- Closed at both ends, like the runner's window, so the cited line re-runs the same.
        coalesce(r.started_at >= $as_of::DATE - to_days($recent_days::INTEGER)
            AND r.started_at < $as_of::DATE + INTERVAL 1 DAY, false) AS in_recent,
        coalesce(r.started_at >= $as_of::DATE - to_days($window_days::INTEGER)
            AND r.started_at < $as_of::DATE + INTERVAL 1 DAY, false) AS in_window
    FROM corpus_rollups r
)
SELECT
    substr(root, 1, $head_chars) AS project_dir,
    CASE WHEN length(root) <= $head_chars THEN root END AS project_filter,
    count(*) FILTER (in_recent) AS recent_sessions,
    round(sum(cost_usd) FILTER (in_recent), 4) AS recent_cost,
    sum(unpriced_api_calls) FILTER (in_recent) AS recent_unpriced,
    count(*) FILTER (in_window) AS window_sessions,
    round(sum(cost_usd) FILTER (in_window), 4) AS window_cost,
    sum(unpriced_api_calls) FILTER (in_window) AS window_unpriced,
    count(*) AS sessions,
    round(sum(cost_usd), 4) AS cost_usd,
    sum(unpriced_api_calls) AS unpriced_api_calls,
    max(started_at) AS last_active,
    -- How many projects the store holds, so a page that cut some can say how many. Counted
    -- over the groups rather than by a second query: window functions run after the grouping.
    count(*) OVER () AS matched_projects
FROM folded
GROUP BY root
-- The sessions naming no directory group into one row, which has no timestamp to rank by:
-- the store holds their spend, and it belongs at the end of a page ordered by recency.
ORDER BY last_active DESC NULLS LAST
LIMIT $projects;
