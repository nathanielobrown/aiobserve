-- What kind of session each one was, as a rule over the rollup columns. A shape is worth
-- having only if it is citable: the classifier is a `CASE` anyone can read and argue with,
-- so "delegation-heavy sessions cost more" is a claim about a stated predicate rather than a
-- reader's impression of what they skimmed.
-- The ladder is ordered, first match wins, and every threshold is bound — the shapes are a
-- starting vocabulary, and an iteration that finds them cutting badly rebinds and re-runs.
WITH measured AS (
    SELECT
        p.period,
        r.session_id,
        r.turns,
        r.tool_calls,
        r.agent_runs,
        r.compactions,
        r.cost_usd,
        r.unpriced_api_calls,
        coalesce(e.edit_calls, 0) AS edit_calls,
        -- Share of the session's api calls made while some one skill was loaded. Read as
        -- "one skill ran most of this session", which is the orchestrated shape; a session
        -- that touched four skills briefly stays out of it.
        coalesce(k.top_skill_calls, 0) * 100 / nullif(r.api_calls, 0) AS top_skill_pct
    FROM session_period p
    JOIN corpus_rollups r USING (session_id)
    LEFT JOIN (
        SELECT session_id, count(*) AS edit_calls
        FROM corpus_tool_calls
        WHERE name IN ('Edit', 'Write', 'NotebookEdit')
        GROUP BY session_id
    ) e USING (session_id)
    LEFT JOIN (
        SELECT session_id, max(calls) AS top_skill_calls
        FROM (
            SELECT session_id, attribution_skill, count(*) AS calls
            FROM corpus_api_calls
            WHERE attribution_skill IS NOT NULL
            GROUP BY session_id, attribution_skill
        ) GROUP BY session_id
    ) k USING (session_id)
), classified AS (
    SELECT
        m.*,
        CASE
            -- Nothing of its own left to read: `corpus_rollups` credits a resume's copied
            -- records to the session that ran them first.
            WHEN m.turns = 0 AND m.agent_runs = 0 THEN 'no-work'
            WHEN m.top_skill_pct >= $skill_share_pct THEN 'skill-orchestrated'
            WHEN m.agent_runs >= $delegating_runs THEN 'delegation-heavy'
            WHEN m.edit_calls >= $editing_calls THEN 'solo-editing'
            WHEN m.edit_calls = 0 AND m.tool_calls >= $busy_tool_calls THEN 'read-only-analysis'
            WHEN m.tool_calls < $busy_tool_calls THEN 'conversational'
            ELSE 'mixed'
        END AS shape
    FROM measured m
)
SELECT
    period,
    shape,
    count(*) AS sessions,
    sum(turns) AS turns,
    sum(tool_calls) AS tool_calls,
    sum(edit_calls) AS edit_calls,
    sum(agent_runs) AS agent_runs,
    sum(compactions) AS compactions,
    round(sum(cost_usd), 4) AS cost_usd,
    round(avg(cost_usd), 4) AS cost_usd_per_session,
    sum(unpriced_api_calls) AS unpriced_api_calls
FROM classified
GROUP BY period, shape
ORDER BY period, sessions DESC, shape;
