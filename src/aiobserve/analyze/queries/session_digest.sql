-- One session's main thread, a row per turn: what was asked, and what it cost to answer.
-- Reads the `live_*` family, so a resumed session shows the work it actually ran (the
-- deduplicated view is for counting across sessions, not for reading one).
-- The last row is the api calls that sit under no turn — a resume's calls answer turns that
-- live in the session it resumed. Without that row the digest reports $0 against a front
-- matter quoting the real cost. `run_digest` is this query at an agent source.
WITH turn AS (
    SELECT * FROM live_turns WHERE session_id = $session_id AND source = 'main'
), call AS (
    SELECT * FROM live_api_calls WHERE session_id = $session_id AND source = 'main'
), tool AS (
    SELECT c.turn_id, count(*) AS tool_calls, count(*) FILTER (tc.is_error) AS tool_errors
    FROM live_tool_calls tc
    JOIN call c ON c.id = tc.api_call_id
    WHERE tc.session_id = $session_id AND tc.source = 'main'
    GROUP BY c.turn_id
), spend AS (
    SELECT
        turn_id,
        count(*) AS api_calls,
        coalesce(sum(cost_usd), 0) AS cost_usd,
        count(*) FILTER (cost_usd IS NULL) AS unpriced_api_calls,
        min(started_at) AS first_call
    FROM call GROUP BY turn_id
)
SELECT
    t."index" AS turn_index,
    coalesce(t.id, '(unattributed)') AS turn_id,
    substr(t.prompt, 1, 300) AS prompt,
    t.command_name,
    coalesce(t.started_at, s.first_call) AS started_at,
    coalesce(s.api_calls, 0) AS api_calls,
    coalesce(l.tool_calls, 0) AS tool_calls,
    coalesce(l.tool_errors, 0) AS tool_errors,
    round(coalesce(s.cost_usd, 0), 4) AS cost_usd,
    coalesce(s.unpriced_api_calls, 0) AS unpriced_api_calls
FROM turn t
-- A full join: a turn that spent nothing keeps its row, and the NULL `turn_id` group matches
-- no turn, so it becomes the unattributed row.
FULL JOIN spend s ON s.turn_id = t.id
LEFT JOIN tool l ON l.turn_id IS NOT DISTINCT FROM coalesce(s.turn_id, t.id)
ORDER BY t."index" NULLS LAST;
