-- What one tool call returned, whole — the largest single fetch the viewer makes, against the
-- store's biggest recorded `result`. NULL means the tool returned nothing, which a call that
-- returned "" did not; a result the transcript offloaded to a file is NULL here too, and that
-- file has its own page (`view_offload`) rather than riding in this row.
SELECT
    t.result AS value,
    -- What the result is written in, where the call says so: the suffix of the file a `Read`
    -- returned, by the rule `view_tool_header` reads it by, so the whole value is marked up
    -- the way its preview on the pane was.
    CASE WHEN t.name = 'Read' AND json_valid(t.input)
         THEN substr(
             lower(regexp_extract(json_extract_string(t.input, '$.file_path'), '\.[^./]+$')),
             1, $head_chars)
         END AS result_type
FROM live_tool_calls t
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
