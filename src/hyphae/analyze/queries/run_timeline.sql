-- One agent run's turns: `session_timeline` at a bound source instead of the main thread.
-- The source is the run's agent id, so a run's timeline holds that run's own rows — the runs
-- it spawned have their own sources, and their own timelines.
WITH turn AS (
    SELECT * FROM live_turns WHERE session_id = $session_id AND source = $source
), call AS (
    SELECT * FROM live_api_calls WHERE session_id = $session_id AND source = $source
), tool AS (
    SELECT c.turn_id, count(*) AS tool_calls, count(*) FILTER (tc.is_error) AS tool_errors
    FROM live_tool_calls tc
    JOIN call c ON c.id = tc.api_call_id
    WHERE tc.session_id = $session_id AND tc.source = $source
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
    -- The three strings a turn is named by, each cut to `$log_chars` and selected one
    -- character past it: they reach a reader through a children log's row, which cuts them
    -- again at that width and marks whichever of them the extra character says went on.
    cut(t.prompt, $log_chars) AS prompt,
    -- A slash turn's heading shows the command it ran and what followed it *instead of*
    -- the prompt, which still holds the `<command-…>` tags Claude Code wrapped it in.
    -- Cut like the prompt: a command is named by whoever wrote the file defining it,
    -- and its arguments are whatever was typed after it.
    cut(t.command_name, $log_chars) AS command_name,
    cut(t.command_args, $log_chars) AS command_args,
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
