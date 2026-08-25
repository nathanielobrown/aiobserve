-- The api calls under one turn in outline, a row per call: the tree level below a turn.
-- `$turn_id` NULL selects this thread's unattributed calls, which is the bucket's level.
-- Unattributed is decided by the join and not by `c.turn_id IS NULL`: a fork's transcript
-- replays its parent's turn, so its calls name a turn recorded on the parent's thread, and a
-- call whose turn is not this thread's belongs to this thread's bucket rather than to the
-- other thread's turn. `session_digest` bins them the same way, through the same absence.
-- Thin like `view_tree_turns` and unlimited for the same reason — the cap lives in the
-- composition, because it has to keep the row the open path goes through.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- What the call said, and the model that said it: the title falls back to the model
    -- when the answer was tool calls and no text. Both are cut here, and only one of them
    -- reaches a row.
    substr(c.text, 1, $nav_chars + 1) AS text_head,
    substr(c.model, 1, $nav_chars + 1) AS model,
    -- When it was made, which is what the compactions of the same turn interleave against.
    c.started_at,
    round(c.cost_usd, 4) AS cost_usd,
    -- A NULL cost is a model our price table lacks, not a call that was free.
    (c.cost_usd IS NULL)::INTEGER AS unpriced_api_calls
FROM live_api_calls c
LEFT JOIN live_turns t
    ON t.session_id = c.session_id AND t.source = c.source AND t.id = c.turn_id
WHERE c.session_id = $session_id
  AND c.source = $source
  AND t.id IS NOT DISTINCT FROM $turn_id
ORDER BY c."index";
