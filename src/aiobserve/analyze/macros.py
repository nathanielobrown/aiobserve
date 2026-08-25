"""The SQL functions the query library is written against, and the one call that installs them.

A query file is the unit a report cites and a reader re-runs, so a rule two files share cannot
live in either of them: the copies drift, and then one query denies what the other reported.
What lives here is the shared half — a definition several queries call by name — as a DuckDB
temp macro, created on whatever connection is about to run a query.

Both consumers install the same set: `analyze/runner.py` before the query `aiobserve query`
was asked for, and `view/store.py` on the connection a page reads through. That is the trade a
shared definition costs: a query file naming one of these runs under a consumer that installed
them, and under a bare `duckdb` shell it does not.
"""

import duckdb

# The line a failure is grouped by: its first, whitespace collapsed, with every absolute path
# standing as `<path>`. Two queries group on it and a group key that drifted between them
# would count the same failure two ways.
# The paths are what makes it a macro rather than a `substr`. A message that carries its path
# in the *middle* of the sentence — Claude Code's worktree-isolation guardrail, and its
# "current working directory is …" note — splits into a group per worktree, and no length cut
# can merge them back. The guardrail alone held 36 failures in 28 groups over mycelia's
# 2026-08-13 window; collapsing paths took that window from 240 signatures to 185. Dropping
# the path is also what lets a signature be published: the value is ours, not the tool's.
# Trailing punctuation is left behind, so the sentence still reads as one.
_SIGNATURE_LINE = r"""
CREATE OR REPLACE TEMP MACRO signature_line(text) AS
regexp_replace(
    regexp_replace(trim(split_part(text, chr(10), 1)), '\s+', ' ', 'g'),
    '(^|\s)/[^\s]*[^\s.,;:]',
    '\1<path>',
    'g'
)
"""

# Whether one api call rebuilt the context it already had: it wrote at least `min_tokens` to
# the cache, and wrote at least `min_pct` of everything it cached. Shared for the same reason
# as the line above — `context_reloads.sql` counts these calls and `idle_gaps.sql` says which
# silences they followed, so a detector that drifted between them would let one query deny
# what the other reported. Neither number is a fact about Claude Code; `context_reloads.sql`
# holds the corpus measurements that placed them, and both stay bound parameters.
# The caller still owns the rest of the definition: a thread's first call writes everything
# and rebuilds nothing, and only the query knows where its thread starts.
_REBUILT_CONTEXT = """
CREATE OR REPLACE TEMP MACRO rebuilt_context(creation_tokens, read_tokens, min_tokens, min_pct)
AS creation_tokens >= min_tokens
   AND creation_tokens * 100 >= min_pct * (creation_tokens + read_tokens)
"""

# One field of a tool call's input, cut to the width of the column that will print it. Every
# read of `input` is guarded, because the column holds whatever the transcript did and
# `json_extract_string` raises on a value that is not JSON: a malformed input is a row to
# render, not a 500. The cut is one character past the width, which is the protocol every
# preview in the viewer rides (`view/format.py:cut`).
_TOOL_ASKED = """
CREATE OR REPLACE TEMP MACRO tool_asked(input, field, chars) AS
CASE WHEN json_valid(input)
     THEN substr(json_extract_string(input, '$.' || field), 1, chars + 1)
     END
"""

# A tool call's `file_path`, relative to the session's project directory when it sits inside it.
# The repository comes off before the cut, not after: an agent reads its own tree far more than
# anything else, so a column of absolute paths is a column of identical prefixes, and the part
# that tells them apart is the tail. Cutting first would spend the width on the prefix and then
# throw the prefix away — every relativized path would saturate short of the width, where
# nothing downstream can mark it (`view/format.py:cut` marks at the width, not below it).
# So the inner read asks for the prefix on top of the width, and the strip gives back exactly
# the one-past-the-width the cut protocol wants. A path outside the project — or a session with
# no `project_dir` — takes the absolute arm at the plain width.
_TOOL_PATH = """
CREATE OR REPLACE TEMP MACRO tool_path(input, project_dir, chars) AS
CASE WHEN starts_with(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                      project_dir || '/')
     THEN substr(tool_asked(input, 'file_path', chars + length(project_dir) + 1),
                 length(project_dir) + 2)
     ELSE tool_asked(input, 'file_path', chars) END
"""

# What a tool call is called, in the most readable form the record supports — the derivation
# behind every surface that names one (`docs/viewer.md`). Which part of the input the title
# comes from is decided by the input and not by a list of tool names, so a tool nobody here
# has heard of still names itself:
#   * a `file_path` is the path, relativized by `tool_path` above
#   * else a `description` — what the caller said the call was for, which is what `Bash` and
#     `Agent` put there
#   * else the head of the input as it was stored, which is JSON for every tool we have seen
_TOOL_TITLE = """
CREATE OR REPLACE TEMP MACRO tool_title(input, project_dir, chars) AS
coalesce(
    tool_path(input, project_dir, chars),
    tool_asked(input, 'description', chars),
    substr(input, 1, chars + 1))
"""

# The line under a tool call's title, where the title was a description and the input also
# carried the command it describes. NULL everywhere else, including on the calls whose title
# is already the command's own JSON — a row does not print one value twice.
_TOOL_RAN = """
CREATE OR REPLACE TEMP MACRO tool_ran(input, chars) AS
CASE WHEN tool_asked(input, 'file_path', chars) IS NULL
      AND tool_asked(input, 'description', chars) IS NOT NULL
     THEN tool_asked(input, 'command', chars)
     END
"""

# The macros a query may wrap a fat column in and still be bounded: each cuts what it reads to
# the width its caller passes. Named in public because the viewer's payload bound is held by a
# scan of query text (`tests/view/test_bounds.py`), and a scan cannot see through a macro call
# — so it trusts these names, and a leaf there re-scans each body to earn that trust.
BOUNDING = {
    "tool_asked": _TOOL_ASKED,
    "tool_path": _TOOL_PATH,
    "tool_title": _TOOL_TITLE,
    "tool_ran": _TOOL_RAN,
}

# Every macro a shipped query may call, in dependency order — `tool_path`, `tool_title` and
# `tool_ran` are written in terms of the ones above them. Installed as a set rather than per
# query: which macros a file needs is the file's business, and a connection that holds some of
# them is a connection where a query fails on the ones it does not.
_MACROS = (_SIGNATURE_LINE, _REBUILT_CONTEXT, *BOUNDING.values())


def install(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the library's macros on `connection`, before any query file runs against it.

    Temp macros, so this works on the read-only connection both consumers open: what it
    creates lives in the session's own catalog rather than in the store.
    """
    for macro in _MACROS:
        connection.execute(macro)
