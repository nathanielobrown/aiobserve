-- Which errors keep happening, counted. A reader who saw "File has not been read yet" in
-- three sessions has a recurring observation; this is what turns it into a number.
-- The signature is `signature_line` — the first line of the error text, whitespace collapsed,
-- with paths cut out — capped at `$signature_chars`, because the line that names the failure
-- carries the path or the command that hit it, text that would split one error into a
-- hundred groups.
-- Bind `$signature` to count a phrase wherever it sits in the text instead: it matches
-- case-sensitively against the whole result, so an error whose first line is a generic
-- "Error:" is still countable by the sentence underneath it.
-- `$min_occurrences` bounds the output. Most error text on a real corpus is unique, so
-- without a floor this returns one row per failed call.
WITH failure AS (
    SELECT
        p.period,
        t.name AS tool,
        substr(signature_line(t.result), 1, $signature_chars) AS signature,
        t.session_id,
        t.source
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    WHERE t.is_error
      AND ($signature::VARCHAR IS NULL OR contains(t.result, $signature))
)
SELECT
    period,
    tool,
    signature,
    count(*) AS errors,
    -- How much of the corpus the count is evidence about. An error 400 times over in one
    -- session is that session's loop; the same number over 40 threads is the tool's.
    count(DISTINCT session_id) AS sessions,
    count(DISTINCT session_id || ':' || source) AS threads
FROM failure
GROUP BY period, tool, signature
HAVING count(*) >= $min_occurrences
ORDER BY period, errors DESC, sessions DESC, tool, signature;
