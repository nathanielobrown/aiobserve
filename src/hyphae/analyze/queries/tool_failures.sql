-- Which tools come back an error, and how often. The rate is the point: a tool called twice
-- and failed twice is a different problem from one called four hundred times and failed
-- twice, and only the pair of counts tells them apart.
-- `sessions` is how many sessions the tool erred in, which bounds a claim: an error rate
-- carried by one session is one session's evidence however many calls it holds.
SELECT
    p.period,
    t.name AS tool,
    count(*) AS calls,
    count(*) FILTER (t.is_error) AS errors,
    round(count(*) FILTER (t.is_error) / count(*), 4) AS error_rate,
    count(DISTINCT t.session_id) AS sessions,
    count(DISTINCT t.session_id) FILTER (t.is_error) AS erring_sessions
FROM session_period p
JOIN corpus_tool_calls t USING (session_id)
GROUP BY p.period, t.name
ORDER BY p.period, errors DESC, calls DESC, tool;
