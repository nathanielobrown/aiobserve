"""The SQL functions the query library is written against, and the one call that installs them.

A query file is the unit a report cites and a reader re-runs, so a rule two files share cannot
live in either of them: the copies drift, and then one query denies what the other reported.
What lives here is the shared half — a definition several queries call by name — as a DuckDB
temp macro, created on whatever connection is about to run a query.

`analyze/runner.py` installs them before it runs the query it was asked for, and anything
else that runs a query file has to do the same. That is the trade a shared definition costs:
a query file naming one of these runs under a consumer that installed them, and under a bare
`duckdb` shell it does not.
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

# Every macro a shipped query may call. Installed as a set rather than per query: which macros
# a file needs is the file's business, and a connection that holds some of them is a connection
# where a query fails on the ones it does not.
_MACROS = (_SIGNATURE_LINE, _REBUILT_CONTEXT)


def install(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the library's macros on `connection`, before any query file runs against it.

    Temp macros, so this works on the read-only connection both consumers open: what it
    creates lives in the session's own catalog rather than in the store.
    """
    for macro in _MACROS:
        connection.execute(macro)
