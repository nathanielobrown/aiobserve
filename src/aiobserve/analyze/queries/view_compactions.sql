-- The compactions one thread ran, as markers the timeline interleaves by time.
-- Keyed by source as well as session: `main` for a session page, a run id for a run page.
SELECT
    k.id AS compaction_id,
    k.timestamp,
    k.trigger,
    k.pre_tokens,
    k.post_tokens,
    k.duration_ms
FROM live_compactions k
WHERE k.session_id = $session_id AND k.source = $source
ORDER BY k.timestamp;
