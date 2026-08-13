-- One session's enrichment sheet: what the model said about the session, each of its main
-- turns, and each of its agent runs. Read it beside the digest that shows what the item
-- actually did — `session_digest` keys on the same `turn_index` and `turn_id`, `run_digest`
-- and `view_runs` on the same run id — which is how a description gets checked against the
-- records it was written from.
-- An item no pass has reached keeps its row with a NULL description, so what is missing is
-- visible rather than absent. Bind `$level` to `turn`, `agent_run` or `session` to read one
-- level; NULL is all three.
-- Reads the `enriched_*` views (`docs/enrichment.md`), so it is the `live_*` family a keyed
-- query always reads: a resumed session shows the rows its own files hold.
WITH item AS (
    SELECT
        'turn' AS level,
        t.source,
        t.id AS item_id,
        t."index" AS item_index,
        t.command_name AS label,
        t.started_at,
        t.description,
        t.category,
        t.outcome,
        t.friction,
        t.enrichment_model,
        t.prompt_version,
        t.taxonomy_version,
        t.enriched_at
    FROM enriched_turns t
    WHERE t.session_id = $session_id AND t.source = 'main'
    UNION ALL
    -- A run's id is also the source its rows carry, so one column opens both digests. Its
    -- `agent_type` is the label, which is what a reader recognises a run by.
    SELECT
        'agent_run', r.id, r.id, NULL, r.agent_type, r.started_at,
        r.description, r.category, r.outcome, r.friction,
        r.enrichment_model, r.prompt_version, r.taxonomy_version, r.enriched_at
    FROM enriched_agent_runs r
    WHERE r.session_id = $session_id
    UNION ALL
    SELECT
        'session', NULL, NULL, NULL, NULL, s.started_at,
        s.description, s.category, s.outcome, s.friction,
        s.enrichment_model, s.prompt_version, s.taxonomy_version, s.enriched_at
    FROM enriched_sessions s
    WHERE s.session_id = $session_id
)
SELECT * FROM item
WHERE $level::VARCHAR IS NULL OR level = $level
ORDER BY level, item_index NULLS LAST, item_id;
