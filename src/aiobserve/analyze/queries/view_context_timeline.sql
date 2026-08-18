-- One thread's context size over time, a row per turn that made at least one api call.
-- Reads the `live_*` family like the digests do, so a resumed session shows the work it ran.
-- A turn that made no call carries no row: there is nothing to plot for it, the same silence
-- `session_digest`'s spend CTE already tolerates.
-- Aggregated to at most $max_points rows. `rn // greatest(ceil(total / $max_points), 1)` is
-- the whole bucketing rule: at or under $max_points turns the divisor is 1, so every bucket
-- holds one turn and nothing is averaged away; above it, consecutive turns group in index
-- order. Context size is a snapshot rather than a sum, so it takes the last value in a group
-- (`arg_max`) while the four composition columns sum — those are spend.
WITH turn AS (
    SELECT id AS turn_id, "index" AS turn_index, started_at
    FROM live_turns WHERE session_id = $session_id AND source = $source
), call AS (
    SELECT
        turn_id, "index" AS call_index, input_tokens, output_tokens,
        cache_read_tokens, cache_creation_tokens, cache_5m_tokens, cache_1h_tokens
    FROM live_api_calls
    WHERE session_id = $session_id AND source = $source AND turn_id IS NOT NULL
), per_turn AS (
    SELECT
        turn.turn_index,
        turn.started_at,
        row_number() OVER (ORDER BY turn.turn_index) - 1 AS rn,
        count(*) OVER () AS total_turns,
        count(*) AS api_calls,
        -- What the context had grown to by the end of the turn: the last call's own size,
        -- which is the value the chart would show if it drew every turn.
        arg_max(
            call.input_tokens + call.cache_read_tokens + call.cache_creation_tokens,
            call.call_index
        ) AS context_tokens,
        sum(call.input_tokens) AS input_tokens,
        sum(call.output_tokens) AS output_tokens,
        sum(call.cache_read_tokens) AS cache_read_tokens,
        sum(call.cache_creation_tokens) AS cache_creation_tokens,
        sum(call.cache_5m_tokens) AS cache_5m_tokens,
        sum(call.cache_1h_tokens) AS cache_1h_tokens,
        -- Whether every call in the group reported the cache lifetime split. False makes the
        -- two split columns a floor rather than a total (`docs/schema.md`).
        bool_and(call.cache_5m_tokens IS NOT NULL) AS split_known
    FROM turn JOIN call ON call.turn_id = turn.turn_id
    GROUP BY turn.turn_index, turn.started_at
), bucket AS (
    SELECT *, rn // greatest(ceil(total_turns::DOUBLE / $max_points)::BIGINT, 1) AS bucket_index
    FROM per_turn
)
SELECT
    bucket_index,
    min(turn_index) AS first_turn_index,
    max(turn_index) AS last_turn_index,
    -- The bucket's own clock, which is what places a compaction marker between two points.
    min(started_at) AS started_at,
    sum(api_calls) AS api_calls,
    arg_max(context_tokens, turn_index) AS context_tokens,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(cache_read_tokens) AS cache_read_tokens,
    sum(cache_creation_tokens) AS cache_creation_tokens,
    sum(cache_5m_tokens) AS cache_5m_tokens,
    sum(cache_1h_tokens) AS cache_1h_tokens,
    bool_and(split_known) AS split_known,
    any_value(total_turns) AS total_turns
FROM bucket GROUP BY bucket_index ORDER BY bucket_index;
