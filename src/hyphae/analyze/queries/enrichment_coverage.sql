-- How much of the corpus enrichment has described, and what it said about it. One row per
-- period, level, category, outcome and stamp; `level_items` is the level's whole population,
-- so `share_pct` reads straight off the row.
-- A NULL category is the level's undescribed items — its coverage gap — and a stamp column
-- moving is the other health signal: rows written by two models, or under two prompt
-- versions, are a pass someone stopped halfway.
-- The denominators are the items a pass would describe, which is not every row of a level:
-- enrichment describes main turns, every agent run, and the sessions that drove a model
-- response (`src/hyphae/enrich/store.py`). Counting the rest would report a permanent
-- shortfall.
-- Counts through the `corpus_*` family like everything else here, rather than the
-- `enriched_*` views, which are LEFT joins over `live_*`: a resume's copied turn is enriched
-- under the copy's own key, so dropping the copy drops its enrichment row with it.
WITH item AS (
    SELECT
        p.period,
        'turn' AS level,
        e.category,
        e.outcome,
        e.friction,
        e.model,
        e.prompt_version,
        e.taxonomy_version,
        e.enriched_at
    FROM session_period p
    JOIN corpus_turns t USING (session_id)
    LEFT JOIN turn_enrichments e
        ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id
    WHERE t.source = 'main'
    UNION ALL
    SELECT
        p.period, 'agent_run', e.category, e.outcome, e.friction,
        e.model, e.prompt_version, e.taxonomy_version, e.enriched_at
    FROM session_period p
    JOIN corpus_agent_runs a USING (session_id)
    LEFT JOIN agent_run_enrichments e
        ON e.session_id = a.session_id AND e.agent_run_id = a.id
    UNION ALL
    SELECT
        p.period, 'session', e.category, e.outcome, e.friction,
        e.model, e.prompt_version, e.taxonomy_version, e.enriched_at
    FROM session_period p
    JOIN corpus_rollups r USING (session_id)
    LEFT JOIN session_enrichments e ON e.session_id = r.session_id
    -- Mirrors the `describable_sessions` view enrichment reads: a session with no main turn
    -- and no agent run has nothing to describe, and one whose turns drove no api call has no
    -- model response to describe. Its own copy of the rule, since this counts over
    -- `corpus_rollups` while enrichment reads the live view.
    WHERE (r.turns > 0 OR r.agent_runs > 0) AND r.api_calls > 0
)
SELECT
    period,
    level,
    category,
    outcome,
    -- The enrichment's model, not the agent's: `agent_runs` carries one of its own, and the
    -- three levels answer this question the same way.
    model AS enrichment_model,
    prompt_version,
    taxonomy_version,
    count(*) AS items,
    sum(count(*)) OVER (PARTITION BY period, level) AS level_items,
    round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY period, level), 1) AS share_pct,
    count(*) FILTER (friction IS NOT NULL) AS with_friction,
    max(enriched_at) AS last_enriched_at
FROM item
GROUP BY period, level, category, outcome, model, prompt_version, taxonomy_version
ORDER BY period, level, items DESC, category NULLS FIRST, outcome;
