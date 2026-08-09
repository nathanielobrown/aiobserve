-- What a thread pays to rebuild a context it already had. A mid-thread api call that writes
-- its whole prompt to the cache and reads nothing back is the thread starting over: the
-- prefix cache missed, and every token of the conversation is billed at the write price
-- again. Iteration 2's A11 saw this where a coordinator sent a follow-up into an open agent
-- run — the run had gone idle long enough for the cache to expire — and had no way to count
-- it (`reports/2026_08_09_mycelia_agent_friction.md`).
-- A reload is a call that is not its thread's first, writes at least `$min_rebuilt_tokens`,
-- and writes at least `$min_rebuilt_pct` of the context it sent. The share is the detector:
-- a growing thread writes the turn's new tokens and reads the rest, so 90% written is a
-- thread that kept almost nothing. The token floor only keeps trivia out — moving it from
-- 10,000 to 20,000 changes the corpus count by 7 events in 935, while moving the share from
-- 90% to 80% adds 312 (measured on mycelia, `as_of=2026-08-09`). Both are bound because
-- neither is a fact about Claude Code: they are where we cut a continuum.
-- The count is slightly generous and slightly short, in ways worth knowing before quoting it:
-- the share's denominator counts only cached tokens, so a call that sent a lot of uncached
-- input still reads as fully rebuilt (27 corpus reloads send more than 10% uncached); and
-- `corpus_api_calls` drops replayed rows, so 21 resumed threads open on a call that is not
-- their true first and lose the cold rebuild a resume always pays (at most 2%).
-- Two columns bound the readings a reload is *not* evidence for:
--   `idle_reloads` — the reload followed a gap of `$idle_seconds` or more, which is A11's
--     shape. Read it as an upper bound on cache expiry, not a certainty. The gap runs
--     start-to-start, because `started_at` is when the request went out (the timestamp of the
--     record it answers) and request-to-request is the interval a cache entry ages over; but
--     it also spans the previous call's own generation, and measuring end-to-start instead
--     moves the corpus figure 627 → 576. Separately, 102 corpus idle reloads follow a call
--     that wrote 1-hour cache entries, and 21 of those came back inside the hour, so a live
--     entry was there to hit. The rest missed with the thread still working, for a reason the
--     transcript does not record
--   `compaction_reloads` — a compaction boundary landed between the previous call and this
--     one, so this is the call that reloads the window from the summary and rebuilds by
--     design. Compaction is `agent_compactions.sql`'s subject, not this one. The flag is
--     anchored on the call *after* the boundary because that is the one that pays: 1,014 of
--     1,116 boundary timestamps equal the following call's `started_at`. Flagging the call at
--     or after the boundary instead marks the summarizing call, which reads its context back
--     rather than rebuilding it, and leaves the rebuild unexplained — that anchoring found 24
--     of 928 corpus reloads where this one finds 40 (19 of 648 in the window)
-- Neither column filters, so a reading that needs only A11's shape can subtract them itself.
-- Three grains in one result, told apart by `grain`: the corpus total, one row per
-- `agent_type`, and one row per affected thread — the row a report cites when it names a run.
-- `reload_cost_usd` has two honest denominators and they differ by a factor of two: name
-- whichever you quote. `thread_cost_usd` is what the threads that reloaded cost in full ($607
-- of $5,886, 10.3%, in the 2026-08-09 window); the window's total spend across every session
-- is a different, larger number this query does not carry (`session_shapes.sql`; $11,110 for
-- the same window, making the same $607 5.5%).
WITH call AS (
    -- Per-call windows are computed before `session_period` fans a session out into its two
    -- rows. Joining the periods first duplicates every call, and a partition that then
    -- ignored `period` would compare a call against its own copy — a real bug this query had,
    -- worth 242 idle reloads. `project_sessions` carries each session once, so scoping here
    -- costs nothing and the fan-out below cannot reach the ordering.
    SELECT
        c.session_id,
        c.source,
        c.started_at,
        c.cache_read_tokens,
        c.cache_creation_tokens,
        c.cost_usd,
        min(c."index") OVER (PARTITION BY c.session_id, c.source) AS first_index,
        lag(c.started_at) OVER (
            PARTITION BY c.session_id, c.source ORDER BY c."index"
        ) AS previous_start,
        c."index" AS call_index
    FROM project_sessions s
    JOIN corpus_api_calls c USING (session_id)
), reload AS (
    SELECT
        session_id,
        source,
        cache_creation_tokens,
        cost_usd,
        date_diff('second', previous_start, started_at) >= $idle_seconds AS idle,
        EXISTS (
            SELECT 1 FROM corpus_compactions k
            WHERE k.session_id = call.session_id AND k.source = call.source
              -- The boundary record is written when the reply to the summary goes out, so it
              -- lands at the start of the call that reads the summary back. The second of
              -- slack is clock skew between two records: the two boundaries checked by hand
              -- sat 0 ms and 5 ms *after* that call's start. Sliding the same second onto
              -- both ends keeps the windows disjoint, so no boundary is claimed twice.
              AND k.timestamp > call.previous_start + INTERVAL 1 SECOND
              AND k.timestamp <= call.started_at + INTERVAL 1 SECOND
        ) AS compacting
    FROM call
    WHERE call_index > first_index
      AND cache_creation_tokens >= $min_rebuilt_tokens
      AND cache_creation_tokens * 100
          >= $min_rebuilt_pct * (cache_creation_tokens + cache_read_tokens)
), thread_total AS (
    SELECT session_id, source, sum(cost_usd) AS cost_usd,
        count(*) FILTER (cost_usd IS NULL) AS unpriced_api_calls
    FROM call GROUP BY session_id, source
), thread AS (
    -- One row per affected thread per period, so the grains above it sum threads rather than
    -- events: a thread's own cost would otherwise be counted once per reload it holds.
    SELECT
        p.period,
        -- A main thread has no `agent_runs` row; it rides in named, the way it does in
        -- `agent_compactions.sql`, so a definition's tax has the main thread's beside it.
        coalesce(a.agent_type, '(main thread)') AS agent_type,
        r.session_id,
        r.source,
        count(*) AS reloads,
        count(*) FILTER (r.idle) AS idle_reloads,
        count(*) FILTER (r.compacting) AS compaction_reloads,
        sum(r.cache_creation_tokens) AS rebuilt_tokens,
        sum(r.cost_usd) AS reload_cost_usd,
        any_value(t.cost_usd) AS thread_cost_usd,
        any_value(t.unpriced_api_calls) AS unpriced_api_calls
    FROM reload r
    JOIN session_period p USING (session_id)
    JOIN thread_total t USING (session_id, source)
    LEFT JOIN corpus_agent_runs a ON a.session_id = r.session_id AND a.id = r.source
    GROUP BY p.period, agent_type, r.session_id, r.source
)
SELECT
    period,
    CASE
        WHEN agent_type IS NULL THEN 'corpus'
        WHEN session_id IS NULL THEN 'agent_type'
        ELSE 'thread'
    END AS grain,
    coalesce(agent_type, '(all)') AS agent_type,
    coalesce(session_id, '(all)') AS session_id,
    coalesce(source, '(all)') AS source,
    count(*) AS threads,
    count(DISTINCT session_id) AS sessions,
    sum(reloads) AS reloads,
    sum(idle_reloads) AS idle_reloads,
    sum(compaction_reloads) AS compaction_reloads,
    sum(rebuilt_tokens) AS rebuilt_tokens,
    round(sum(reload_cost_usd), 2) AS reload_cost_usd,
    -- What the same threads cost in full, so a rebuilt total can be read as a share of the
    -- spend it taxed. `unpriced_api_calls` counts every call of those threads our price
    -- table lacks, reloads included — the denominator is what has to be sound.
    round(sum(thread_cost_usd), 2) AS thread_cost_usd,
    sum(unpriced_api_calls) AS unpriced_api_calls
FROM thread
GROUP BY GROUPING SETS (
    (period),
    (period, agent_type),
    (period, agent_type, session_id, source)
)
ORDER BY
    period,
    CASE WHEN agent_type IS NULL THEN 0 WHEN session_id IS NULL THEN 1 ELSE 2 END,
    rebuilt_tokens DESC,
    agent_type,
    session_id,
    source;
