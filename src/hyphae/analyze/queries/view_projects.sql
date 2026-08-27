-- Every repository the store holds sessions for, most sessions first: what the viewer's
-- project filter offers. Names only — a project directory is a path, not session content.
-- A session whose transcript never named a working directory is left out rather than shown
-- as an empty option; the list unfiltered still holds it.
-- The suggestions are roots, folded the way `view_project_rollups.sql` and the CLI's
-- `--project` fold: a worktree's sessions count under the checkout it was cut from, so the
-- box offers the checkout rather than a path that names a slice of it.
-- Suggestions grow with the corpus, so the box takes the `$head_projects` busiest and offers
-- each path whole or not at all: a path cut to `$head_chars` would fill the box in with a
-- value that finds nothing. Both bound because the suggestions ride every page of the list
-- (`tests/view/test_bounds.py`).
WITH roots AS (
    SELECT DISTINCT project_dir FROM corpus_rollups WHERE project_dir IS NOT NULL
),
folded AS (
    SELECT
        (SELECT min_by(a.project_dir, length(a.project_dir)) FROM roots a
         WHERE r.project_dir = a.project_dir
            OR starts_with(r.project_dir, a.project_dir || '/')) AS project_dir
    FROM corpus_rollups r
    WHERE r.project_dir IS NOT NULL
)
SELECT
    project_dir,
    count(*) AS sessions
FROM folded
WHERE length(project_dir) <= $head_chars
GROUP BY project_dir
ORDER BY sessions DESC, project_dir
LIMIT $head_projects;
