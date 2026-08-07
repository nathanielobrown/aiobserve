-- The one row a session reader starts from: the headline numbers its report's front matter
-- carries, and the extraction stamp that says which read of the transcript produced them.
-- Keyed, so it reads the `live_*` family: a resumed session's copied records are in the file
-- its reader is reading. Corpus counts drop them; this row does not, which is why a session's
-- numbers here can exceed the same session's row in a corpus count.
SELECT
    r.session_id,
    r.started_at,
    r.turns,
    r.api_calls,
    r.tool_calls,
    (SELECT count(*) FILTER (t.is_error) FROM live_tool_calls t
        WHERE t.session_id = $session_id) AS tool_errors,
    r.agent_runs,
    r.compactions,
    round(r.cost_usd, 4) AS cost_usd,
    r.unpriced_api_calls,
    -- Names, not content: which skills ran and which commands started a turn.
    (SELECT string_agg(DISTINCT c.attribution_skill, ' ') FROM live_api_calls c
        WHERE c.session_id = $session_id AND c.attribution_skill IS NOT NULL) AS skills,
    (SELECT string_agg(DISTINCT t.command_name, ' ') FROM live_turns t
        WHERE t.session_id = $session_id AND t.command_name IS NOT NULL) AS commands,
    -- What a later iteration compares to decide whether this session needs re-reading.
    e.fingerprint AS extract_fingerprint,
    e.extractor_version
FROM session_rollups r
LEFT JOIN extract_state e USING (session_id)
WHERE r.session_id = $session_id;
