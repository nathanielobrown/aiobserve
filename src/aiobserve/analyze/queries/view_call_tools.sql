-- One page of the tool calls one api call made: the children log under a call's node page.
-- Ordered by "index", unique and ascending within a (session, source); `$skipped` is how
-- many tool calls the pages before this one held, and 0 asks for the first page.
--
-- A row carries the head of what the tool was asked, because a name alone tells no two calls
-- of one tool apart: a page of twenty `Read` rows says twenty times that a file was read.
-- Which part of the input that head is comes from the input itself and not from a list of
-- tool names, so a tool nobody here has heard of still summarises itself:
--   * a `file_path` is the path, cut to the repo when it sits inside the session's own
--     project directory and absolute when it does not — an agent reads its own tree far more
--     than anything else, and a column of identical prefixes is a column of nothing
--   * else a `description` — what the caller said the call was for, which is what `Bash` and
--     `Agent` put there — with the `command` under it as texture where there is one
--   * else the head of the input as it was stored, which is JSON for every tool we have seen
-- Every read of the input is guarded, because `input` is whatever the transcript held and
-- `json_extract_string` raises on a value that is not JSON: a malformed input is a row to
-- render, not a 500.
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
        -- The three fields a row may name itself by, and the input as stored behind them.
        -- Each is cut here, at the width of the column that would print it.
        CASE WHEN json_valid(t.input)
             THEN substr(json_extract_string(t.input, '$.file_path'), 1, $log_chars + 1)
             END AS asked_path,
        CASE WHEN json_valid(t.input)
             THEN substr(json_extract_string(t.input, '$.description'), 1, $log_chars + 1)
             END AS asked_for,
        CASE WHEN json_valid(t.input)
             THEN substr(json_extract_string(t.input, '$.command'), 1, $log_chars + 1)
             END AS asked_ran,
        substr(t.input, 1, $log_chars + 1) AS asked_raw,
        -- What a path is read against. LEFT joined, so a tool call whose session row is
        -- missing is a row with an absolute path rather than a row the page drops.
        s.project_dir
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
    -- The one-extra-character protocol every cut column rides: the parts above come back one
    -- character past the width, so a head that fills the column says the value went on.
    -- Cutting the repository off a path shortens what a full column shows, which is the point.
    coalesce(
        CASE WHEN starts_with(asked_path, project_dir || '/')
             THEN substr(asked_path, length(project_dir) + 2)
             ELSE asked_path END,
        asked_for,
        asked_raw) AS input_head,
    -- The line under the head, where the head was a description and the input also carried
    -- the command it describes. NULL everywhere else, including on the rows whose head is
    -- already the command's own JSON — a row does not print one value twice.
    CASE WHEN asked_path IS NULL AND asked_for IS NOT NULL THEN asked_ran END AS command
FROM page
ORDER BY tool_index;
