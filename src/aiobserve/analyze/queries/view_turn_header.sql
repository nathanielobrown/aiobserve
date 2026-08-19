-- One turn, whole: what was asked, when, and what answering it took. The header of a turn's
-- node page, so what was typed is cut at `$detail_chars` with its whole length beside it — a
-- pane shows the head and says how much more there is (`view_turn_prompt` has the rest).
WITH call AS (
    SELECT * FROM live_api_calls
    WHERE session_id = $session_id AND source = $source AND turn_id = $turn_id
), tool AS (
    SELECT count(*) AS tool_calls, count(*) FILTER (tc.is_error) AS tool_errors
    FROM live_tool_calls tc
    JOIN call c ON c.id = tc.api_call_id
    WHERE tc.session_id = $session_id AND tc.source = $source
)
SELECT
    t."index" AS turn_index,
    t.id AS turn_id,
    substr(t.prompt, 1, $detail_chars) AS prompt,
    length(t.prompt) AS prompt_chars,
    -- A slash turn's heading shows the command it ran and what followed it instead of the
    -- prompt, which still holds the tags Claude Code wrapped it in.
    substr(t.command_name, 1, $head_chars) AS command_name,
    -- Cut at a fact row's width and not at the pane's: a slash turn's `prompt` holds the
    -- whole `<command-…>` wrapper Claude Code built, arguments included, so the pane's one
    -- value is still the prompt.
    substr(t.command_args, 1, $head_chars) AS command_args,
    t.started_at,
    t.ended_at,
    -- A replayed turn is one a resume re-read rather than one the model answered again.
    t.replayed,
    (SELECT count(*) FROM call) AS api_calls,
    coalesce((SELECT tool_calls FROM tool), 0) AS tool_calls,
    coalesce((SELECT tool_errors FROM tool), 0) AS tool_errors,
    (SELECT round(coalesce(sum(cost_usd), 0), 4) FROM call) AS cost_usd,
    (SELECT count(*) FILTER (cost_usd IS NULL) FROM call) AS unpriced_api_calls
FROM live_turns t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $turn_id;
