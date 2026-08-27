-- The raw transcript records of one `(session_id, source)`, over a line range the caller has
-- to name. This is the only route from a timeline back to the text a session actually held, so
-- both bounds are deliberate: the range has no default, and each record comes back cut to
-- `$max_chars`. Session data is private (`CLAUDE.md`) — pull the least of it that answers the
-- question, and cite the lines.
SELECT
    line_no,
    type,
    timestamp,
    length(raw) AS raw_chars,
    substr(raw, 1, $max_chars) AS raw
FROM raw_records
WHERE session_id = $session_id
  AND source = $source
  AND line_no BETWEEN $first_line AND $last_line
ORDER BY line_no;
