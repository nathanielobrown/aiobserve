-- Which sessions an iteration reads: a deterministic stratified draw over the trailing
-- window. Strata fill in order — cost, tool errors, compactions, one slot per major skill,
-- then a seeded remainder for discovery — and each walks down its own ranking taking only
-- sessions no earlier stratum took. Without that walk-down the read set collapses onto the
-- same few monster sessions, which is the reason the strata exist.
-- Every quota is bound, so an iteration resets its reading budget without editing the query
-- and the citation says which budget produced the set.
WITH RECURSIVE pool AS (
    -- The draw is over in-window sessions that did work of their own. `corpus_rollups`
    -- credits a resume's copied records to the session that ran them first, so a session
    -- with no turns and no agent runs has nothing left for a reader to read.
    SELECT
        p.session_id,
        r.started_at,
        r.turns,
        r.agent_runs,
        r.tool_calls,
        coalesce(e.tool_errors, 0) AS tool_errors,
        r.compactions,
        r.cost_usd,
        r.unpriced_api_calls
    FROM project_sessions p
    JOIN corpus_rollups r USING (session_id)
    LEFT JOIN (
        SELECT session_id, count(*) FILTER (is_error) AS tool_errors
        FROM corpus_tool_calls
        GROUP BY session_id
    ) e USING (session_id)
    WHERE p.in_window AND (r.turns > 0 OR r.agent_runs > 0)
), skill_use AS (
    -- A skill's users, counted once each: a skill invoked forty times inside one session is
    -- one session's worth of evidence about how it behaves.
    SELECT c.attribution_skill AS skill, l.session_id
    FROM corpus_api_calls c
    JOIN pool l USING (session_id)
    WHERE c.attribution_skill IS NOT NULL
    GROUP BY 1, 2
), major_skill AS (
    SELECT skill FROM skill_use GROUP BY skill HAVING count(*) >= $skill_threshold
), budget AS (
    -- One slot per major skill, so the whole quota sum is the reading budget. It also caps
    -- the set: unused ranked slots fall through to discovery rather than shrinking it.
    SELECT $cost_quota + $error_quota + $compaction_quota + $discovery_quota
        + (SELECT count(*) FROM major_skill) AS total
), stratum_quota AS (
    SELECT 'cost' AS stratum, $cost_quota AS quota
    UNION ALL SELECT 'tool-errors', $error_quota
    UNION ALL SELECT 'compactions', $compaction_quota
    UNION ALL SELECT 'skill:' || skill, 1 FROM major_skill
    -- Discovery is bounded by the total alone. That is the mechanism behind a ranked slot
    -- passing to it: the budget is spent either way.
    UNION ALL SELECT 'discovery', (SELECT total FROM budget)
), offer AS (
    -- What each stratum would take, in the order it would take it. A ranked stratum offers
    -- only sessions with a nonzero metric — a `tool-errors` tag on an error-free session
    -- would lie — so a stratum whose metric runs out stops short instead of padding.
    SELECT 1 AS phase, '' AS skill, 'cost' AS stratum, session_id,
        row_number() OVER (ORDER BY cost_usd DESC, session_id) AS rank
    FROM pool WHERE cost_usd > 0
    UNION ALL
    SELECT 2, '', 'tool-errors', session_id,
        row_number() OVER (ORDER BY tool_errors DESC, session_id)
    FROM pool WHERE tool_errors > 0
    UNION ALL
    SELECT 3, '', 'compactions', session_id,
        row_number() OVER (ORDER BY compactions DESC, session_id)
    FROM pool WHERE compactions > 0
    UNION ALL
    -- One slot per major skill, iterated in skill-name order, each taking its most recent
    -- unselected user, so a skill nobody has read yet gets a reader this iteration.
    SELECT 4, u.skill, 'skill:' || u.skill, u.session_id,
        row_number() OVER (PARTITION BY u.skill ORDER BY l.started_at DESC, u.session_id)
    FROM skill_use u
    JOIN major_skill m USING (skill)
    JOIN pool l USING (session_id)
    UNION ALL
    -- Discovery surfaces friction no ranked metric points at. The seed is bound, so the draw
    -- is one anyone can re-run and an iteration can rotate.
    SELECT 5, '', 'discovery', session_id,
        row_number() OVER (ORDER BY hash(session_id || $seed), session_id)
    FROM pool
), ordered AS (
    SELECT
        row_number() OVER (ORDER BY o.phase, o.skill, o.rank) AS n,
        o.stratum, o.session_id, q.quota, b.total
    FROM offer o
    JOIN stratum_quota q USING (stratum)
    CROSS JOIN budget b
    -- A stratum can walk past at most a full budget of taken sessions before its own quota
    -- fills, so nothing ranked below this is reachable.
    WHERE o.rank <= q.quota + b.total
), fill AS (
    -- The walk itself, one offer at a time: take it when the session is still free, the
    -- stratum still has room, and the budget is not spent. That one rule is the ordered
    -- strata, the walk-down, the run-out, and the fall-through to discovery.
    SELECT 0::BIGINT AS n, []::STRUCT(n BIGINT, stratum VARCHAR, session_id VARCHAR)[] AS chosen
    UNION ALL
    SELECT
        o.n,
        CASE
            WHEN NOT list_contains(list_transform(f.chosen, c -> c.session_id), o.session_id)
                AND len(list_filter(f.chosen, c -> c.stratum = o.stratum)) < o.quota
                AND len(f.chosen) < o.total
            THEN list_append(f.chosen, {'n': o.n, 'stratum': o.stratum, 'session_id': o.session_id})
            ELSE f.chosen
        END
    FROM fill f
    JOIN ordered o ON o.n = f.n + 1
), selected AS (
    SELECT unnest(chosen) AS pick FROM (SELECT chosen FROM fill ORDER BY n DESC LIMIT 1)
)
SELECT
    s.pick.stratum AS stratum,
    s.pick.session_id AS session_id,
    l.started_at,
    l.turns,
    l.agent_runs,
    l.tool_calls,
    l.tool_errors,
    l.compactions,
    round(l.cost_usd, 4) AS cost_usd,
    l.unpriced_api_calls
FROM selected s
JOIN pool l ON l.session_id = s.pick.session_id
ORDER BY s.pick.n;
