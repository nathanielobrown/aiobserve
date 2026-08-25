-- What an enrichment pass said about one session, about one of its threads' turns, and about
-- its agent runs — the rows the viewer shows beside what each item actually did.
-- `enrichment_digest` is the same question for a report: it reads the `enriched_*` views, so
-- an undescribed item keeps a row with a NULL description and coverage reads honestly. This
-- one reads the enrichment tables directly, because a page shows an undescribed item as an
-- item with nothing beside it — a row of NULLs would be markup standing in for absence.
-- `$source` is the thread whose turns the page renders: `main` on a session page, the run's
-- id on a run page. The other two levels belong to the session however it is being read.
-- A store no pass has touched holds none of these tables, which is why the viewer asks the
-- catalog before it runs this (`view/enrichment.py`).
SELECT
    'turn' AS level,
    e.turn_id AS item_id,
    substr(e.description, 1, $description_chars + 1) AS description,
    substr(e.category, 1, $tag_chars) AS category,
    substr(e.outcome, 1, $tag_chars) AS outcome,
    substr(e.friction, 1, $description_chars + 1) AS friction,
    -- How long each of the two runs whole, which is what the link beside a cut one offers.
    length(e.description) AS description_chars,
    length(e.friction) AS friction_chars,
    -- What wrote the line and when, beside the two versions it was written under: the pane
    -- prints all four in one place, so a reader can see whether a re-run would say more.
    -- The model is a name Anthropic chooses, so it is cut like every other foreign string.
    substr(e.model, 1, $head_chars) AS model,
    e.enriched_at,
    e.prompt_version,
    e.taxonomy_version
FROM turn_enrichments e
WHERE e.session_id = $session_id AND e.source = $source
UNION ALL
-- A run belongs to the session, not to the thread being read: a run page lists the runs under
-- it and the session page lists them all, and both want the same tags.
SELECT
    'agent_run', e.agent_run_id,
    substr(e.description, 1, $description_chars + 1),
    substr(e.category, 1, $tag_chars),
    substr(e.outcome, 1, $tag_chars),
    substr(e.friction, 1, $description_chars + 1),
    length(e.description), length(e.friction),
    substr(e.model, 1, $head_chars), e.enriched_at,
    e.prompt_version, e.taxonomy_version
FROM agent_run_enrichments e
WHERE e.session_id = $session_id
UNION ALL
-- The session's own row is keyed by the session, so the item id is the session id.
SELECT
    'session', e.session_id,
    substr(e.description, 1, $description_chars + 1),
    substr(e.category, 1, $tag_chars),
    substr(e.outcome, 1, $tag_chars),
    substr(e.friction, 1, $description_chars + 1),
    length(e.description), length(e.friction),
    substr(e.model, 1, $head_chars), e.enriched_at,
    e.prompt_version, e.taxonomy_version
FROM session_enrichments e
WHERE e.session_id = $session_id
ORDER BY level, item_id;
