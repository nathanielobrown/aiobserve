-- One page of the api calls under one turn: the children log under a turn's node page.
-- A turn is not a bounded unit — the largest in the canonical store aggregates megabytes
-- across hundreds of tool calls — so the page is the unit and `$page_calls` is its size.
-- Ordered by "index", which is unique and ascending within a (session, source): `$skipped`
-- is how many calls the pages before this one held, and 0 asks for the first page.
-- `$turn_id` NULL selects this thread's unattributed calls, which is the bucket's own page.
-- Unattributed is decided by the join and not by `c.turn_id IS NULL`, for the reason
-- `view_nav_tree_calls` states: a call naming a turn recorded on another thread is this thread's.
-- `text` is previewed here and fetched whole one value at a time (`view_call_text`); the
-- tool rows under a call are their own query (`view_call_tools`), capped the same way.
--
-- A row names the tool calls it made as well as counting them: what comes back is the fields
-- each of them is named by (`analyze/macros.py:tool_fields`) and the words are composed in
-- Python (`view/builders.py:tool_titles`), the same derivation the tools log's own rows read,
-- so a call's row and the log inside it name one tool the same way. Every tool comes back and
-- the composed line is cut whole where it is printed: what a reader gets is the first tools of
-- a call that made forty, marked where the column ran out, rather than forty stubs.
SELECT
    c."index" AS call_index,
    c.id AS api_call_id,
    -- The two model names, cut like every other string a repeated row shows: what an api
    -- request carried is Claude Code's to lengthen, and a call row rides a page of a hundred.
    substr(c.model, 1, $log_chars + 1) AS model,
    substr(c.fallback_from, 1, $log_chars) AS fallback_from,
    -- What the call itself said, at the width of the column that prints it. The row's own
    -- words: a call that answered with tool calls and nothing else has none, and the model
    -- beside it is what the row is named by.
    substr(c.text, 1, $log_chars + 1) AS text_head,
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
    (
        SELECT list({
            'name': t.name,
            'fields': tool_fields(t.input, s.project_dir, ad.agent_type, $log_chars)
        } ORDER BY t."index")
        FROM live_tool_calls t
        -- What a path in the fields reads against. Joined here rather than taken from the
        -- outer row: DuckDB 1.5.5 cannot bind a struct-returning macro over a correlated
        -- column, and answers `Need named argument for struct pack`.
        LEFT JOIN sessions s ON s.id = t.session_id
        -- Who a `SendMessage` addressed, resolved the way `view_call_tools` resolves it: a
        -- name reading the run id here would name the call differently from its own row.
        LEFT JOIN live_agent_runs ad
            ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $log_chars)
        WHERE t.session_id = c.session_id AND t.source = c.source AND t.api_call_id = c.id
    ) AS called_tools,
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
