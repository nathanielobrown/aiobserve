-- Every repository the store holds sessions for, most sessions first: what the viewer's
-- project filter offers. Names only — a project directory is a path, not session content.
-- A session whose transcript never named a working directory is left out rather than shown
-- as an empty option; the list unfiltered still holds it.
SELECT
    r.project_dir,
    count(*) AS sessions
FROM session_rollups r
WHERE r.project_dir IS NOT NULL
GROUP BY r.project_dir
ORDER BY sessions DESC, r.project_dir;
