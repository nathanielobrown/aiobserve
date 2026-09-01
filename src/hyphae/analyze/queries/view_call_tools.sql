-- One page of the tool calls one api call made: the children log under a call's node page.
-- Ordered by "index", unique and ascending within a (session, source); `$skipped` is how
-- many tool calls the pages before this one held, and 0 asks for the first page.
--
-- A row carries the fields the tool call is named by, because a name alone tells no two calls
-- of one tool apart: a page of twenty `Read` rows says twenty times that a file was read. The
-- words are composed in Python (`view/text/tool_names.py`), the derivation every other surface that
-- names a tool call reads.
WITH page AS (
    SELECT
        t."index" AS tool_index,
        t.id AS tool_call_id,
        cut(t.name, $log_chars) AS name,
        t.server_side,
        t.is_error,
        t.incomplete,
        t.offload_file,
        t.started_at,
        -- What the tool answered stays a size: a result is the one thing on the tool's own
        -- page that a hundred rows of preview would cost a hundred panes to carry.
        length(t.input) AS input_chars,
        -- NULL where the tool returned nothing at all, which is not the same as returning "".
        length(t.result) AS result_chars,
        -- How many tool calls the api call made in all, counted before the LIMIT bites. The
        -- page divides it by its own size to say which page of how many this is, which is what
        -- keeps a cap from looking like a call that simply made fewer tool calls.
        count(*) OVER () AS matched_rows,
        -- What the input carried under the names the tools the viewer knows name their calls
        -- by, so a `Read` row reads as a path and a `Bash` row as the command it ran
        -- (`view/text/tool_names.py:FORMATTERS`). Every member cut at the width of the column that
        -- prints it, one character past it.
        tool_fields(t.input, s.project_dir, ad.agent_type, $log_chars) AS fields
    FROM live_tool_calls t
    LEFT JOIN sessions s ON s.id = t.session_id
    -- Who a `SendMessage` addressed, where `to` held an agent run's id rather than a name the
    -- caller typed: one lookup, LEFT so a name that matches no run comes back NULL and the row
    -- prints what was recorded (`view/text/tool_names.py:_send_message`).
    LEFT JOIN live_agent_runs ad
        ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $log_chars)
    WHERE t.session_id = $session_id
      AND t.source = $source
      AND t.api_call_id = $api_call_id
    ORDER BY t."index"
    LIMIT $page_tools OFFSET $skipped
)
SELECT
    tool_index,
    tool_call_id,
    name,
    server_side,
    is_error,
    incomplete,
    offload_file,
    started_at,
    input_chars,
    result_chars,
    matched_rows,
    fields
FROM page
ORDER BY tool_index;
