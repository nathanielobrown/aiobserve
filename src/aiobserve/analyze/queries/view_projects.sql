-- Every repository the store holds sessions for, most sessions first: what the viewer's
-- project filter offers. Names only — a project directory is a path, not session content.
-- A session whose transcript never named a working directory is left out rather than shown
-- as an empty option; the list unfiltered still holds it.
-- Suggestions grow with the corpus, so the box takes the `$head_projects` busiest and offers
-- each path whole or not at all: a filter matches a project exactly, so a path cut to
-- `$head_chars` would fill the box in with a value that finds nothing. Both bound because the
-- suggestions ride every page of the list (`tests/view/test_bounds.py`).
SELECT
    r.project_dir,
    count(*) AS sessions
FROM session_rollups r
WHERE r.project_dir IS NOT NULL AND length(r.project_dir) <= $head_chars
GROUP BY r.project_dir
ORDER BY sessions DESC, r.project_dir
LIMIT $head_projects;
