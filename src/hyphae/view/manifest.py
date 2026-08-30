"""What each of the viewer's queries takes: one entry per `view_*.sql` file.

The `view_` family are library queries like any other — `hp query` runs them and a footer
cites them — and the viewer composes its sort, its filter and its paging around them rather
than embedding SQL of its own. What sets them apart is who owns their numbers: a width or a
row count is the surface's, named beside the ceiling that caps it in `view/bounds.py`, so
these entries sit here rather than in the analysis half.

They are read through the one registry `analyze/manifest.py` publishes, which is what the
runner binds against and what the smoke tier holds to the query directory. The SQL itself
lives with every other query file in `analyze/queries/`, and the parameter vocabulary these
entries are written in is `analyze/queries.py`.
"""

from hyphae.analyze.queries import (
    AFTER,
    API_CALL_ID,
    CHIP_CHARS_PARAM,
    CHUNK_CHARS,
    COMPACTION_ID,
    DETAIL_CHARS_PARAM,
    ENRICHMENT_CHARS,
    HEADER_CHARS,
    HEADER_ITEM_CHARS,
    HEADER_ITEMS,
    LIST_CATEGORIES,
    LIST_CHARS,
    LIST_ITEM_CHARS,
    LIST_PROJECTS,
    LOG_CHARS_PARAM,
    LOG_ROWS,
    MODEL_CHARS,
    NAV_CHARS_PARAM,
    NODE_ID,
    NODE_KIND,
    PAGE_ERRORS,
    PAGE_PROJECTS,
    PAGE_RECENT_DAYS,
    PAGE_RECORDS,
    PAGE_WINDOW_DAYS,
    RECORD_PREVIEW,
    REQUIRED,
    RUN_ID,
    SESSION_ID,
    SKIPPED,
    SOURCE,
    TAG_CHARS,
    TOOL_CALL_ID,
    TURN_ID,
    Param,
    ParamType,
    Query,
    Scope,
)

