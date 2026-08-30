"""The SQL functions the query library is written against, and the one call that installs them.

A query file is the unit a report cites and a reader re-runs, so a rule two files share cannot
live in either of them: the copies drift, and then one query denies what the other reported.
What lives here is the shared half — a definition several queries call by name — as a DuckDB
temp macro, created on whatever connection is about to run a query.

Both consumers install the same set: `analyze/runner.py` before the query `hp query`
was asked for, and `view/store.py` on the connection a page reads through. That is the trade a
shared definition costs: a query file naming one of these runs under a consumer that installed
them, and under a bare `duckdb` shell it does not.
"""

import duckdb

from hyphae.extract.pricing import MODELS

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

# Where one api call left the model's context window, and how much of that the call itself put
# there. The fill is everything the reply was billed for: the cache it read is context it was
# working in, and its own output is context the next call inherits. What it added is that less
# the read — the part of the window this call put in front of the model.
# Macros because every level of the NavTree derives them and the popover prints them, and a level
# that counted the window its own way would draw a bar denying the row above it. Neither reads
# a fat column: what they take is a row of `live_api_calls`, and what they answer is a count.
_CONTEXT_FILL = """
CREATE OR REPLACE TEMP MACRO context_fill(call) AS
call.cache_read_tokens + call.cache_creation_tokens + call.input_tokens + call.output_tokens
"""

_CONTEXT_ADDED = """
CREATE OR REPLACE TEMP MACRO context_added(call) AS
call.cache_creation_tokens + call.input_tokens + call.output_tokens
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

# The window each model answers in, written out of the model table `extract/pricing.py` keeps.
# Generated rather than bound as a parameter so a query names a model and gets a number, with
# the constant still defined in one place — and so `SETUP` hands a reader the whole rule rather
# than a macro they have to supply the numbers for. A model the table lacks — and the
# placeholder, which states no window — answers NULL, which is a bar the viewer does not draw
# rather than a scale it invents.
_CONTEXT_WINDOW = (
    "\nCREATE OR REPLACE TEMP MACRO context_window(model) AS CASE model\n"
    + "".join(
        f"    WHEN '{model}' THEN {spec.context_window}\n"
        for model, spec in MODELS.items()
        if spec.context_window is not None
    )
    + "END\n"
)

# What a tool call carried, for the rules that name one (`view/tool_names.py`) — one struct
# rather than a column apiece, so a query adds the whole set with one expression and a
# formatter reads what it needs by name.
# Extraction only: which member a tool reads is the registry's business, and keeping the
# name list out of here is what lets a tool be renamed or added without a query changing.
# Every string member rides the same one-past-the-width protocol as the macros above.
# `addressed` is the caller's, not the input's: a `SendMessage` addresses an agent run by id,
# and the name behind that id is a row of `live_agent_runs` the query joins.
# `input_head` is the last member because it is the last resort: the input as recorded, for a
# tool no rule names and whose input carried nothing any rule reads.
# `query` and `message` were read off session `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b` (Claude
# Code 2.1.221), which is what `ToolSearch` and `PushNotification` name their calls by
# (`view/tool_names.py`) and the recording `tests/fixtures/spine` is cut from.
_TOOL_FIELDS = """
CREATE OR REPLACE TEMP MACRO tool_fields(input, project_dir, addressed, chars) AS {
    'path': tool_path(input, project_dir, chars),
    'command': tool_asked(input, 'command', chars),
    'description': tool_asked(input, 'description', chars),
    'subagent_type': tool_asked(input, 'subagent_type', chars),
    'skill': tool_asked(input, 'skill', chars),
    'args': tool_asked(input, 'args', chars),
    'to': tool_asked(input, 'to', chars),
    'addressed': substr(addressed, 1, chars + 1),
    'summary': tool_asked(input, 'summary', chars),
    'pattern': tool_asked(input, 'pattern', chars),
    'url': tool_asked(input, 'url', chars),
    'query': tool_asked(input, 'query', chars),
    'message': tool_asked(input, 'message', chars),
    'todos': CASE WHEN json_valid(input)
                  THEN json_array_length(input, '$.todos') END,
    'input_head': substr(input, 1, chars + 1)
}
"""

# The macros a query may wrap a fat column in and still be bounded: each cuts what it reads to
# the width its caller passes. Named in public because the viewer's payload bound is held by a
# scan of query text (`tests/view/test_bounds.py`), and a scan cannot see through a macro call
# — so it trusts these names, and a leaf there re-scans each body to earn that trust.
BOUNDING = {
    "tool_asked": _TOOL_ASKED,
    "tool_path": _TOOL_PATH,
    "tool_fields": _TOOL_FIELDS,
}

# Every macro a shipped query may call, in dependency order — `tool_path` and `tool_fields` are
# written in terms of the ones above them. Installed as a set rather than per query: which
# macros a file needs is the file's business, and a connection that holds some of them is a
# connection where a query fails on the ones it does not.
DEFINITIONS = {
    "signature_line": _SIGNATURE_LINE,
    "rebuilt_context": _REBUILT_CONTEXT,
    "context_fill": _CONTEXT_FILL,
    "context_added": _CONTEXT_ADDED,
    "context_window": _CONTEXT_WINDOW,
    **BOUNDING,
}

# The same set as one script a reader can paste, which is what the viewer prints above a
# statement that calls any of them (`view/pages.py:query_page`). Semicolons and the install
# order are the whole difference: what a consumer does on your behalf, written out.
SETUP = ";\n".join(definition.strip() for definition in DEFINITIONS.values()) + ";"


def needed_by(sql: str) -> str:
    """The setup `sql` must run under, or nothing when it calls no macro at all.

    Named by hand rather than parsed: a statement mentioning one of these names in a comment
    gets the definitions too, which costs a reader nothing and is the safe way to be wrong.
    """
    return SETUP if any(f"{name}(" in sql for name in DEFINITIONS) else ""


def install(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the library's macros on `connection`, before any query file runs against it.

    Temp macros, so this works on the read-only connection both consumers open: what it
    creates lives in the session's own catalog rather than in the store.
    """
    for macro in DEFINITIONS.values():
        connection.execute(macro)
