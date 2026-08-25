-- One page of the tool calls one api call made: the children log under a call's node page.
-- Ordered by "index", unique and ascending within a (session, source); `$skipped` is how
-- many tool calls the pages before this one held, and 0 asks for the first page.
--
-- A row carries the tool call's title, because a name alone tells no two calls of one tool
-- apart: a page of twenty `Read` rows says twenty times that a file was read. What a tool call
-- is titled is `tool_title` (`analyze/macros.py`), shared with the three other surfaces that
-- name one, and `tool_ran` is the command a title that is a description describes.
WITH page AS (
    SELECT
        t."index" AS tool_index,
        t.id AS tool_call_id,
        substr(t.name, 1, $log_chars + 1) AS name,
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
        count(*) OVER () AS matched_tool_calls,
        -- Cut at the width of the column that prints them, one character past it.
        tool_title(t.input, s.project_dir, $log_chars) AS title,
        tool_ran(t.input, $log_chars) AS command
    FROM live_tool_calls t
    LEFT JOIN sessions s ON s.id = t.session_id
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
    matched_tool_calls,
    title,
    command
FROM page
ORDER BY tool_index;
