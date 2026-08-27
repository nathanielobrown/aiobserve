-- Which directory a failing file tool was pointed at. `error_signatures` cannot answer it:
-- the path is the volatile part of an error text, so the signature stands it as `<path>` and
-- "File does not exist" arrives as one group whatever it was looking for. This groups the
-- same failures by the directory in the call's *input* instead, beside that directory's calls
-- that succeeded — a directory read a thousand times fails differently from one read twice.
-- The key is the last `$tail_segments` segments of the path's directory, which is what makes
-- the count comparable across checkouts: a worktree, a sandbox copy and the primary checkout
-- give one `handoffs` rather than three roots. Bind it higher to tell same-named directories
-- apart, at the cost of splitting one directory across the copies of the repository it sits in.
-- `run_errors` is the share of the failures that a spawned agent hit rather than the main
-- thread: a directory a run cannot see but the thread that spawned it can looks like this.
-- Unlike `error_signatures`, a row of this table names a directory. The report's redaction
-- rule applies to it — a path outside any checkout of the project can key a row by whatever
-- directory it sat in.
WITH targeted AS (
    SELECT
        p.period,
        t.is_error,
        t.session_id,
        t.source,
        -- Any tool whose input carries a file path, discovered rather than listed: `Read`,
        -- `Edit` and `Write` are today's, and a tool that takes `path` or a glob is invisible
        -- here, so this counts named files rather than every filesystem reach.
        json_extract_string(t.input, '$.file_path') AS path
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    WHERE json_valid(t.input)
      AND json_extract_string(t.input, '$.file_path') IS NOT NULL
), placed AS (
    SELECT
        period,
        is_error,
        session_id,
        source,
        array_to_string(
            list_slice(
                str_split(regexp_extract(path, '^(.*)/[^/]*$', 1), '/'), -$tail_segments, -1
            ),
            '/'
        ) AS tail
    FROM targeted
)
SELECT
    period,
    -- A bare filename with no directory in it — a relative read of the working directory.
    CASE WHEN tail = '' THEN '(no directory)' ELSE tail END AS directory,
    count(*) AS calls,
    count(*) FILTER (is_error) AS errors,
    -- How much of the corpus the error count is evidence about, as `error_signatures` reports
    -- it: the same 40 failures in one thread and across thirty are different claims.
    count(DISTINCT session_id) FILTER (is_error) AS sessions,
    count(DISTINCT session_id || ':' || source) FILTER (is_error) AS threads,
    count(*) FILTER (is_error AND source <> 'main') AS run_errors
FROM placed
GROUP BY 1, 2
HAVING count(*) FILTER (is_error) >= $min_occurrences
ORDER BY period, errors DESC, directory;
