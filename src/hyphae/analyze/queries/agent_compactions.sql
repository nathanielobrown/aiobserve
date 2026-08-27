-- Which kinds of thread run out of context. A compaction is the agent losing the top of its
-- window, so a definition that compacts every other run is a definition whose work does not
-- fit the shape it was given — and that is a fact about the agent library, not about a
-- session that happened to go long.
-- The population is every thread in the period: one main thread per session, plus one per
-- agent run. The main thread rides in as its own row, `(main thread)`, so a definition's
-- rate has the thing it has to beat sitting beside it. It carries no floor on thread count
-- for the same reason — a row dropped for rarity takes its compactions with it, and the
-- column would no longer sum to what the period holds.
WITH thread AS (
    SELECT p.period, p.session_id, '(main thread)' AS agent_type, 'main' AS source
    FROM session_period p
    UNION ALL
    SELECT p.period, p.session_id, a.agent_type, a.id AS source
    FROM session_period p
    JOIN corpus_agent_runs a USING (session_id)
),
compacted AS (
    SELECT
        t.period,
        t.agent_type,
        t.session_id,
        (SELECT count(*) FROM corpus_compactions k
            WHERE k.session_id = t.session_id AND k.source = t.source) AS compactions
    FROM thread t
)
SELECT
    period,
    agent_type,
    count(*) AS threads,
    -- Threads beside compactions because the two answer different questions: whether this
    -- definition usually compacts, and whether the ones that do compact again and again.
    count(*) FILTER (compactions > 0) AS compacting_threads,
    sum(compactions) AS compactions,
    round(avg(compactions), 2) AS compactions_per_thread,
    count(DISTINCT session_id) AS sessions
FROM compacted
GROUP BY period, agent_type
-- Ordered by the total, not the rate: `agent_type` is an open set, and a name one session
-- invented for one run takes the top of a rate ranking with a denominator of 1.
ORDER BY period, compactions DESC, threads DESC, agent_type;
