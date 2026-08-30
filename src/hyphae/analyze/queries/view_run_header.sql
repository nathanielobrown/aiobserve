-- One agent run's header: what it was asked to do, where it sits, and what it spent.
-- A run's id is also the `source` its rows carry, so its counts are the session's rows at
-- that source — the same rule `run_timeline` reads by.
-- Every column is the run's own row or a count of its rows. Where a run hangs in the NavTree is
-- `view_runs`' answer, read once for the whole session; resolving it again here would give
-- the two a way to disagree.
SELECT
    a.id AS run_id,
    a.session_id,
    -- The three strings a header carries from the transcript, each cut to the same head:
    -- whatever the spawning agent typed in the Agent tool's `description`, the definition it
    -- named, and the model the run answered on. Nothing on the far side of any of them bounds
    -- what it holds — an agent definition is named by whoever writes it.
    cut(a.agent_type, $head_chars) AS agent_type,
    -- The task brief, cut one character past a pane's width — the protocol
    -- `view/format.py:cut` reads — with its whole length beside it; the rest is fetched as
    -- one value (`view_run_brief`).
    cut(a.brief, $detail_chars) AS brief,
    length(a.brief) AS brief_chars,
    cut(a.model, $head_chars) AS model,
    -- What the run was asked and what its parent got back, both read off the one call that
    -- spawned it: Claude Code records a run's instructions as that call's `prompt` and the
    -- run's answer as its `result`. The answer is what the parent received and not the run's
    -- own last turn — a run that stopped without reporting told its parent nothing. Keyed on
    -- the JSON field rather than on the tool's name, because a run spawned by something other
    -- than `Agent` is asked in whatever that tool's arguments are called. Both are cut one
    -- character past a pane's width, and fetched whole as `view_run_prompt`/`view_run_result`.
    cut(json_extract_string(tc.input, '$.prompt'), $detail_chars) AS prompt,
    length(json_extract_string(tc.input, '$.prompt')) AS prompt_chars,
    cut(tc.result, $detail_chars) AS result,
    length(tc.result) AS result_chars,
    a.spawn_depth,
    a.is_fork,
    a.parent_agent_id,
    a.tool_use_id,
    a.started_at,
    a.ended_at,
    date_diff('millisecond', a.started_at, a.ended_at) AS wall_ms,
    (SELECT count(*) FROM live_turns t
        WHERE t.session_id = a.session_id AND t.source = a.id) AS turns,
    (SELECT count(*) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS api_calls,
    (SELECT count(*) FROM live_tool_calls l
        WHERE l.session_id = a.session_id AND l.source = a.id) AS tool_calls,
    (SELECT count(*) FROM live_tool_calls l
        WHERE l.session_id = a.session_id AND l.source = a.id AND l.is_error) AS tool_errors,
    (SELECT count(*) FROM live_compactions k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS compactions,
    (SELECT coalesce(sum(k.output_tokens), 0) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS output_tokens,
    -- Sums only the calls our price table prices; the count beside it says how many it left
    -- out, so a total is never read as complete without checking.
    (SELECT round(coalesce(sum(k.cost_usd), 0), 4) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id) AS cost_usd,
    (SELECT count(*) FROM live_api_calls k
        WHERE k.session_id = a.session_id AND k.source = a.id
          AND k.cost_usd IS NULL) AS unpriced_api_calls
FROM live_agent_runs a
-- The spawning call, by the rule `view_runs` reads it by: a run's `tool_use_id` names a tool
-- call on some other thread, and the source guard is what keeps a run from matching itself.
-- LEFT, because a resumed or forked transcript replays runs whose spawning call it never held.
LEFT JOIN live_tool_calls tc
    ON tc.session_id = a.session_id AND tc.id = a.tool_use_id AND tc.source <> a.id
WHERE a.session_id = $session_id AND a.id = $run_id;
