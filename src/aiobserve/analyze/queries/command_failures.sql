-- Which command produced the error, when the error text does not say. `tool_failures` counts
-- per tool and `error_signatures` per error text, and neither reaches a bare `Exit code 1` —
-- a shell's exit code names nothing, so a grep that found no match and a build that broke are
-- one group. This groups the same failures by the shape of the command line instead.
-- `command_head` is the command word plus the bare lowercase words after it, so `gh pr checks`
-- and `gh pr create` stay apart while `grep -rn "…" src/` and `grep -c … README.md` come
-- together. What sits before the command is cut first: leading `VAR=value` assignments, any
-- number of `cd <dir> &&` wrappers — 839 of the 1,487 failed Bash commands in mycelia's
-- 2026-08-07 window open with one — and a directory path on the command word itself.
-- Nothing else survives into the group key: no flag, path, quoted argument or URL can reach
-- the output, which is what makes a table of private command lines publishable.
-- A row whose `signature` is NULL is that shape's calls that *succeeded* — the denominator an
-- error count is read against, since a command that fails 100 times in 110 calls is a
-- different claim from one that fails 100 times in 4,000. `$min_occurrences` can drop it.
-- Bind `$mentions` to keep only command lines containing a phrase: the head is the *first*
-- command, so a `grep` further down a pipeline is invisible to the grouping and countable
-- only this way.
WITH commanded AS (
    SELECT
        p.period,
        t.name AS tool,
        -- Any tool whose input carries a command, discovered rather than listed: `Bash` is
        -- the one today, and an MCP server that shells out would belong in the same table.
        json_extract_string(t.input, '$.command') AS command,
        t.is_error,
        t.result,
        t.session_id,
        t.source
    FROM session_period p
    JOIN corpus_tool_calls t USING (session_id)
    WHERE json_valid(t.input)
      AND json_extract_string(t.input, '$.command') IS NOT NULL
), shaped AS (
    SELECT
        period,
        tool,
        is_error,
        result,
        session_id,
        source,
        regexp_replace(
            regexp_replace(
                regexp_replace(trim(command), '^([A-Za-z_][A-Za-z0-9_]*=[^\s;]*[\s;]+)+', ''),
                '^(\s*cd\s+[^&;|]+&&\s*)+',
                ''
            ),
            '^\S*/',
            ''
        ) AS invocation
    FROM commanded
    WHERE $mentions::VARCHAR IS NULL OR contains(command, $mentions)
), grouped AS (
    SELECT
        period,
        tool,
        substr(
            regexp_extract(invocation, '^(\S+(\s+[a-z][a-z-]*){0,2})(\s|$)', 1), 1, $head_chars
        ) AS command_head,
        CASE
            WHEN is_error THEN substr(
                regexp_replace(trim(split_part(result, chr(10), 1)), '\s+', ' ', 'g'),
                1,
                $signature_chars
            )
        END AS signature,
        count(*) AS calls,
        -- How much of the corpus the count is evidence about, as `error_signatures` reports it.
        count(DISTINCT session_id) AS sessions,
        count(DISTINCT session_id || ':' || source) AS threads,
        -- Calls whose line held more than one command after the wrapper strip. The head is the
        -- first command, not necessarily the one that set the exit code, and this says how
        -- often that gap is open.
        count(*) FILTER (regexp_matches(invocation, '&&|\||;')) AS chained,
        count(*) FILTER (is_error) AS errors
    FROM shaped
    GROUP BY 1, 2, 3, 4
    HAVING count(*) >= $min_occurrences
)
SELECT
    period,
    tool,
    command_head,
    signature,
    calls,
    sessions,
    threads,
    chained,
    -- The shape's failures across every signature it carries, repeated on each of its rows:
    -- the ranking a reader wants ("which command shape fails most") without summing by hand.
    sum(errors) OVER (PARTITION BY period, tool, command_head) AS head_errors
FROM grouped
ORDER BY period, head_errors DESC, command_head, calls DESC, signature;
