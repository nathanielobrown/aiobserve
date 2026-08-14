-- How long a thread went quiet, one row per silence, and whether the call that broke it paid
-- to rebuild the context. `context_reloads` answers the same question as a boolean — the gap
-- cleared `$idle_seconds` or it did not — which is enough to say a reload is explainable and
-- not enough to price anything. Sizing a keep-warm heartbeat needs the lengths themselves:
-- the cost of holding a cache alive scales with the wait, so the population that would repay
-- it is the gaps under a break-even, and that break-even moves with the pricing table
-- (`handoffs/pricing-f3-keepwarm.md` had to leave it unsized for want of this).
-- The two queries meet at one number: the rows here with `reloaded` true are exactly the
-- `idle_reloads` `context_reloads` counts — 627 corpus, 449 in the 2026-08-13 window. What
-- the lengths add is the shape of them. 371 of those 449 waits ran under an hour, against a
-- record that until now held gap lengths for one thread, all of them over an hour.
-- A gap is the wait between two consecutive api calls of one thread, start to start, which is
-- the interval a cache entry ages over — the same measure `context_reloads` flags on, and it
-- includes the previous call's own generation time.
-- `reloaded` is `context_reloads`'s detector on the call that broke the silence, minus its
-- compaction column: a compaction boundary inside the gap is a rebuild by design, and no
-- heartbeat would have saved it. Read `reloaded` as an upper bound for that reason — 40 of
-- the 928 corpus reloads and 16 of the 605 in the 2026-08-13 window sat on a boundary, and
-- `context_reloads` is where that split is counted.
-- `cached_1h` says the call before the silence wrote 1-hour cache entries rather than the
-- 5-minute default, so a reader thresholding on `idle_seconds` knows which TTL each gap was
-- racing. It does not promise the entry was still there: 21 corpus reloads came back inside
-- the hour they had paid for and rebuilt anyway.
-- Rows carry `in_window` rather than the usual period fan-out. Every other corpus query
-- reports one count twice; a detail table that did the same would hand a reader two rows per
-- gap to sum.
SELECT
    s.in_window,
    c.session_id,
    c.source,
    -- A main thread has no `agent_runs` row and rides in named, as in `context_reloads.sql`.
    coalesce(a.agent_type, '(main thread)') AS agent_type,
    -- When the silence began: the last call before it went out at this time.
    lag(c.started_at) OVER thread AS gap_start,
    date_diff('second', lag(c.started_at) OVER thread, c.started_at) AS idle_seconds,
    rebuilt_context(
        c.cache_creation_tokens, c.cache_read_tokens, $min_rebuilt_tokens, $min_rebuilt_pct
    ) AS reloaded,
    -- What the call that broke the silence wrote, so the gaps a heartbeat would have covered
    -- carry the size of what it would have saved. Tokens rather than dollars: a reload call's
    -- cost mixes the rebuild with the answer it then generated, and `context_reloads` is where
    -- that total is reported with the unpriced calls under it.
    c.cache_creation_tokens AS rebuilt_tokens,
    coalesce(lag(c.cache_1h_tokens) OVER thread, 0) > 0 AS cached_1h
FROM project_sessions s
JOIN corpus_api_calls c USING (session_id)
LEFT JOIN corpus_agent_runs a ON a.session_id = c.session_id AND a.id = c.source
WINDOW thread AS (PARTITION BY c.session_id, c.source ORDER BY c."index")
-- A thread's first call follows no silence, and `lag` gives it a NULL gap that the floor
-- drops. `corpus_api_calls` hides replayed rows, so a resumed thread opens on a call that is
-- not its true first and loses the wait before it.
QUALIFY idle_seconds >= $min_idle_seconds
ORDER BY idle_seconds DESC, session_id, source;
