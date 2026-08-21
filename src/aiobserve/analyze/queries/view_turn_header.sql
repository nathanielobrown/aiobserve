-- One turn, whole: what was asked, when, and what answering it took. The header of a turn's
-- node page, so what was typed is cut one character past `$detail_chars` — the protocol
-- `view/format.py:cut` reads — with its whole length beside it, and a pane shows the head,
-- marks it as cut and says how much more there is (`view_turn_prompt` has the rest).
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
    substr(t.prompt, 1, $detail_chars + 1) AS prompt,
    length(t.prompt) AS prompt_chars,
    -- A slash turn leads with the command it ran rather than with the prompt, which holds
    -- the `<command-…>` wrapper Claude Code expanded it into. The name is a word, so it is
    -- cut to a fact's width...
    substr(t.command_name, 1, $head_chars) AS command_name,
    -- ...and what followed it is a value of the turn like the prompt is — arguments run to
    -- thousands of characters — so it is cut one past `$detail_chars` with its whole length
    -- beside it, and `view_turn_command_args` has the rest.
    substr(t.command_args, 1, $detail_chars + 1) AS command_args,
    length(t.command_args) AS command_args_chars,
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
