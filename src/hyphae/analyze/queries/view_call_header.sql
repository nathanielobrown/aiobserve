-- One api call, whole: what it answered, what it thought, and what it cost.
-- The header of a call's node page. The two fat columns are cut one character past
-- `$detail_chars` — the protocol `view/format.py:cut` reads — with their whole lengths
-- beside them, so the pane shows the head, marks it as cut and says how much more there
-- is; the rest is fetched one value at a time (`view_call_text`, `view_call_thinking`).
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- The turn this call answers, NULL when it answers none of *this thread's* — which is
    -- what puts the call in its thread's unattributed bucket rather than under a turn. Read
    -- through the join rather than off the column: a fork replays its parent's turn, so its
    -- calls carry a turn id recorded on the parent's thread and belong to neither.
    t.id AS turn_id,
    substr(c.model, 1, $head_chars + 1) AS model,
    substr(c.fallback_from, 1, $head_chars + 1) AS fallback_from,
    c.effort,
    c.stop_reason,
    c.attribution_skill,
    c.started_at,
    c.input_tokens,
    c.output_tokens,
    c.cache_read_tokens,
    c.cache_creation_tokens,
    round(c.cost_usd, 4) AS cost_usd,
    -- A NULL cost is a model our price table lacks, not a call that was free.
    (c.cost_usd IS NULL)::INTEGER AS unpriced_api_calls,
    substr(c.text, 1, $detail_chars + 1) AS text_head,
    length(c.text) AS text_chars,
    substr(c.thinking, 1, $detail_chars + 1) AS thinking_head,
    length(c.thinking) AS thinking_chars,
    (
        SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tool_calls,
    -- What the call went on to do, for the title of a call that answered with tool calls and
    -- no words (`view/nodes.py:call_node`): the first call's own title, through the macro
    -- every surface that names a tool call reads, and every call's tool name in the order it
    -- was made. The count that follows the title is composed at the width of the surface
    -- printing it, so what comes back here is the parts rather than the sentence.
    (
        SELECT {
            'head': min_by(tool_title(t.input, s.project_dir, $head_chars), t."index"),
            'names': list(t.name ORDER BY t."index")
        }
        FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tools
FROM live_api_calls c
-- For the project a tool call's path reads against, which is the session's, not the turn's.
LEFT JOIN sessions s ON s.id = c.session_id
LEFT JOIN live_turns t
    ON t.session_id = c.session_id AND t.source = c.source AND t.id = c.turn_id
WHERE c.session_id = $session_id AND c.source = $source AND c.id = $api_call_id;
