-- One chunk of a tool result Claude Code wrote to a file beside the transcript instead of into
-- it. The content has no ceiling — the canonical store holds one over 50 MB — so it is read a
-- window at a time: `$after_chars` is how much has already been served and `$chunk_chars` how
-- much more to take. Characters, not bytes: `content` is text, and DuckDB cannot cut a string
-- on a byte boundary without splitting a codepoint.
-- `$name` is the transcript's own file name. It is a key here and nothing else — the viewer
-- reads this table, never a path on disk.
SELECT
    name,
    lossy_decode,
    size_bytes,
    length(content) AS content_chars,
    substr(content, $after_chars + 1, $chunk_chars) AS chunk
FROM offload_files
WHERE session_id = $session_id
  AND name = $name;
