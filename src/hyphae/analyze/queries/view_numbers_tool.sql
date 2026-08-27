-- The exact numbers behind one tool call's tree row: how much it gave back, and what else the
-- api call that made it asked for in the same breath.
--
-- A tool call reports no usage of its own — its tokens are its api call's (`docs/schema.md`) —
-- so there is no window and no price to print. What the store does hold is the size of the
-- result, which is the honest proxy for what the call put in front of the model, and the other
-- calls it was made alongside. Titles through `tool_title` (`analyze/macros.py`), so the
-- siblings read the way the same calls read everywhere else.
WITH beside AS (
    SELECT coalesce(list(o.title ORDER BY o."index"), []) AS titles
    FROM (
        SELECT o."index", tool_title(o.input, s.project_dir, $item_chars) AS title
        FROM live_tool_calls t
        JOIN live_tool_calls o
          ON o.session_id = t.session_id
         AND o.source = t.source
         AND o.api_call_id = t.api_call_id
         AND o.id <> t.id
        LEFT JOIN sessions s ON s.id = t.session_id
        WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id
    ) o
)
SELECT
    -- NULL where the tool returned nothing at all, which is not the same as returning "".
    length(t.result) AS result_chars,
    length(t.input) AS input_chars,
    -- Where the result was written instead of stored, which is why a large one can read as
    -- nothing here (`docs/store.md`).
    t.offload_file,
    -- Bound like a header's lists: an api call can make a thousand tool calls, and a popover
    -- is not a level to page through — so it names the first few and says how many it left.
    list_slice(beside.titles, 1, $head_items) AS siblings,
    greatest(len(beside.titles) - $head_items, 0) AS siblings_cut
FROM live_tool_calls t
CROSS JOIN beside
WHERE t.session_id = $session_id AND t.source = $source AND t.id = $tool_call_id;
