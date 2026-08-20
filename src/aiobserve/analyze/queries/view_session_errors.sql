-- Every failed tool call of one session, whichever thread it ran on, in the order they
-- happened: the list behind the viewer's errors page and the stepper beside a pane reading a
-- failure (`view/errors.py`).
-- Session-wide rather than per thread, for the reason the unattached bucket is: what a
-- subagent failed at is what the session failed at, and the tree opens one path — so nothing
-- else on a node page reaches a failure five spawns down without reading everything first.
-- No join up to the api call or the turn: each row leads to the tool call's own page, which
-- carries the crumb chain that places it. Thin like `view_tree_tools`, and cut to the same
-- width, because both label a node rather than describe one.
-- The order is total — `(source, "index")` and `(source, id)` are each unique within a
-- session (`export/duckdb.py`) — which is what a cut means anything against: a page showing
-- the first `$errors` of a partial order would show different rows on two reads of one store.
SELECT
    t.source,
    t.id AS tool_call_id,
    -- What the tool was called and the head of what it was asked, which is what tells two
    -- failures of one tool apart in the width of a row.
    substr(t.name, 1, $nav_chars + 1) AS name,
    substr(t.input, 1, $nav_chars + 1) AS input_head,
    -- Constant true under this filter, and selected anyway: a tool node carries the flag
    -- wherever it is built from, so every query behind one answers the same columns.
    t.is_error,
    t.started_at,
    -- How many the session failed in all, counted before the LIMIT bites, so a page that
    -- showed the first `$errors` can say how many it left rather than reading as the whole.
    count(*) OVER () AS matched_errors
FROM live_tool_calls t
WHERE t.session_id = $session_id
  AND t.is_error
ORDER BY t.started_at, t.source, t."index", t.id
LIMIT $errors;
