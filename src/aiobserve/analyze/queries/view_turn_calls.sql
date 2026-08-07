-- One page of the api calls under one turn: what the model said, and what it cost to say it.
-- A turn is not a bounded unit — the largest in the canonical store aggregates megabytes
-- across hundreds of tool calls — so the page is the unit and `$page_calls` is its size.
-- Keyset on "index", which is unique and ascending within a (session, source): `$after` is
-- the last index already shown, and -1 asks for the first page.
-- `$turn_id` NULL selects the calls that sit under no turn, which is the unattributed row on
-- a session page and the continuation section on a run page.
-- `text` is previewed here and fetched whole one value at a time (`view_call_text`); the
-- tool rows under a call are their own query (`view_call_tools`), capped the same way.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    c.model,
    c.fallback_from,
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
    -- The preview and the size of what was cut, so the page can say there is more to fetch
    -- rather than leaving a reader to guess whether the answer ended there.
    substr(c.text, 1, 2000) AS text_head,
    length(c.text) AS text_chars,
    length(c.thinking) AS thinking_chars,
    (
        SELECT count(*) FROM live_tool_calls t
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS tool_calls,
    -- How many calls the cursor still has ahead of it, counted before the LIMIT bites, so
    -- the page knows whether to offer another without a second query.
    count(*) OVER () AS matched_api_calls
FROM live_api_calls c
WHERE c.session_id = $session_id
  AND c.source = $source
  AND c.turn_id IS NOT DISTINCT FROM $turn_id
  AND c."index" > $after
ORDER BY c."index"
LIMIT $page_calls;
