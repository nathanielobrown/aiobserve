-- One session's main thread in outline: a row per turn, with what to call it and what it cost.
-- The map beside a session page (`view/threads.py:nav_tree`) covers the whole session rather
-- than the page's window, so a row is deliberately thinner than `session_digest`'s: one label
-- head at `$nav_chars`, one number, and the count of calls under it our price table lacked —
-- a total missing calls is not what the turn cost. Paging composes around this rather than in it.
-- The three label columns are the fallback the map reads in order — the command a turn ran and
-- what followed it, else the prompt, which for a slash turn is the `<command-…>` wrapper
-- Claude Code put around it. Unlike the digest there is no row for the calls that answer no
-- turn: the map maps turns, and those calls answered one this session does not hold.
WITH spend AS (
    SELECT
        turn_id,
        round(sum(cost_usd), 4) AS cost_usd,
        count(*) FILTER (cost_usd IS NULL) AS unpriced_api_calls
    FROM live_api_calls
    WHERE session_id = $session_id AND source = 'main'
    GROUP BY turn_id
)
SELECT
    t."index" AS turn_index,
    t.id AS turn_id,
    substr(t.prompt, 1, $nav_chars) AS prompt,
    substr(t.command_name, 1, $nav_chars) AS command_name,
    substr(t.command_args, 1, $nav_chars) AS command_args,
    coalesce(s.cost_usd, 0) AS cost_usd,
    coalesce(s.unpriced_api_calls, 0) AS unpriced_api_calls
FROM live_turns t
LEFT JOIN spend s ON s.turn_id = t.id
WHERE t.session_id = $session_id AND t.source = 'main'
ORDER BY t."index";
