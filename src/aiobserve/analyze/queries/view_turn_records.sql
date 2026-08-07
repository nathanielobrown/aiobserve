-- Which transcript line each of one thread's turns was read from. `turns.id` is the `uuid` of
-- the record the extractor built the turn out of, in the same `(session_id, source)` — so the
-- link from a turn on a page down to the text behind it is the store's own join rather than a
-- guess about ordering. A turn whose record is not archived simply has no row here.
-- Bounded by the thread's turn count, and every column is an identifier or a number.
SELECT
    t.id AS turn_id,
    r.line_no
FROM live_turns t
JOIN raw_records r
  ON r.session_id = t.session_id
 AND r.source = t.source
 AND r.uuid = t.id
WHERE t.session_id = $session_id
  AND t.source = $source;
