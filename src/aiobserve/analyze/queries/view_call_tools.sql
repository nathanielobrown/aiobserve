-- One page of the tool calls one api call made: the children log under a call's node page.
-- Keyset on "index", unique and ascending within a (session, source); `$after` is the last
-- index already shown, and -1 asks for the first page.
SELECT
    t."index" AS tool_index,
    t.id AS tool_call_id,
    substr(t.name, 1, $log_chars) AS name,
    t.server_side,
    t.is_error,
    t.incomplete,
    t.offload_file,
    t.started_at,
    -- Sizes only: what the tool was asked and what it answered are on its own page, which
    -- this row links to. A log row carrying a preview would price a page of twelve at one.
    length(t.input) AS input_chars,
    -- NULL where the tool returned nothing at all, which is not the same as returning "".
    length(t.result) AS result_chars,
    -- How many rows the cursor still has ahead of it, counted before the LIMIT bites. The
    -- page subtracts what it shows to say "+N more", which is what keeps a cap from looking
    -- like a call that simply made fewer tool calls.
    count(*) OVER () AS matched_tool_calls
FROM live_tool_calls t
WHERE t.session_id = $session_id
  AND t.source = $source
  AND t.api_call_id = $api_call_id
  AND t."index" > $after
ORDER BY t."index"
LIMIT $page_tools;
