-- One page of the tool calls one api call made. The same query serves the rows shown under a
-- call and the rows behind its "+N more": pages of one list, so the two cannot disagree.
-- Keyset on "index", unique and ascending within a (session, source); `$after` is the last
-- index already shown, and -1 asks for the first page.
-- `input` is previewed here and fetched whole one value at a time (`view_tool_value`).
SELECT
    t."index" AS tool_index,
    t.id AS tool_call_id,
    t.name,
    t.server_side,
    t.is_error,
    t.incomplete,
    t.offload_file,
    t.started_at,
    substr(t.input, 1, 200) AS input_head,
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
