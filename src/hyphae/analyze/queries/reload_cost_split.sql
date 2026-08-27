-- What a rebuild bill splits into at one gap length: the tokens threads rebuilt after short
-- silences, against the tokens they rebuilt after long ones, per kind of thread.
-- `idle_gaps` lists the silences and `context_reloads` counts the reloads; neither prices a
-- recommendation scoped to gap length, because a count share is not a token share. Short
-- waits can be most of the events and a minority of the bill, and the difference is what a
-- keep-warm heartbeat scoped to sub-hour gaps would be buying — iteration 4's F3 had to leave
-- that upside unquantified for want of this split
-- (`reports/2026_08_14_mycelia_iteration_4.md`).
-- `$short_gap_seconds` has no default: the bound is the claim. A break-even moves with the
-- pricing table and a cache lifetime is a configuration, so a split at some bound nobody
-- named is a number a report would quote as if the query had chosen it.
-- A gap is short when it ran *strictly* under the bound, which is the complement of the floor
-- `$min_idle_seconds` applies — every silence is on exactly one side of the split.
-- Tokens, not dollars, for `idle_gaps`'s reason: a reload call's cost mixes the rebuild with
-- the answer it then generated, and `context_reloads` is where that total is reported with
-- the unpriced calls under it.
-- The population is every silence over the floor, not only the ones that ended in a rebuild.
-- A heartbeat fires over the waits that would have cost nothing too, so `gaps` and
-- `short_gaps` are what its own price is counted against; `reloads` and `rebuilt_tokens` are
-- what it would have saved.
-- `reloaded` is `context_reloads`'s detector through the shared `rebuilt_context` macro,
-- minus its compaction column — the same upper bound `idle_gaps` reports, and for the same
-- reason: a compaction boundary inside the gap is a rebuild by design that no heartbeat
-- would have saved.
WITH gap AS (
    -- The windows are computed before `session_period` fans an in-window session out into
    -- two rows. Joining the periods first duplicates every call, and a `lag` over the copies
    -- compares a call against itself — a real bug `context_reloads.sql` had, worth 242 idle
    -- reloads. `project_sessions` carries each session once.
    SELECT
        c.session_id,
        c.source,
        date_diff('second', lag(c.started_at) OVER thread, c.started_at) AS idle_seconds,
        rebuilt_context(
            c.cache_creation_tokens, c.cache_read_tokens, $min_rebuilt_tokens, $min_rebuilt_pct
        ) AS reloaded,
        c.cache_creation_tokens AS rebuilt_tokens
    FROM project_sessions s
    JOIN corpus_api_calls c USING (session_id)
    WINDOW thread AS (PARTITION BY c.session_id, c.source ORDER BY c."index")
    -- A thread's first call follows no silence, and `lag` gives it a NULL the floor drops.
    QUALIFY idle_seconds >= $min_idle_seconds
), banded AS (
    SELECT
        p.period,
        -- A main thread has no `agent_runs` row and rides in named, as in `idle_gaps.sql`.
        coalesce(a.agent_type, '(main thread)') AS agent_type,
        g.idle_seconds < $short_gap_seconds AS short_gap,
        g.reloaded,
        -- Named apart from the total below, so the shares can read the aggregates by name.
        g.rebuilt_tokens AS tokens
    FROM gap g
    JOIN session_period p USING (session_id)
    LEFT JOIN corpus_agent_runs a ON a.session_id = g.session_id AND a.id = g.source
)
SELECT
    period,
    CASE WHEN agent_type IS NULL THEN 'corpus' ELSE 'agent_type' END AS grain,
    coalesce(agent_type, '(all)') AS agent_type,
    count(*) AS gaps,
    count(*) FILTER (short_gap) AS short_gaps,
    count(*) FILTER (reloaded) AS reloads,
    count(*) FILTER (reloaded AND short_gap) AS short_reloads,
    -- Zero rather than NULL where nothing rebuilt, so a share below reads as the 0% it is
    -- and a NULL share means the one thing it should: no reload to take a share of.
    coalesce(sum(tokens) FILTER (reloaded), 0) AS rebuilt_tokens,
    coalesce(sum(tokens) FILTER (reloaded AND short_gap), 0) AS short_rebuilt_tokens,
    -- The two shares a scoped recommendation is argued from. They are different numbers
    -- whenever short reloads rebuild less than long ones do, which is the reading the count
    -- share alone gets wrong.
    round(100.0 * short_reloads / nullif(reloads, 0), 1) AS short_reload_pct,
    round(100.0 * short_rebuilt_tokens / nullif(rebuilt_tokens, 0), 1) AS short_token_pct
FROM banded
GROUP BY GROUPING SETS ((period), (period, agent_type))
ORDER BY
    period,
    CASE WHEN agent_type IS NULL THEN 0 ELSE 1 END,
    rebuilt_tokens DESC,
    agent_type;
