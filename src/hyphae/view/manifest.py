"""What each of the viewer's queries takes: one entry per `view_*.sql` file.

The `view_` family are library queries like any other — `hp query` runs them and a footer
cites them — and the viewer composes its sort, its filter and its paging around them rather
than embedding SQL of its own. What sets them apart is who owns their numbers: a width or a
row count is the surface's, named beside the ceiling that caps it in `view/bounds.py`, so
these entries sit here rather than in the analysis half.

No entry declares a default. The viewer never reads one — every page composes its own
bindings, and DuckDB refuses an unbound parameter rather than falling back — so a default
here would be a number nothing runs and nothing checks. It is also a number that cannot be
right: `chip_chars` runs at 110 in a NavTree row and at 300 in a runs log, and the analysis
half's promise for a default is that it is *the* value a bare invocation runs and a committed
report quotes. The width lives once, as the constant in `analyze/queries.py` that the surface
binds, and what a page ran is quoted in the citation under it. A reader running one of these
from the command line states every size, which `hp query --list` prints.

They are read through the one registry `analyze/manifest.py` publishes, which is what the
runner binds against and what the smoke tier holds to the query directory. The SQL itself
lives with every other query file in `analyze/queries/`, and the parameter vocabulary these
entries are written in is `analyze/queries.py`.
"""

from collections.abc import Mapping

from hyphae.analyze.queries import (
    API_CALL_ID,
    COMPACTION_ID,
    NODE_ID,
    NODE_KIND,
    REQUIRED,
    RUN_ID,
    SESSION_ID,
    SOURCE,
    TOOL_CALL_ID,
    TURN_ID,
    Param,
    ParamType,
)

# Every number a viewer query takes: a width to cut a value to, a page of rows to fetch, a
# cursor to resume from. One spelling for all of them, because what tells them apart is the
# surface that binds one and not anything declared here.
SIZE = Param(type=ParamType.INTEGER, default=REQUIRED)

VIEW_QUERIES: dict[str, Mapping[str, Param]] = {
    "view_call_text": {"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    "view_call_thinking": {"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    "view_call_tools": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "api_call_id": API_CALL_ID,
        "skipped": SIZE,
        "page_tools": SIZE,
        "log_chars": SIZE,
    },
    "view_call_header": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "api_call_id": API_CALL_ID,
        "head_chars": SIZE,
        "detail_chars": SIZE,
    },
    "view_described_sessions": {
        "head_chars": SIZE,
        "tag_chars": SIZE,
        "kind_chars": SIZE,
        "head_kinds": SIZE,
    },
    "view_enrichment": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "description_chars": SIZE,
        "tag_chars": SIZE,
        "head_chars": SIZE,
    },
    "view_turn_said": {"session_id": SESSION_ID, "source": SOURCE, "turn_id": TURN_ID},
    "view_run_said": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_session_said": {"session_id": SESSION_ID},
    "view_compactions": {"session_id": SESSION_ID, "source": SOURCE, "chip_chars": SIZE},
    "view_run_header": {
        "session_id": SESSION_ID,
        "run_id": RUN_ID,
        "head_chars": SIZE,
        "detail_chars": SIZE,
    },
    "view_run_brief": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_run_prompt": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_run_result": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_numbers": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "node_id": NODE_ID,
        "kind": NODE_KIND,
        "model_chars": SIZE,
    },
    "view_numbers_compaction": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "compaction_id": COMPACTION_ID,
        "chip_chars": SIZE,
    },
    "view_numbers_tool": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "tool_call_id": TOOL_CALL_ID,
        "item_chars": SIZE,
        "head_items": SIZE,
    },
    "view_offload": {
        "session_id": SESSION_ID,
        "name": Param(type=ParamType.TEXT, default=REQUIRED),
        "after_chars": SIZE,
        "chunk_chars": SIZE,
    },
    "view_project_rollups": {
        "as_of": Param(type=ParamType.DATE, default=REQUIRED),
        "recent_days": SIZE,
        "window_days": SIZE,
        "head_chars": SIZE,
        "projects": SIZE,
    },
    "view_projects": {"head_chars": SIZE, "head_projects": SIZE},
    "view_record": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "line_no": Param(type=ParamType.INTEGER, default=REQUIRED),
    },
    "view_records": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "after": SIZE,
        "page_records": SIZE,
        "preview_chars": SIZE,
    },
    "view_runs": {"session_id": SESSION_ID, "chip_chars": SIZE},
    "view_session_header": {
        "session_id": SESSION_ID,
        "head_chars": SIZE,
        "item_chars": SIZE,
        "head_items": SIZE,
    },
    "view_session_errors": {
        "session_id": SESSION_ID,
        "nav_chars": SIZE,
        "errors": SIZE,
    },
    "view_sessions": {"item_chars": SIZE},
    "view_tool_header": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "tool_call_id": TOOL_CALL_ID,
        "head_chars": SIZE,
        "detail_chars": SIZE,
    },
    "view_tool_command": {"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    "view_tool_input": {"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    "view_tool_result": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "tool_call_id": TOOL_CALL_ID,
        "head_chars": SIZE,
    },
    "view_nav_tree_calls": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "turn_id": TURN_ID,
        "nav_chars": SIZE,
    },
    "view_nav_tree_tools": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "api_call_id": API_CALL_ID,
        "turn_id": TURN_ID,
        "nav_chars": SIZE,
    },
    "view_nav_tree_turns": {"session_id": SESSION_ID, "source": SOURCE, "nav_chars": SIZE},
    "view_turn_calls": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "turn_id": TURN_ID,
        "skipped": SIZE,
        "page_calls": SIZE,
        "log_chars": SIZE,
    },
    "view_turn_command_args": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "turn_id": TURN_ID,
    },
    "view_turn_header": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "turn_id": TURN_ID,
        "head_chars": SIZE,
        "detail_chars": SIZE,
    },
    "view_turn_prompt": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "turn_id": TURN_ID,
    },
    "view_turn_records": {"session_id": SESSION_ID, "source": SOURCE},
}
