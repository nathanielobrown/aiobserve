-- The compactions one thread ran, as markers the timeline interleaves by time.
-- Keyed by source as well as session: `main` for a session page, a run id for a run page.
SELECT
    k.id AS compaction_id,
    k.timestamp,
    -- The turn it happened during, NULL where it happened between two of them — which is
    -- what decides whether the NavTree hangs it under a turn or beside one. Half-open, so a
    -- compaction at the instant a turn starts is that turn's and one at the instant it ends
    -- is the next thing's. Turn spans overlap, so a compaction can sit in two — 44 of the
    -- canonical store's 1,269 do. The turn that started last wins, because that is the one
    -- still running when the context was dropped.
    -- `max_by` rather than an ordered `LIMIT 1`, because every limit in a viewer query is a
    -- page size a caller can bind (`tests/view/test_bounds.py`) and this one is neither.
    (
        SELECT max_by(t.id, (t.started_at, t."index"))
        FROM live_turns t
        WHERE t.session_id = k.session_id
          AND t.source = k.source
          AND k.timestamp >= t.started_at
          AND k.timestamp < t.ended_at
    ) AS turn_id,
    -- Cut like a chip's columns are: a marker is a row of a page whose size is arithmetic.
    substr(k.trigger, 1, $chip_chars) AS trigger,
    k.pre_tokens,
    k.post_tokens,
    k.duration_ms,
    -- The one bar read backwards: the fill is where the window stood before the boundary and
    -- what it "added" is what it gave back, so the row draws the freed span between the two.
    -- A compaction records no model, so the window comes off the thread — the nearest call of
    -- the same source at or before it, else the first after it, through the macro the api call
    -- and turn rows already draw against. A thread with no answered call, or a model our price
    -- table lacks, leaves it NULL, which is a bar the NavTree does not draw.
    {
        'fill': k.pre_tokens,
        'added': k.pre_tokens - k.post_tokens,
        'window': context_window((
            SELECT coalesce(
                max_by(c.model, c.started_at) FILTER (c.started_at <= k.timestamp),
                min_by(c.model, c.started_at) FILTER (c.started_at > k.timestamp))
            FROM live_api_calls c
            WHERE c.session_id = k.session_id AND c.source = k.source AND NOT c.synthetic))
    } AS context
FROM live_compactions k
WHERE k.session_id = $session_id AND k.source = $source
ORDER BY k.timestamp;
