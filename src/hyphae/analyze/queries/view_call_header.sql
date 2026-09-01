-- One api call, whole: what it answered, what it thought, and what it cost.
-- The header of a call's node page. The two fat columns are cut one character past
-- `$detail_chars` — the protocol `view/text/format.py:cut` reads — with their whole lengths
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
    cut(c.model, $head_chars) AS model,
    cut(c.fallback_from, $head_chars) AS fallback_from,
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
    cut(c.text, $detail_chars) AS text_head,
    length(c.text) AS text_chars,
    cut(c.thinking, $detail_chars) AS thinking_head,
    length(c.thinking) AS thinking_chars,
    (
        SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tool_calls,
    -- What the call went on to do, for the title of a call that answered with tool calls and
    -- no words (`view/builders.py:call_node`): the first tool call's name and the fields the
    -- rules that name one read, and every call's tool name in the order it was made. The name
    -- itself is composed in Python out of those fields, the same way the tool's own row is
    -- named, so the two rows agree — and the count that follows it is composed at the width of
    -- the surface printing it. What comes back here is the parts rather than the sentence.
    (
        SELECT {
            'first': min_by({
                'name': t.name,
                'fields': tool_fields(t.input, s.project_dir, ad.agent_type, $head_chars)
            }, t."index"),
            'names': list(t.name ORDER BY t."index")
        }
        FROM live_tool_calls t
        -- What a path in the fields reads against. Joined here rather than taken from the
        -- outer row: DuckDB 1.5.5 cannot bind a struct-returning macro over a correlated
        -- column, and answers `Need named argument for struct pack`.
        LEFT JOIN sessions s ON s.id = t.session_id
        -- Who a `SendMessage` addressed, resolved the way `view_tool_header` resolves it: a
        -- title reading the run id here would deny the page one level down.
        LEFT JOIN live_agent_runs ad
            ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $head_chars)
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tools
FROM live_api_calls c
LEFT JOIN live_turns t
    ON t.session_id = c.session_id AND t.source = c.source AND t.id = c.turn_id
WHERE c.session_id = $session_id AND c.source = $source AND c.id = $api_call_id;
