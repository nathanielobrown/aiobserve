-- One tool call, whole: what it was asked and what it answered, cut to a pane's width.
-- The header of a tool call's node page. Both fat columns are cut one character past
-- `$detail_chars` — the protocol `view/format.py:cut` reads — with their whole lengths
-- beside them; the rest is fetched as one value (`view_tool_value`), and
-- a result the transcript offloaded to a file has a page of its own instead.
SELECT
    t."index" AS tool_index,
    t.id AS tool_call_id,
    -- The session, which the pane needs to link an offloaded result to its own page.
    t.session_id,
    -- The call that made it, which is this node's parent — and the turn that call answers,
    -- which is its grandparent. Both here so the path down to a tool needs no second read;
    -- read through the join for the reason `view_call_header` states, and NULL where the call
    -- answers no turn of this thread, which puts it in this thread's unattributed bucket.
    t.api_call_id,
    n.id AS turn_id,
    substr(t.name, 1, $head_chars + 1) AS name,
    -- What the pane heads the call with, by the derivation the NavTree row, the errors list and
    -- the parent's children log all read (`analyze/macros.py`) — so a reader who clicked a
    -- row lands on a page headed the way the row was.
    tool_title(t.input, s.project_dir, $head_chars) AS title,
    -- And what the input carried under the names the tools the viewer knows name their calls
    -- by, so a `Read` row reads as a path and a `Bash` row as the command it ran
    -- (`view/formatters.py:FORMATTERS`). Every member cut to the same width as the title above.
    tool_fields(t.input, s.project_dir, ad.agent_type, $head_chars) AS fields,
    t.server_side,
    t.is_error,
    t.incomplete,
    t.offload_file,
    -- The run this call started, where it started one. A `Task` call is where an agent run
    -- begins, so its node view leads with the way there; NULL for every other tool. Joined by
    -- the same rule the rest of the library reads the spawning edge by — `tc.source <> a.id`
    -- keeps a fork's own copy of the call that spawned it from answering here.
    a.id AS run_id,
    t.started_at,
    t.ended_at,
    date_diff('millisecond', t.started_at, t.ended_at) AS wall_ms,
    substr(t.input, 1, $detail_chars + 1) AS input,
    length(t.input) AS input_chars,
    -- NULL where the tool returned nothing at all, which is not the same as returning "".
    substr(t.result, 1, $detail_chars + 1) AS result_head,
    length(t.result) AS result_chars,
    -- What a `Bash` call ran, as a value of its own: the input holds it escaped onto one line
    -- among the call's other arguments, and a shell command is the thing a reader opened the
    -- call to read. `Bash` and not every input carrying the word — a value marked up as shell
    -- claims to be shell — and guarded like every other read of `input`, which is whatever
    -- the transcript held and raises `json_extract_string` when it is not JSON.
    CASE WHEN t.name = 'Bash' AND json_valid(t.input)
         THEN substr(json_extract_string(t.input, '$.command'), 1, $detail_chars + 1)
         END AS command,
    CASE WHEN t.name = 'Bash' AND json_valid(t.input)
         THEN length(json_extract_string(t.input, '$.command'))
         END AS command_chars,
    -- And the suffix of the file a `Read` returned, lowercased, which is the only evidence in
    -- the record of what its result holds (`view/highlight.py:by_suffix` places it). `Read`
    -- alone: an `Edit` names a file too, but what it returns is a confirmation, not the file.
    CASE WHEN t.name = 'Read' AND json_valid(t.input)
         -- Cut at the width and not one past it, unlike every string this query previews:
         -- a suffix is a key looked up in a closed set and never printed, so a character
         -- saying it went on would only be a character the lookup has to miss on.
         THEN substr(
             lower(regexp_extract(json_extract_string(t.input, '$.file_path'), '\.[^./]+$')),
             1, $head_chars)
         END AS result_type
FROM live_tool_calls t
JOIN live_api_calls c
    ON c.session_id = t.session_id AND c.source = t.source AND c.id = t.api_call_id
LEFT JOIN live_turns n
    ON n.session_id = c.session_id AND n.source = c.source AND n.id = c.turn_id
LEFT JOIN live_agent_runs a
    ON a.session_id = t.session_id AND a.tool_use_id = t.id AND t.source <> a.id
-- What a path in the title is read against, LEFT joined for the reason the other three
-- queries that title a tool call state.
LEFT JOIN sessions s ON s.id = t.session_id
-- Who a `SendMessage` addressed, where `to` held an agent run's id rather than a name the
-- caller typed: one lookup, LEFT so a name that matches no run comes back NULL and the row
-- prints what was recorded (`view/formatters.py:_send_message`).
LEFT JOIN live_agent_runs ad
    ON ad.session_id = t.session_id AND ad.id = tool_asked(t.input, 'to', $head_chars)
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
