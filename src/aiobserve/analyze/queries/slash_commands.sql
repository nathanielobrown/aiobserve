-- Which slash commands got run, and what each one costs to run. A turn carries the command
-- that started it, so the api calls under that turn are the command's own bill — the work it
-- delegates to an agent run is not, and shows up in `agent_types` instead.
WITH command_turn AS (
    SELECT
        p.period,
        t.command_name AS command,
        t.session_id,
        (SELECT count(*) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id)
            AS api_calls,
        (SELECT coalesce(sum(c.cost_usd), 0) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id)
            AS cost_usd,
        (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id)
            AS unpriced_api_calls,
        -- A tool call hangs off the api call that asked for it, not off the turn, so the
        -- turn's tools are reached one join further out.
        (SELECT count(*) FROM corpus_tool_calls k
            JOIN corpus_api_calls c ON c.session_id = k.session_id AND c.id = k.api_call_id
            WHERE c.session_id = t.session_id AND c.source = t.source AND c.turn_id = t.id)
            AS tool_calls
    FROM session_period p
    JOIN corpus_turns t USING (session_id)
    WHERE t.command_name IS NOT NULL
)
SELECT
    period,
    command,
    count(*) AS turns,
    count(DISTINCT session_id) AS sessions,
    sum(api_calls) AS api_calls,
    sum(tool_calls) AS tool_calls,
    round(sum(cost_usd), 4) AS cost_usd,
    sum(unpriced_api_calls) AS unpriced_api_calls
FROM command_turn
GROUP BY period, command
ORDER BY period, turns DESC, command;
