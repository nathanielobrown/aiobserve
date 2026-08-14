-- What a thread did in the calls right after a file tool failed on the path it named. The
-- pattern iteration 3 kept seeing and could not count: a read of a guessed filename 404s, and
-- the thread lists the directory it should have listed first. `path_failures` says which
-- directory the failure was in; this says whether the thread recovered by looking.
-- Every failure lands in exactly one of three dispositions, so the population is the whole
-- column and not the interesting part of it:
--   `listed the directory` — a listing call within `$within_calls` naming the failed path's
--                            own directory. The recovery, and the guess it recovers from
--   `listed elsewhere`     — a listing call, but not of that directory: a broader search, or
--                            a thread that had already moved on
--   `no listing`           — nothing was listed. The thread guessed again, gave up, or the
--                            failure was never about a name it could have looked up
-- A listing is `Glob`, `LS`, or a shell command opening with `ls`, `find`, `tree` or
-- `rg --files` — discovered from the corpus, so a tool nobody has used yet reads as no
-- listing. `$within_calls` is 1 because the claim is about the *next* call: widen it to ask
-- how long a thread takes to look, at the cost of catching listings that answer something
-- else. Over mycelia's 2026-08-13 window, widening it to 3 moved 33 failures out of
-- `no listing`, 11 of them into `listed the directory`.
-- Bind `$missing` to a phrase in the error text — "does not exist" — to narrow the population
-- to the failures a listing could have prevented. Left NULL it holds every failed call that
-- named a path, permission errors and unread-file guardrails included.
WITH failed AS (
    SELECT
        p.period,
        t.session_id,
        t.source,
        t."index" AS idx,
        -- The directory the call was pointed at, empty for a bare filename.
        regexp_extract(
            json_extract_string(t.input, '$.file_path'), '^(.*)/[^/]*$', 1
        ) AS directory
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    WHERE t.is_error
      AND json_valid(t.input)
      AND json_extract_string(t.input, '$.file_path') IS NOT NULL
      AND ($missing::VARCHAR IS NULL OR contains(t.result, $missing))
), nearby AS (
    -- Every call of the same thread inside the window, and whether it looked at a directory.
    -- A LEFT JOIN, so a failure nobody followed keeps its row: the three dispositions have to
    -- cover the failures, and one that ended its thread is the commonest way to not look.
    SELECT
        f.period,
        f.session_id,
        f.source,
        f.idx,
        f.directory,
        n.name IN ('Glob', 'LS')
            OR regexp_matches(
                CASE WHEN json_valid(n.input)
                    THEN coalesce(json_extract_string(n.input, '$.command'), '') ELSE '' END,
                '(^|[;&|]\s*)(ls|find|tree|rg --files)\b'
            ) AS listing,
        n.input AS looked_at
    FROM failed f
    LEFT JOIN corpus_tool_calls n
        ON n.session_id = f.session_id
       AND n.source = f.source
       AND n."index" > f.idx
       AND n."index" <= f.idx + $within_calls
), judged AS (
    SELECT
        period,
        session_id,
        source,
        CASE
            -- The failed path's directory has to appear in what the listing asked for. A bare
            -- filename has no directory to match, so its recovery is never the exact one.
            WHEN bool_or(listing AND directory <> '' AND contains(looked_at, directory)) THEN 1
            WHEN bool_or(listing) THEN 2
            ELSE 3
        END AS rank
    FROM nearby
    GROUP BY period, session_id, source, idx
), counted AS (
    SELECT
        period,
        rank,
        count(*) AS failures,
        -- How much of the corpus the count is evidence about, as the other counts report it.
        count(DISTINCT session_id) AS sessions,
        count(DISTINCT session_id || ':' || source) AS threads
    FROM judged
    GROUP BY period, rank
), disposition AS (
    -- The three names, written once and joined to the counts, so a disposition nothing fell
    -- into is a zero rather than a missing row: "no thread ever looked" is a finding, and a
    -- reader must not have to tell it from a query that stopped working.
    SELECT * FROM (VALUES
        (1, 'listed the directory'), (2, 'listed elsewhere'), (3, 'no listing')
    ) AS named(rank, recovery)
)
SELECT
    p.period,
    d.recovery,
    coalesce(c.failures, 0) AS failures,
    coalesce(c.sessions, 0) AS sessions,
    coalesce(c.threads, 0) AS threads
FROM (SELECT DISTINCT period FROM session_period) p
CROSS JOIN disposition d
LEFT JOIN counted c ON c.period = p.period AND c.rank = d.rank
ORDER BY p.period, d.rank;
