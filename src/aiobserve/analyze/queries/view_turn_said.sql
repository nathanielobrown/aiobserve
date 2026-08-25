-- The whole of what an enrichment pass wrote about one turn — the description of what the
-- turn did, and the friction the model saw in it. A per-value query: the pane previews both
-- at `ENRICHMENT_CHARS` and this is what the link beside a cut one fetches.
-- Keyed by the thread as well as the session, because a turn id is unique within one.
SELECT e.description, e.friction
FROM turn_enrichments e
WHERE e.session_id = $session_id AND e.source = $source AND e.turn_id = $turn_id;
