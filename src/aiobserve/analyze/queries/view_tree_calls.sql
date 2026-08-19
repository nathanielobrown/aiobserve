-- The api calls under one turn in outline, a row per call: the tree level below a turn.
-- `$turn_id` NULL selects the calls that answer no turn, which is the unattributed bucket's
-- level. Thin like `view_tree_turns` and unlimited for the same reason — the cap lives in
-- the composition, because it has to keep the row the open path goes through.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- What the call said, and the model that said it: the label falls back to the model
    -- when the answer was tool calls and no text. Both are cut here, and only one of them
    -- reaches a row.
    substr(c.text, 1, $nav_chars) AS text_head,
    substr(c.model, 1, $nav_chars) AS model,
    round(c.cost_usd, 4) AS cost_usd,
    -- A NULL cost is a model our price table lacks, not a call that was free.
    (c.cost_usd IS NULL)::INTEGER AS unpriced_api_calls
FROM live_api_calls c
WHERE c.session_id = $session_id
  AND c.source = $source
  AND c.turn_id IS NOT DISTINCT FROM $turn_id
ORDER BY c."index";