VIEW_QUERIES: dict[str, Query] = {
    "view_call_text": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    ),
    "view_call_thinking": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "api_call_id": API_CALL_ID},
    ),
    "view_call_tools": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "api_call_id": API_CALL_ID,
            "skipped": SKIPPED,
            "page_tools": Param(type=ParamType.INTEGER, default=LOG_ROWS),
            "log_chars": LOG_CHARS_PARAM,
        },
    ),
    "view_call_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "api_call_id": API_CALL_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_described_sessions": Query(
        scope=Scope.KEYED,
        # What a list row shows of a session's enrichment. The description takes a row's head
        # rather than a page's, because the list multiplies its row by the size of the page,
        # and the work cell is cut here rather than in the composition: nothing filters on it.
        params={
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "tag_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            "kind_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            "head_kinds": Param(type=ParamType.INTEGER, default=LIST_CATEGORIES),
        },
    ),
    "view_enrichment": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Which thread's turns to describe. Required like every other source: the turn
            # keys are `(session, source, turn)`, so a default would silently answer for the
            # main thread on a run's page.
            "source": SOURCE,
            "description_chars": Param(type=ParamType.INTEGER, default=ENRICHMENT_CHARS),
            "tag_chars": Param(type=ParamType.INTEGER, default=TAG_CHARS),
            # The width the model's own name is cut to: a header's, not a tag's — a model
            # string is longer than a taxonomy word and shorter than a sentence.
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
        },
    ),
    # The whole of what that pass wrote, one item at a time: the three levels the pane shows,
    # each keyed the way its own table is. The fetch behind a description or a friction line
    # the pane had to cut.
    "view_turn_said": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "turn_id": TURN_ID},
    ),
    "view_run_said": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID, "run_id": RUN_ID}),
    "view_session_said": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID}),
    "view_compactions": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "chip_chars": CHIP_CHARS_PARAM},
    ),
    "view_run_header": Query(
        scope=Scope.KEYED,
        # The run's id is also the source its rows carry, so one key answers both questions.
        params={
            "session_id": SESSION_ID,
            "run_id": RUN_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_run_brief": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID, "run_id": RUN_ID}),
    # The two a run reads off the call that spawned it, keyed the same way the brief is.
    "view_run_prompt": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "run_id": RUN_ID}
    ),
    "view_run_result": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "run_id": RUN_ID}
    ),
    # The numbers behind one node's row, which the NavTree draws as a bar and a badge. One query
    # for every kind that is made of api calls, keyed by the kind as well as the id; the tool
    # call, which is made of none, has its own.
    "view_numbers": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "node_id": NODE_ID,
            "kind": NODE_KIND,
            "model_chars": Param(type=ParamType.INTEGER, default=MODEL_CHARS),
        },
    ),
    # And the compaction, which is made of no api calls either — what it has is the window it
    # dropped, off the boundary record itself.
    "view_numbers_compaction": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "compaction_id": COMPACTION_ID,
            "chip_chars": CHIP_CHARS_PARAM,
        },
    ),
    "view_numbers_tool": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "tool_call_id": TOOL_CALL_ID,
            "item_chars": Param(type=ParamType.INTEGER, default=HEADER_ITEM_CHARS),
            "head_items": Param(type=ParamType.INTEGER, default=HEADER_ITEMS),
        },
    ),
    "view_offload": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Which file. Required for the same reason the session is: a default would pick
            # some session's offloaded tool output at random.
            "name": Param(type=ParamType.TEXT, default=REQUIRED),
            "after_chars": Param(type=ParamType.INTEGER, default=0),
            "chunk_chars": Param(type=ParamType.INTEGER, default=CHUNK_CHARS),
        },
    ),
    "view_project_rollups": Query(
        scope=Scope.KEYED,
        params={
            # The clock both windows are measured back from. No default: a landing page's
            # "last 7 days" is only reproducible if the day it counted from is bound and
            # cited, and SQL's own clock would answer something else tomorrow.
            "as_of": Param(type=ParamType.DATE, default=REQUIRED),
            "recent_days": Param(type=ParamType.INTEGER, default=PAGE_RECENT_DAYS),
            "window_days": Param(type=ParamType.INTEGER, default=PAGE_WINDOW_DAYS),
            # A project path takes a row's head, like the list's — and the row links by the
            # whole path, so the head is what the page shows and not what it filters by.
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "projects": Param(type=ParamType.INTEGER, default=PAGE_PROJECTS),
        },
    ),
    "view_projects": Query(
        scope=Scope.KEYED,
        params={
            "head_chars": Param(type=ParamType.INTEGER, default=LIST_CHARS),
            "head_projects": Param(type=ParamType.INTEGER, default=LIST_PROJECTS),
        },
    ),
    "view_record": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Which line. A key like the two above: "some record of this thread" is not a
            # question anyone asked, and the answer would be private transcript either way.
            "line_no": Param(type=ParamType.INTEGER, default=REQUIRED),
        },
    ),
    "view_records": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "after": AFTER,
            "page_records": Param(type=ParamType.INTEGER, default=PAGE_RECORDS),
            "preview_chars": Param(type=ParamType.INTEGER, default=RECORD_PREVIEW),
        },
    ),
    "view_runs": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "chip_chars": CHIP_CHARS_PARAM}
    ),
    "view_session_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "item_chars": Param(type=ParamType.INTEGER, default=HEADER_ITEM_CHARS),
            "head_items": Param(type=ParamType.INTEGER, default=HEADER_ITEMS),
        },
    ),
    "view_session_errors": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Labelled at a NavTree row's width, because the rows link to nodes: a failure reads
            # as the same line here as it does in the NavTree beside its own page.
            "nav_chars": NAV_CHARS_PARAM,
            "errors": Param(type=ParamType.INTEGER, default=PAGE_ERRORS),
        },
    ),
    "view_sessions": Query(
        scope=Scope.KEYED,
        # How much of each agent definition's name a row's list carries. The viewer composes
        # the rest of a row's cuts around this query (`view/store.py`) because its filters
        # read whole values; nothing filters on this one, so it is cut in the file.
        params={"item_chars": Param(type=ParamType.INTEGER, default=LIST_ITEM_CHARS)},
    ),
    "view_tool_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "tool_call_id": TOOL_CALL_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_tool_command": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    ),
    "view_tool_input": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "tool_call_id": TOOL_CALL_ID},
    ),
    "view_tool_result": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "tool_call_id": TOOL_CALL_ID,
            # Not a width the answer is cut to — the value rides whole — but the bound on the
            # file suffix beside it, which says what the value is written in.
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
        },
    ),
    "view_nav_tree_calls": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # NULL is the real question "which calls sit under no turn", so the key is
            # required rather than defaulted: absence cannot stand in for it.
            "turn_id": TURN_ID,
            "nav_chars": NAV_CHARS_PARAM,
        },
    ),
    "view_nav_tree_tools": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Both NULL-able and both required for the same reason as `view_nav_tree_calls`:
            # NULL is the question "under this turn, whichever call made it" at the first and
            # "under no turn of this thread" at the second, not a key left out.
            "api_call_id": API_CALL_ID,
            "turn_id": TURN_ID,
            "nav_chars": NAV_CHARS_PARAM,
        },
    ),
    "view_nav_tree_turns": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "nav_chars": NAV_CHARS_PARAM},
    ),
    "view_turn_calls": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # NULL is the real question "which calls sit under no turn", so the key is
            # required rather than defaulted: absence cannot stand in for it.
            "turn_id": TURN_ID,
            "skipped": SKIPPED,
            "page_calls": Param(type=ParamType.INTEGER, default=LOG_ROWS),
            # The two model names a call row shows, cut like every other log row's strings.
            "log_chars": LOG_CHARS_PARAM,
        },
    ),
    "view_turn_command_args": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "turn_id": TURN_ID,
        },
    ),
    "view_turn_header": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Which turn. A key like the session and the thread: "some turn of this thread"
            # is not a question anyone asked.
            "turn_id": TURN_ID,
            "head_chars": Param(type=ParamType.INTEGER, default=HEADER_CHARS),
            "detail_chars": DETAIL_CHARS_PARAM,
        },
    ),
    "view_turn_prompt": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            "turn_id": TURN_ID,
        },
    ),
    "view_turn_records": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "source": SOURCE}
    ),
}
