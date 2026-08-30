-- The tool calls under one api call, in outline: the NavTree level below a call.
-- `$api_call_id` NULL asks the other question the NavTree has: every tool call under one turn,
-- ordered by the call that made it and then by its own index. That is the `noapi` preset's
-- level, where the api calls are hidden and their tool calls hoist to the turn — and, at
-- `$turn_id` NULL, the same for the calls that answer no turn. Unattributed is decided by the
-- join, not by `c.turn_id IS NULL`: a fork replays its parent's turn (`view_nav_tree_calls`).
-- Thin like `view_nav_tree_turns` and `view_nav_tree_calls`, and unlimited for the same reason — the
-- cap lives in the composition (`view/nav_tree.py`), where it has to keep the row the open path
-- goes through. A tool call costs nothing of its own: what an api call spent is the api call's.
-- The one exception is a row that asked for an agent run, which is charged what the call
-- holding it cost — so this query alone returns that price, and every other surface builds a
-- costless tool node (`view/builders.py:tool_node`).
SELECT
    -- Where the call that made it sits in the thread, which is what orders a hoisted level.
    c."index" AS call_index,
    t."index" AS tool_index,
    t.id AS tool_call_id,
    -- What the tool was called, beside the fields its title is composed out of — which is what
    -- tells two calls of the same tool apart in the width of a NavTree (`view/tool_names.py`).
    substr(t.name, 1, $nav_chars + 1) AS name,
    -- And what the input carried under the names the tools the viewer knows name their calls
    -- by, so a `Read` row reads as a path and a `Bash` row as the command it ran
    -- (`view/tool_names.py:FORMATTERS`). Every member cut to the same width as the title above.
    tool_fields(t.input, s.project_dir, ad.agent_type, $nav_chars) AS fields,
    -- When it ran, which is what the compactions of the same turn interleave against where
    -- the api calls are folded away and the tool calls stand under the turn.
    t.started_at,
    t.is_error,
    -- What the api call holding this tool call cost. Only a ⚒ row spends it, and only here:
    -- the NavTree is the one surface that draws a badge on a tool row. A call our price table
    -- could not price comes back NULL and its row wears the mark that says so, the way every
    -- other cost the viewer reports does.
    round(c.cost_usd, 4) AS call_cost_usd,
    (c.cost_usd IS NULL)::int AS unpriced_api_calls
FROM live_tool_calls t
JOIN live_api_calls c
    ON c.session_id = t.session_id AND c.source = t.source AND c.id = t.api_call_id
-- What a path in the title is read against. LEFT joined, so a tool call whose session row is
-- missing is a row titled with an absolute path rather than a row the NavTree drops.
LEFT JOIN sessions s ON s.id = t.session_id
-- Who a `SendMessage` addressed, where `to` held an agent run's id rather than a name the
-- caller typed: one lookup, LEFT so a name that matches no run comes back NULL and the row
-- prints what was recorded (`view/tool_names.py:_send_message`).
LEFT JOIN live_agent_runs ad
    ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $nav_chars)
LEFT JOIN live_turns tn
    ON tn.session_id = c.session_id AND tn.source = c.source AND tn.id = c.turn_id
WHERE t.session_id = $session_id
  AND t.source = $source
  AND ($api_call_id IS NULL OR t.api_call_id = $api_call_id)
  AND ($api_call_id IS NOT NULL OR tn.id IS NOT DISTINCT FROM $turn_id)
ORDER BY c."index", t."index";
