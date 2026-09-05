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
    # What a list row shows of a session's enrichment. The work cell is cut here rather
    # than in the composition around the query: nothing filters on it.
    "view_described_sessions": {
        "head_chars": SIZE,
        "tag_chars": SIZE,
        "kind_chars": SIZE,
        "head_kinds": SIZE,
    },
    "view_enrichment": {
        "session_id": SESSION_ID,
        # Which thread's turns to describe. The turn keys are `(session, source, turn)`,
        # so a page that left this out would be answering for another thread.
        "source": SOURCE,
        "description_chars": SIZE,
        "tag_chars": SIZE,
        # The model's own name is cut at a width of its own: a model string is longer
        # than a taxonomy word and shorter than a sentence.
        "head_chars": SIZE,
    },
    # The whole of what that pass wrote, one item at a time: the three levels the pane shows,
    # each keyed the way its own table is. The fetch behind a description or a friction line
    # the pane had to cut.
    "view_turn_said": {"session_id": SESSION_ID, "source": SOURCE, "turn_id": TURN_ID},
    "view_run_said": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_session_said": {"session_id": SESSION_ID},
    "view_compactions": {"session_id": SESSION_ID, "source": SOURCE, "chip_chars": SIZE},
    # The run's id is also the source its rows carry, so one key answers both questions.
    "view_run_header": {
        "session_id": SESSION_ID,
        "run_id": RUN_ID,
        "head_chars": SIZE,
        "detail_chars": SIZE,
    },
    "view_run_brief": {"session_id": SESSION_ID, "run_id": RUN_ID},
    # The two a run reads off the call that spawned it, keyed the same way the brief is.
    "view_run_prompt": {"session_id": SESSION_ID, "run_id": RUN_ID},
    "view_run_result": {"session_id": SESSION_ID, "run_id": RUN_ID},
    # The numbers behind one node's row, which the NavTree draws as a bar and a badge. One query
    # for every kind that is made of api calls, keyed by the kind as well as the id; the tool
    # call, which is made of none, has its own.
    "view_numbers": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        "node_id": NODE_ID,
        "kind": NODE_KIND,
        "model_chars": SIZE,
    },
    # And the compaction, which is made of no api calls either — what it has is the window it
    # dropped, off the boundary record itself.
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
        # Which file: a key, like the session it was written under.
        "name": Param(type=ParamType.TEXT, default=REQUIRED),
        "after_chars": SIZE,
        "chunk_chars": SIZE,
    },
    "view_project_rollups": {
        # The clock both windows are measured back from. A landing page's "last 7 days"
        # is only reproducible if the day it counted from is bound and cited, and SQL's
        # own clock would answer something else tomorrow.
        "as_of": Param(type=ParamType.DATE, default=REQUIRED),
        "recent_days": SIZE,
        "window_days": SIZE,
        # A project path takes a head, and the row links by the whole path: the head is
        # what the page shows and not what it filters by.
        "head_chars": SIZE,
        "projects": SIZE,
    },
    "view_projects": {"head_chars": SIZE, "head_projects": SIZE},
    "view_record": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        # Which line. A key like the two above: "some record of this thread" is not a
        # question anyone asked, and the answer would be private transcript either way.
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
        # The rows link to nodes, so a failure reads as the same line here as it does in
        # the NavTree beside its own page — which is what the surface binds it at.
        "nav_chars": SIZE,
        "errors": SIZE,
    },
    # How much of each agent definition's name a row's list carries. The viewer composes
    # the rest of a row's cuts around this query (`view/store.py`) because its filters
    # read whole values; nothing filters on this one, so it is cut in the file.
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
        # Not a width the answer is cut to — the value rides whole — but the bound on the
        # file suffix beside it, which says what the value is written in.
        "head_chars": SIZE,
    },
    "view_nav_tree_calls": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        # NULL is the real question "which calls sit under no turn", so the key carries
        # it: nothing may stand in for the id.
        "turn_id": TURN_ID,
        "nav_chars": SIZE,
    },
    "view_nav_tree_tools": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        # Both NULL-able, and NULL is a question at each: "under this turn, whichever
        # call made it" at the first, "under no turn of this thread" at the second.
        "api_call_id": API_CALL_ID,
        "turn_id": TURN_ID,
        "nav_chars": SIZE,
    },
    "view_nav_tree_turns": {"session_id": SESSION_ID, "source": SOURCE, "nav_chars": SIZE},
    "view_turn_calls": {
        "session_id": SESSION_ID,
        "source": SOURCE,
        # NULL is the real question "which calls sit under no turn", so the key carries
        # it: nothing may stand in for the id.
        "turn_id": TURN_ID,
        "skipped": SIZE,
        "page_calls": SIZE,
        # The width the two model names a call row shows are cut to.
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
        # Which turn. A key like the session and the thread: "some turn of this thread"
        # is not a question anyone asked.
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
