-- Which agent runs an iteration reads on top of its selected sessions. Session strata rank
-- whole sessions, so a rarely-used agent definition can go unread for iterations; this draw
-- is per `agent_type`, which is what gets every commonly used definition looked at.
-- Each type gives up its runs that went furthest in two directions — the most tool errors,
-- then the most spent — under the session strata's tiebreak, and a run taken for its errors
-- is not taken again for its cost.
WITH agent_run AS (
    SELECT
        a.session_id,
        a.id AS agent_id,
        a.agent_type,
        a.started_at,
        -- A run's own rows sit at `source = agent_id`; the runs it spawned have sources of
        -- their own, so nothing here fans out over a subtree.
        (SELECT count(*) FROM corpus_tool_calls t
            WHERE t.session_id = a.session_id AND t.source = a.id) AS tool_calls,
        (SELECT count(*) FILTER (t.is_error) FROM corpus_tool_calls t
            WHERE t.session_id = a.session_id AND t.source = a.id) AS tool_errors,
        (SELECT count(*) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS api_calls,
        (SELECT coalesce(sum(c.cost_usd), 0) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS cost_usd,
        (SELECT count(*) FILTER (c.cost_usd IS NULL) FROM corpus_api_calls c
            WHERE c.session_id = a.session_id AND c.source = a.id) AS unpriced_api_calls
    FROM project_sessions p
    JOIN corpus_agent_runs a USING (session_id)
    WHERE p.in_window
), major_type AS (
    -- `agent_type` is an open set: a session names its own subagents, and one-off names run
    -- once and never again. Without a floor the draw hands a reading slot to every one of
    -- them, which is how ~20 runs becomes 75. A definition earns its slot by being used.
    SELECT agent_type FROM agent_run GROUP BY agent_type HAVING count(*) >= $min_runs
), worst AS (
    -- Only runs that actually erred: a `run-errors` tag on a clean run would lie, and a type
    -- whose runs all ran clean simply contributes nothing here.
    SELECT 'run-errors' AS stratum, session_id, agent_id, agent_type,
        row_number() OVER (
            PARTITION BY agent_type ORDER BY tool_errors DESC, session_id, agent_id
        ) AS rank
    FROM agent_run JOIN major_type USING (agent_type) WHERE tool_errors > 0
), taken AS (
    SELECT * FROM worst WHERE rank <= $runs_per_stratum
), costliest AS (
    -- Ranked after the errored runs are removed, so the cost slot walks down to a run nobody
    -- is reading yet rather than naming one twice.
    SELECT 'run-cost' AS stratum, r.session_id, r.agent_id, r.agent_type,
        row_number() OVER (
            PARTITION BY r.agent_type ORDER BY r.cost_usd DESC, r.session_id, r.agent_id
        ) AS rank
    FROM agent_run r
    JOIN major_type m ON m.agent_type = r.agent_type
    WHERE r.cost_usd > 0
      AND NOT EXISTS (
          SELECT 1 FROM taken t
          WHERE t.session_id = r.session_id AND t.agent_id = r.agent_id
      )
), pick AS (
    SELECT * FROM taken
    UNION ALL
    SELECT * FROM costliest WHERE rank <= $runs_per_stratum
)
SELECT
    k.stratum,
    r.agent_type,
    r.session_id,
    r.agent_id,
    r.started_at,
    r.tool_calls,
    r.tool_errors,
    r.api_calls,
    round(r.cost_usd, 4) AS cost_usd,
    r.unpriced_api_calls
FROM pick k
JOIN agent_run r USING (session_id, agent_id)
ORDER BY r.agent_type, k.stratum, r.session_id, r.agent_id;
