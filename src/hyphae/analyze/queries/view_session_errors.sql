-- Every failed tool call of one session, whichever thread it ran on, in the order they
-- happened: the list behind the viewer's errors page and the stepper beside a pane reading a
-- failure (`view/errors.py`).
-- Session-wide rather than per thread, for the reason the unattached bucket is: what a
-- subagent failed at is what the session failed at, and the NavTree opens one path — so nothing
-- else on a node page reaches a failure five spawns down without reading everything first.
-- No join up to the api call or the turn: each row leads to the tool call's own page, which
-- carries the crumb chain that places it. Thin like `view_nav_tree_tools`, and cut to the same
-- width, because both name a node rather than describe one.
-- The order is total — `(source, "index")` and `(source, id)` are each unique within a
-- session (`export/duckdb.py`) — which is what a cut means anything against: a page showing
-- the first `$errors` of a partial order would show different rows on two reads of one store.
SELECT
    t.source,
    t.id AS tool_call_id,
    -- What the tool was called, beside the fields its title is composed out of — which is what
    -- tells two failures of one tool apart in the width of a row (`view/tool_names.py`).
    cut(t.name, $nav_chars) AS name,
    -- And what the input carried under the names the tools the viewer knows name their calls
    -- by, so a `Read` row reads as a path and a `Bash` row as the command it ran
    -- (`view/tool_names.py:FORMATTERS`). Every member cut to the same width as the title above.
    tool_fields(t.input, s.project_dir, ad.agent_type, $nav_chars) AS fields,
    -- Constant true under this filter, and selected anyway: a tool node carries the flag
    -- wherever it is built from, so every query behind one answers the same columns.
    t.is_error,
    t.started_at,
    -- How many the session failed in all, counted before the LIMIT bites, so a page that
    -- showed the first `$errors` can say how many it left rather than reading as the whole.
    count(*) OVER () AS matched_rows
FROM live_tool_calls t
-- What a path in the title is read against. LEFT joined, so a tool call whose session row is
-- missing is a row titled with an absolute path rather than a failure the list drops.
LEFT JOIN sessions s ON s.id = t.session_id
-- Who a `SendMessage` addressed, where `to` held an agent run's id rather than a name the
-- caller typed: one lookup, LEFT so a name that matches no run comes back NULL and the row
-- prints what was recorded (`view/tool_names.py:_send_message`).
LEFT JOIN live_agent_runs ad
    ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $nav_chars)
WHERE t.session_id = $session_id
  AND t.is_error
ORDER BY t.started_at, t.source, t."index", t.id
LIMIT $errors;
