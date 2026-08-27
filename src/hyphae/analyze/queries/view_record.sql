-- One whole raw transcript record, by the line number a citation names. The untruncated `raw`
-- is the point: this is the route from a preview to the text the session actually held, and
-- the bound is one record rather than a page of them.
SELECT
    line_no,
    uuid,
    type,
    timestamp,
    length(raw) AS raw_chars,
    raw
FROM raw_records
WHERE session_id = $session_id
  AND source = $source
  AND line_no = $line_no;
