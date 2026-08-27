-- What each skill cost and how often anyone reached for it. Two different questions, joined
-- here because reading either alone misleads: `attribution_skill` marks the api calls made
-- while a skill was loaded, so a skill invoked once can carry hundreds of them, and a skill
-- invoked constantly can carry none because its work runs as plain turns.
WITH attributed AS (
    SELECT
        p.period,
        c.attribution_skill AS skill,
        count(*) AS api_calls,
        count(DISTINCT c.session_id) AS sessions,
        sum(c.cost_usd) AS cost_usd,
        count(*) FILTER (c.cost_usd IS NULL) AS unpriced_api_calls
    FROM session_period p
    JOIN corpus_api_calls c USING (session_id)
    WHERE c.attribution_skill IS NOT NULL
    GROUP BY 1, 2
), invoked AS (
    -- A `Skill` call's input names the skill (docs/schema.md). A row whose input is not JSON
    -- lands under a NULL skill rather than being filtered away, so a shape change shows up as
    -- a row a reader can see instead of a count that quietly shrank.
    SELECT
        p.period,
        CASE WHEN json_valid(t.input) THEN json_extract_string(t.input, '$.skill') END AS skill,
        count(*) AS invocations,
        count(DISTINCT t.session_id) AS invoking_sessions
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    WHERE t.name = 'Skill'
    GROUP BY 1, 2
)
SELECT
    coalesce(a.period, i.period) AS period,
    coalesce(a.skill, i.skill) AS skill,
    coalesce(i.invocations, 0) AS invocations,
    coalesce(i.invoking_sessions, 0) AS invoking_sessions,
    coalesce(a.api_calls, 0) AS api_calls,
    coalesce(a.sessions, 0) AS sessions,
    round(coalesce(a.cost_usd, 0), 4) AS cost_usd,
    coalesce(a.unpriced_api_calls, 0) AS unpriced_api_calls
FROM attributed a
FULL JOIN invoked i ON i.period = a.period AND i.skill = a.skill
ORDER BY period, api_calls DESC, invocations DESC, skill;
