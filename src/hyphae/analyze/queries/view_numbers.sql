-- The exact numbers behind one node's NavTree row: where it left the model's context window, and
-- where its dollars went. What the row draws as a bar and a badge, written out (`docs/viewer.md`).
--
-- `$kind` names which api calls the node *is* — the one rule that differs between a session,
-- an agent run, a turn and a call. Written once here rather than as a query apiece, because
-- four files answering one question are four chances for a popover to deny the row it opened
-- from. A tool call has no api calls of its own and is not a kind here; `view_numbers_tool.sql`
-- answers for one.
--
-- `$source` is the thread the node's window stands on: `main` for a session, whose reader is
-- reading the main thread; the run's own id for a run; the node's own thread otherwise. It is
-- also what picks the calls out, for every kind but a session, which spends on every thread it spawned.
--
-- Synthetic replies are out of the window numbers for the reason `view_nav_tree_turns` states —
-- Claude Code's own placeholders report no tokens at all (`docs/schema.md`) — and in the
-- spend, where our table prices them at nothing.
WITH calls AS (
    -- The calls this node is, and the per-call numbers everything below reads. The model is
    -- cut here and looked up here, so no row past this one carries the column whole — and it
    -- is renamed as it is cut, because the payload scan that holds the bound reads query text
    -- and cannot see through a CTE (`tests/view/test_bounds.py`).
    SELECT
        c.id,
        c.source,
        c.turn_id,
        c."index",
        c.synthetic,
        substr(c.model, 1, $model_chars + 1) AS model_name,
        context_window(c.model) AS window_tokens,
        context_fill(c) AS fill,
        context_added(c) AS added,
        c.input_tokens,
        c.output_tokens,
        c.cache_read_tokens,
        c.cache_creation_tokens,
        c.cache_5m_tokens,
        c.cache_1h_tokens,
        c.cost_usd
    FROM live_api_calls c
    WHERE c.session_id = $session_id
      AND CASE $kind
            WHEN 'session' THEN true
            WHEN 'run' THEN c.source = $source
            WHEN 'turn' THEN c.source = $source AND c.turn_id = $node_id
            WHEN 'call' THEN c.source = $source AND c.id = $node_id
            ELSE error('view_numbers: no such node kind: ' || $kind)
          END
), held AS (
    -- The call the node's bar was drawn from: the last one on its thread that went to a model.
    -- Every column comes off that one call, which is what makes the three token counts below
    -- add up to the fill instead of describing three different moments.
    SELECT
        max_by(s.model_name, s."index") AS model_name,
        max_by(s.window_tokens, s."index") AS window_tokens,
        max_by(s.fill, s."index") AS fill,
        max_by(s.added, s."index") AS added,
        max_by(s.cache_read_tokens, s."index") AS cached_tokens,
        max_by(s.input_tokens + s.cache_creation_tokens, s."index") AS new_input_tokens,
        max_by(s.output_tokens, s."index") AS output_tokens
    FROM calls s
    WHERE NOT s.synthetic AND s.source = $source
), ends AS (
    -- Where every turn of this thread left the window, so a turn can be measured against the
    -- one before it. Un-clamped, unlike the NavTree's: a compaction inside a turn leaves the
    -- window below where the turn before it stood, and the negative is the number to print.
    -- Read only when a turn asked, since no other kind is measured against a sibling.
    SELECT
        t.id,
        e.fill - coalesce(lag(e.fill IGNORE NULLS) OVER (ORDER BY t."index"), 0) AS added
    FROM live_turns t
    LEFT JOIN (
        SELECT c.turn_id, max_by(context_fill(c), c."index") AS fill
        FROM live_api_calls c
        WHERE c.session_id = $session_id AND c.source = $source AND NOT c.synthetic
        GROUP BY c.turn_id
    ) e ON e.turn_id = t.id
    WHERE t.session_id = $session_id AND t.source = $source AND $kind = 'turn'
), spent AS (
    -- The node's tokens by model, which is what a phase has to be priced from: a phase can mix
    -- models, so one summed row times one price would charge a Haiku call at Opus rates. The
    -- cache write follows `extract/pricing.py` call for call — a call that reported no TTL
    -- split puts its whole write on the 5-minute rate — so the four charges the caller derives
    -- come to the stored total below.
    SELECT
        s.model_name AS model,
        sum(s.input_tokens) AS input_tokens,
        sum(s.output_tokens) AS output_tokens,
        sum(s.cache_read_tokens) AS cache_read_tokens,
        sum(s.cache_creation_tokens) AS cache_creation_tokens,
        sum(coalesce(s.cache_5m_tokens, s.cache_creation_tokens)) AS cache_5m_tokens,
        sum(coalesce(s.cache_1h_tokens, 0)) AS cache_1h_tokens
    FROM calls s
    GROUP BY s.model_name
)
SELECT
    held.model_name AS model,
    -- NULL where our table holds no window for the model, which the popover says rather than
    -- scaling the numbers beside it to a guess.
    held.window_tokens,
    held.cached_tokens,
    held.new_input_tokens,
    held.output_tokens,
    held.fill,
    -- What the node put into the window, by the rule its kind is measured under: a turn against
    -- the turn before it, a run against the empty window it started on, a call against the cache
    -- it read. A session is measured against nothing, and says so.
    CASE $kind
        WHEN 'turn' THEN (SELECT e.added FROM ends e WHERE e.id = $node_id)
        WHEN 'run' THEN held.fill
        WHEN 'call' THEN held.added
    END AS added,
    (SELECT round(sum(s.cost_usd), 4) FROM calls s) AS cost_usd,
    (SELECT count(*) FILTER (s.cost_usd IS NULL) FROM calls s) AS unpriced_api_calls,
    (SELECT count(*) FROM calls s) AS api_calls,
    -- One member per model the node spent on, for the caller to price (`view/numbers.py`).
    -- A list rather than rows because the popover is one row of numbers: the split is summed
    -- across models before anything prints it.
    (SELECT coalesce(list(spent), []) FROM spent) AS spent
FROM held;
