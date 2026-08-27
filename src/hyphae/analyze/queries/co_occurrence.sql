-- Which tools, skills, and agent types turn up in the same session as each other. The point
-- is to find the clusters a reader would otherwise have to notice by hand — a skill that
-- always runs beside one agent type, a tool pair that marks a particular kind of work.
-- Counted once per session per item: a pair is about sessions that used both, not about how
-- many times either fired.
-- `conditional_rate` is the pair count over the rarer item's own count — how often the rarer
-- one brings the other along. It is not evidence of a mechanism; two things co-occurring in
-- ten sessions is ten sessions' worth of a question to go read.
WITH incidence AS (
    SELECT p.period, 'tool' AS kind, t.name AS item, t.session_id
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    GROUP BY 1, 2, 3, 4
    UNION ALL
    SELECT p.period, 'skill', c.attribution_skill, c.session_id
    FROM session_period p
    JOIN corpus_api_calls c USING (session_id)
    WHERE c.attribution_skill IS NOT NULL
    GROUP BY 1, 2, 3, 4
    UNION ALL
    SELECT p.period, 'agent_type', a.agent_type, a.session_id
    FROM session_period p
    JOIN corpus_agent_runs a USING (session_id)
    GROUP BY 1, 2, 3, 4
), solo AS (
    SELECT period, kind, item, count(*) AS sessions FROM incidence GROUP BY 1, 2, 3
), pair AS (
    -- `item_b > item_a` names each pair once and drops the self-pair.
    SELECT x.period, x.kind, x.item AS item_a, y.item AS item_b, count(*) AS sessions
    FROM incidence x
    JOIN incidence y
        ON y.period = x.period AND y.kind = x.kind AND y.session_id = x.session_id
        AND y.item > x.item
    GROUP BY 1, 2, 3, 4
)
SELECT
    p.period,
    p.kind,
    p.item_a,
    p.item_b,
    p.sessions,
    a.sessions AS sessions_a,
    b.sessions AS sessions_b,
    round(p.sessions / least(a.sessions, b.sessions), 4) AS conditional_rate
FROM pair p
JOIN solo a ON a.period = p.period AND a.kind = p.kind AND a.item = p.item_a
JOIN solo b ON b.period = p.period AND b.kind = p.kind AND b.item = p.item_b
-- A pair seen once is noise. The floor is bound so a report can say what it filtered.
WHERE p.sessions >= $min_sessions
ORDER BY p.period, p.kind, p.sessions DESC, conditional_rate DESC, p.item_a, p.item_b;
