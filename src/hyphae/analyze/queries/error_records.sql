-- Every tool call one session got an error back from, and the line to read each one at.
-- This is the index into a session's failures: without it a reader scans raw records a
-- thousand at a time hunting for `is_error`, and often never finds them.
-- `source` is left unbound by default because a session's errors mostly happen inside its
-- runs, and a reader who has to name the thread first is exactly the reader who cannot.
-- `line_no` is the last record of that thread naming the call — the one that carries its
-- result, for a server-side tool as well as a client one. NULL means no raw record names it,
-- so there is nothing to slice: the transcript never held the result.
-- The text comes back whitespace-collapsed and cut to `$max_chars`, with the full length
-- beside it. Session data is private (`CLAUDE.md`) — this is a signature, not the error.
SELECT
    t.source,
    (
        SELECT max(r.line_no) FROM raw_records r
        WHERE r.session_id = t.session_id AND r.source = t.source AND contains(r.raw, t.id)
    ) AS line_no,
    t.name AS tool,
    t.id AS tool_call_id,
    t.started_at,
    length(t.result) AS error_chars,
    substr(regexp_replace(trim(t.result), '\s+', ' ', 'g'), 1, $max_chars) AS error
FROM live_tool_calls t
WHERE t.session_id = $session_id
  AND t.is_error
  AND ($source::VARCHAR IS NULL OR t.source = $source)
ORDER BY line_no NULLS LAST, t.started_at, t.id;
