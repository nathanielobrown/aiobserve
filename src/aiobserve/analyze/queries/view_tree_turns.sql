-- One thread's turns in outline: a row per turn, with what to call it and what it cost.
-- The rows of one level of the tree beside a node page, so a row is deliberately thinner
-- than `session_digest`'s: one title head at `$nav_chars` + 1 — the cut protocol
-- `view/format.py:cut` reads — one cost, and how much of that
-- cost our price table could not price. `$source` is the thread — `main` for a session's
-- own, a run's id for a run's — which is what makes one query serve every level that holds
-- turns. Unlimited on purpose: the tree caps a level in the composition (`view/tree.py`),
-- where the cap has to keep the row the open path goes through whatever else it cuts.
WITH spend AS (
    SELECT
        turn_id,
        round(sum(cost_usd), 4) AS cost_usd,
        count(*) FILTER (cost_usd IS NULL) AS unpriced_api_calls
    FROM live_api_calls
    WHERE session_id = $session_id AND source = $source
    GROUP BY turn_id
)
SELECT
    t."index" AS turn_index,
    t.id AS turn_id,
    -- The three columns a turn's title is read from, in order: the command a turn ran and what
    -- followed it, else the prompt — which for a slash turn is the `<command-…>` wrapper
    -- Claude Code put around it, and says nothing in the width of a tree.
    substr(t.prompt, 1, $nav_chars + 1) AS prompt,
    substr(t.command_name, 1, $nav_chars + 1) AS command_name,
    substr(t.command_args, 1, $nav_chars + 1) AS command_args,
    -- When it started, which is what the compactions of the same thread interleave against.
    t.started_at,
    coalesce(s.cost_usd, 0) AS cost_usd,
    coalesce(s.unpriced_api_calls, 0) AS unpriced_api_calls
FROM live_turns t
LEFT JOIN spend s ON s.turn_id = t.id
WHERE t.session_id = $session_id AND t.source = $source
ORDER BY t."index";
