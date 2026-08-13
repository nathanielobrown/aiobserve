-- Which described items a validation read spot-checks: a seeded draw over one level,
-- `$per_category` items from each category. Stratifying by category is what puts a rare
-- member of the vocabulary in front of a reader — a flat draw over thousands of runs returns
-- the common ones, and the categories nobody checks are the ones a classifier gets wrong.
-- The pool is every session `--project` selects rather than the trailing window: a pass
-- describes the whole store, and validating it reads the same set. `--since` narrows it.
-- Each row carries what it takes to open the item — the session and source `run_digest`,
-- `session_digest` and `enrichment_digest` are bound at — and what it costs to read it.
-- The seed is bound, so the draw is one anyone can re-run and a later read can rotate.
WITH described AS (
    SELECT
        e.category,
        t.session_id,
        t.source,
        t.id AS item_id,
        -- What the seed is hashed with: the item's whole key, so no two levels and no two
        -- items share a draw position.
        t.session_id || '|' || t.source || '|' || t.id AS item_key,
        NULL::VARCHAR AS agent_type,
        (SELECT count(*) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source
              AND c.turn_id = t.id) AS api_calls,
        (SELECT coalesce(sum(c.cost_usd), 0) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source
              AND c.turn_id = t.id) AS cost_usd,
        (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM corpus_api_calls c
            WHERE c.session_id = t.session_id AND c.source = t.source
              AND c.turn_id = t.id) AS unpriced_api_calls,
        e.description,
        e.outcome,
        e.friction,
        e.model,
        e.prompt_version,
        e.enriched_at
    FROM project_sessions p
    JOIN corpus_turns t USING (session_id)
    JOIN turn_enrichments e
        ON e.session_id = t.session_id AND e.source = t.source AND e.turn_id = t.id
    -- Each level's arm carries the level test, so the two nobody asked for read no rows.
    WHERE t.source = 'main' AND $level = 'turn'
    UNION ALL
    -- A run's own thread: the runs it spawned have sources of their own, and their own rows
    -- in this draw.
    SELECT
        e.category, a.session_id, a.id, a.id, a.session_id || '|' || a.id, a.agent_type,
        (SELECT count(*) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id),
        (SELECT coalesce(sum(c.cost_usd), 0) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id),
        (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id),
        e.description, e.outcome, e.friction, e.model, e.prompt_version, e.enriched_at
    FROM project_sessions p
    JOIN corpus_agent_runs a USING (session_id)
    JOIN agent_run_enrichments e ON e.session_id = a.session_id AND e.agent_run_id = a.id
    WHERE $level = 'agent_run'
    UNION ALL
    SELECT
        e.category, r.session_id, NULL, NULL, r.session_id, NULL,
        r.api_calls, r.cost_usd, r.unpriced_api_calls,
        e.description, e.outcome, e.friction, e.model, e.prompt_version, e.enriched_at
    FROM project_sessions p
    JOIN corpus_rollups r USING (session_id)
    JOIN session_enrichments e ON e.session_id = r.session_id
    WHERE $level = 'session'
), pick AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY category ORDER BY hash(item_key || $seed), item_key
        ) AS draw
    FROM described
)
SELECT
    category AS stratum,
    session_id,
    source,
    item_id,
    agent_type,
    api_calls,
    round(cost_usd, 4) AS cost_usd,
    unpriced_api_calls,
    description,
    outcome,
    friction,
    model AS enrichment_model,
    prompt_version,
    enriched_at,
    draw
FROM pick
WHERE draw <= $per_category
ORDER BY category, draw;
