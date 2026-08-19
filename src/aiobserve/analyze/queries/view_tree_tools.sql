-- The tool calls one api call made, in outline: the tree level below a call.
-- Thin like `view_tree_turns` and `view_tree_calls`, and unlimited for the same reason — the
-- cap lives in the composition (`view/tree.py`), where it has to keep the row the open path
-- goes through. A tool call costs nothing of its own: what an api call spent is the api
-- call's, so a tool row carries no cost column and draws no spend bar.
SELECT
    t."index" AS tool_index,
    t.id AS tool_call_id,
    -- What the tool was called and the head of what it was asked, which is what tells two
    -- calls of the same tool apart in the width of a tree.
    substr(t.name, 1, $nav_chars) AS name,
    substr(t.input, 1, $nav_chars) AS input_head,
    t.is_error
FROM live_tool_calls t
WHERE t.session_id = $session_id
  AND t.source = $source
  AND t.api_call_id = $api_call_id
ORDER BY t."index";
