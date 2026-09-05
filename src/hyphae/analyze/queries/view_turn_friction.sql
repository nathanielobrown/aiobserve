-- And the whole of the friction the pass saw in that turn, keyed the same way. Its own query
-- rather than a second column beside the description: a fetch is for one value, and the pane
-- offers the two apart because a reader opens whichever of them ran past the width.
SELECT e.friction AS value
FROM turn_enrichments e
WHERE e.session_id = $session_id AND e.source = $source AND e.turn_id = $turn_id;
