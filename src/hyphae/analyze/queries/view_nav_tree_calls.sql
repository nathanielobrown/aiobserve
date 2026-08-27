-- The api calls under one turn in outline, a row per call: the NavTree level below a turn.
-- `$turn_id` NULL selects this thread's unattributed calls, which is the bucket's level.
-- Unattributed is decided by the join and not by `c.turn_id IS NULL`: a fork's transcript
-- replays its parent's turn, so its calls name a turn recorded on the parent's thread, and a
-- call whose turn is not this thread's belongs to this thread's bucket rather than to the
-- other thread's turn. `session_timeline` bins them the same way, through the same absence.
-- Thin like `view_nav_tree_turns` and unlimited for the same reason — the cap lives in the
-- composition, because it has to keep the row the open path goes through.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- What the call said, and the model that said it: the title falls back through the tool
    -- calls below to the model when the answer was tool calls and no text. Both are cut
    -- here, and only one of them reaches a row.
    substr(c.text, 1, $nav_chars + 1) AS text_head,
    substr(c.model, 1, $nav_chars + 1) AS model,
    -- What the call went on to do, for the title of a call that answered with tool calls and
    -- no words (`view/nodes.py:call_node`): the first call's own title, through the macro
    -- every surface that names a tool call reads, and every call's tool name in the order it
    -- was made. The count that follows the title is composed at the width of the surface
    -- printing it, so what comes back here is the parts rather than the sentence.
    (
        SELECT {
            'head': min_by(tool_title(t.input, s.project_dir, $nav_chars), t."index"),
            'names': list(t.name ORDER BY t."index")
        }
        FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tools,
    -- Where the call left the model's context window, how much of that it put there itself,
    -- and the window it was answering in (`analyze/macros.py`). A synthetic reply is Claude
    -- Code's own and reports no tokens at all, so it says nothing about the window rather
    -- than saying the window was empty.
    CASE WHEN NOT c.synthetic THEN {
        'fill': context_fill(c),
        'added': context_added(c),
        'window': context_window(c.model)
    } END AS context,
    -- When it was made, which is what the compactions of the same turn interleave against.
    c.started_at,
    round(c.cost_usd, 4) AS cost_usd,
    -- A NULL cost is a model our price table lacks, not a call that was free.
    (c.cost_usd IS NULL)::INTEGER AS unpriced_api_calls
FROM live_api_calls c
-- For the project a tool call's path reads against, which is the session's, not the turn's.
LEFT JOIN sessions s ON s.id = c.session_id
LEFT JOIN live_turns t
    ON t.session_id = c.session_id AND t.source = c.source AND t.id = c.turn_id
WHERE c.session_id = $session_id
  AND c.source = $source
  AND t.id IS NOT DISTINCT FROM $turn_id
ORDER BY c."index";
