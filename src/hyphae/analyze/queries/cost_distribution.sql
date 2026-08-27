-- How spend is spread across sessions, not just how much of it there was. A mean hides the
-- shape that matters here: most corpora of coding sessions are a long tail plus a handful of
-- sessions that cost more than the rest combined, and a recommendation aimed at the mean
-- would be aimed at a session that does not exist.
-- `top_decile_share` is the fraction of spend the costliest tenth of sessions carries.
WITH ranked AS (
    SELECT
        p.period,
        r.cost_usd,
        r.input_tokens + r.output_tokens + r.cache_read_tokens + r.cache_creation_tokens
            AS tokens,
        r.unpriced_api_calls,
        percent_rank() OVER (PARTITION BY p.period ORDER BY r.cost_usd) AS cost_percentile
    FROM session_period p
    JOIN corpus_rollups r USING (session_id)
)
SELECT
    period,
    count(*) AS sessions,
    round(sum(cost_usd), 4) AS cost_usd,
    round(avg(cost_usd), 4) AS mean_cost_usd,
    round(quantile_cont(cost_usd, 0.5), 4) AS p50_cost_usd,
    round(quantile_cont(cost_usd, 0.9), 4) AS p90_cost_usd,
    round(quantile_cont(cost_usd, 0.99), 4) AS p99_cost_usd,
    round(max(cost_usd), 4) AS max_cost_usd,
    round(
        sum(cost_usd) FILTER (cost_percentile >= 0.9) / nullif(sum(cost_usd), 0), 4
    ) AS top_decile_share,
    sum(tokens) AS tokens,
    sum(unpriced_api_calls) AS unpriced_api_calls
FROM ranked
GROUP BY period
ORDER BY period;
