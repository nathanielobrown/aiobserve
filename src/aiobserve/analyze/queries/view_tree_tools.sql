-- The tool calls under one api call, in outline: the tree level below a call.
-- `$api_call_id` NULL asks the other question the tree has: every tool call under one turn,
-- ordered by the call that made it and then by its own index. That is the `noapi` preset's
-- level, where the api calls are hidden and their tool calls hoist to the turn — and, at
-- `$turn_id` NULL, the same for the calls that answer no turn. Unattributed is decided by the
-- join, not by `c.turn_id IS NULL`: a fork replays its parent's turn (`view_tree_calls`).
-- Thin like `view_tree_turns` and `view_tree_calls`, and unlimited for the same reason — the
-- cap lives in the composition (`view/tree.py`), where it has to keep the row the open path
-- goes through. A tool call costs nothing of its own: what an api call spent is the api
-- call's, so a tool row carries no cost column and draws no spend bar.
SELECT
    -- Where the call that made it sits in the thread, which is what orders a hoisted level.
    c."index" AS call_index,
    t."index" AS tool_index,
    t.id AS tool_call_id,
    -- What the tool was called and the head of what it was asked, which is what tells two
    -- calls of the same tool apart in the width of a tree.
    substr(t.name, 1, $nav_chars + 1) AS name,
    substr(t.input, 1, $nav_chars + 1) AS input_head,
    -- When it ran, which is what the compactions of the same turn interleave against where
    -- the api calls are folded away and the tool calls stand under the turn.
    t.started_at,
    t.is_error
FROM live_tool_calls t
JOIN live_api_calls c
    ON c.session_id = t.session_id AND c.source = t.source AND c.id = t.api_call_id
LEFT JOIN live_turns tn
    ON tn.session_id = c.session_id AND tn.source = c.source AND tn.id = c.turn_id
WHERE t.session_id = $session_id
  AND t.source = $source
  AND ($api_call_id IS NULL OR t.api_call_id = $api_call_id)
  AND ($api_call_id IS NOT NULL OR tn.id IS NOT DISTINCT FROM $turn_id)
ORDER BY c."index", t."index";
