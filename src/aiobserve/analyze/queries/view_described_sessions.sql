-- What an enrichment pass said each session was: the row the list shows beside its title, and
-- the kinds of work its turns were.
-- `view_enrichment` is the same question asked of one session at every level. This one asks
-- the session level of the whole store, because the list renders a page of sessions at a time
-- and the viewer joins this to the page it just read (`view/listing.py`) rather than running
-- a query per row.
-- Cut to a row's head rather than a page's: a list row is multiplied by the size of the page,
-- and the whole description is on the session's own page a click away. Friction stays there
-- too — a line about how a session struggled is a sentence, not a column. The work list is
-- cut here rather than in the composition, like the agent types beside it: nothing filters on
-- it, so the file can bound it and stay the citable core.
-- A store no pass has touched holds no `session_enrichments` at all, which is why the viewer
-- asks the catalog before it composes this in (`view/enrichment.py`).
WITH work_kinds AS (
    -- Every turn a pass reached, whichever thread it ran on: what a session spent its time on
    -- is not a property of its main thread alone.
    SELECT session_id, list({'name': name, 'turns': turns} ORDER BY turns DESC, name) AS kinds
    FROM (
        SELECT session_id, substr(category, 1, $kind_chars) AS name, count(*) AS turns
        FROM turn_enrichments GROUP BY 1, 2
    ) GROUP BY session_id
)
SELECT
    d.session_id,
    substr(e.description, 1, $head_chars) AS description,
    substr(e.category, 1, $tag_chars) AS category,
    substr(e.outcome, 1, $tag_chars) AS outcome,
    list_slice(coalesce(w.kinds, []), 1, $head_kinds) AS work,
    greatest(len(coalesce(w.kinds, [])) - $head_kinds, 0) AS work_cut
-- Either half describes a session on its own: a pass that reached a session's turns but not
-- the session itself still has work to show, and one that reached the session but not its
-- turns still has a description.
FROM (
    SELECT session_id FROM session_enrichments
    UNION
    SELECT session_id FROM turn_enrichments
) d
LEFT JOIN session_enrichments e ON e.session_id = d.session_id
LEFT JOIN work_kinds w ON w.session_id = d.session_id
ORDER BY d.session_id;
