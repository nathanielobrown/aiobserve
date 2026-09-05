-- The whole of what an enrichment pass said one turn did. A per-value query: the pane
-- previews it at `ENRICHMENT_CHARS` and this is what the link beside the cut head fetches.
-- Keyed by the thread as well as the session, because a turn id is unique within one.
SELECT e.description AS value
FROM turn_enrichments e
WHERE e.session_id = $session_id AND e.source = $source AND e.turn_id = $turn_id;
