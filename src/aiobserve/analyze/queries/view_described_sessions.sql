-- What an enrichment pass said each session was: the row the list shows beside its title.
-- `view_enrichment` is the same question asked of one session at every level. This one asks
-- the session level of the whole store, because the list renders a page of sessions at a time
-- and the viewer joins this to the page it just read (`view/listing.py`) rather than running
-- a query per row.
-- Cut to a row's head rather than a page's: a list row is multiplied by the size of the page,
-- and the whole description is on the session's own page a click away. Friction stays there
-- too — a line about how a session struggled is a sentence, not a column.
-- A store no pass has touched holds no `session_enrichments` at all, which is why the viewer
-- asks the catalog before it composes this in (`view/enrichment.py`).
SELECT
    e.session_id,
    substr(e.description, 1, $head_chars) AS description,
    substr(e.category, 1, $tag_chars) AS category,
    substr(e.outcome, 1, $tag_chars) AS outcome
FROM session_enrichments e
ORDER BY e.session_id;
