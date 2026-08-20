-- One page of the api calls under one turn: the children log under a turn's node page.
-- A turn is not a bounded unit — the largest in the canonical store aggregates megabytes
-- across hundreds of tool calls — so the page is the unit and `$page_calls` is its size.
-- Ordered by "index", which is unique and ascending within a (session, source): `$skipped`
-- is how many calls the pages before this one held, and 0 asks for the first page.
-- `$turn_id` NULL selects this thread's unattributed calls, which is the bucket's own page.
-- Unattributed is decided by the join and not by `c.turn_id IS NULL`, for the reason
-- `view_tree_calls` states: a call naming a turn recorded on another thread is this thread's.
-- `text` is previewed here and fetched whole one value at a time (`view_call_text`); the
-- tool rows under a call are their own query (`view_call_tools`), capped the same way.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- The two model names, cut like every other string a repeated row shows: what an api
    -- request carried is Claude Code's to lengthen, and a call row rides a page of a hundred.
    substr(c.model, 1, $log_chars) AS model,
    substr(c.fallback_from, 1, $log_chars) AS fallback_from,
    c.effort,
    c.stop_reason,
    c.attribution_skill,
    c.started_at,
    c.input_tokens,
    c.output_tokens,
    c.cache_read_tokens,
    c.cache_creation_tokens,
    round(c.cost_usd, 4) AS cost_usd,
    -- A NULL cost is a model our price table lacks, not a call that was free. Counted here
    -- as 0 or 1 so a call's cost is marked the way every other cost in the viewer is.
    (c.cost_usd IS NULL)::INTEGER AS unpriced_api_calls,
    -- How much the call said and thought. Sizes only: what it said is on the call's own
    -- page, which this row links to, and a log row that carried a preview would price a page
    -- of a hundred of them at a preview each.
    length(c.text) AS text_chars,
    length(c.thinking) AS thinking_chars,
    (
        SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tool_calls,
    -- How many calls the turn holds in all, counted before the LIMIT bites, so the page
    -- knows how many pages there are without a second query.
    count(*) OVER () AS matched_api_calls
FROM live_api_calls c
LEFT JOIN live_turns t
    ON t.session_id = c.session_id AND t.source = c.source AND t.id = c.turn_id
WHERE c.session_id = $session_id
  AND c.source = $source
  AND t.id IS NOT DISTINCT FROM $turn_id
ORDER BY c."index"
LIMIT $page_calls OFFSET $skipped;
