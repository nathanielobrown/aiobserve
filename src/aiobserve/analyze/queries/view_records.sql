-- One page of the raw transcript records of one `(session_id, source)`: the browser a report's
-- citation lands in. Keyset on `line_no`, unique and ascending within a thread; `$after` is
-- the last line already shown, so a citation of line N opens the page at `after = N - 1`.
-- Session data is private (`CLAUDE.md`) — a row carries `$preview_chars` of the record and its
-- true length, and the whole record is fetched one at a time (`view_record`).
SELECT
    line_no,
    uuid,
    type,
    timestamp,
    length(raw) AS raw_chars,
    substr(raw, 1, $preview_chars) AS raw_head,
    -- How many records the cursor still has ahead of it, counted before the LIMIT bites, so
    -- the page can say what it cut rather than looking like the end of a thread.
    count(*) OVER () AS matched_records
FROM raw_records
WHERE session_id = $session_id
  AND source = $source
  AND line_no > $after
ORDER BY line_no
LIMIT $page_records;
