"""What each query in the library takes: one manifest entry per `.sql` file.

Production code, not a test table. Every parameter a query declares gets either a production
default — the value a bare invocation runs, and the value a committed report quotes — or
`REQUIRED`, for a choice the caller has to make: a defaulted line range on `records_slice`
would quietly hand back a window of raw transcript instead of an error.

Adding a query means adding its file *and* its entry here; the smoke tier fails on either half
alone. The parameter vocabulary the entries are written in — the types, the widths, and the
shared `Param`s — is `analyze/queries.py`.
"""

from hyphae.analyze.queries import (
    AFTER,
    API_CALL_ID,
    CHIP_CHARS_PARAM,
    CHUNK_CHARS,
    COMMAND_HEAD_CHARS,
    DETAIL_CHARS_PARAM,
    DRAW_SEED,
    ENRICHMENT_CHARS,
    ERROR_CHARS,
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
    RAW_CHARS,
    RECORD_PREVIEW,
    REQUIRED,
    RUN_ID,
    SESSION_ID,
    SIGNATURE_CHARS,
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

QUERIES: dict[str, Query] = {
    "agent_compactions": Query(scope=Scope.CORPUS, params={}),
    "agent_types": Query(scope=Scope.CORPUS, params={}),
    "co_occurrence": Query(
        scope=Scope.CORPUS,
        # A pair seen in one or two sessions is noise on any corpus worth counting. The floor
        # is bound rather than fixed because a young corpus has nothing above it.
        params={"min_sessions": Param(type=ParamType.INTEGER, default=3)},
    ),
    "context_reloads": Query(
        scope=Scope.CORPUS,
        params={
            # What a call has to write before it counts as starting over, and how much of
            # what it sent that has to be. The share is the detector; the floor only keeps
            # trivia out. Both are tuned in the query's header against the mycelia corpus.
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
            # The gap that makes a miss explainable: Claude Code's default cache entry lives
            # 5 minutes, so a thread idle that long had no cache left to hit.
            "idle_seconds": Param(type=ParamType.INTEGER, default=300),
        },
    ),
    "idle_gaps": Query(
        scope=Scope.CORPUS,
        params={
            # Shortest silence worth a row. 300 seconds is Claude Code's default cache
            # lifetime — below it nothing had expired — and it is `context_reloads`'s
            # `idle_seconds`, so the two queries call the same waits idle.
            "min_idle_seconds": Param(type=ParamType.INTEGER, default=300),
            # The reload detector, at `context_reloads`'s production values: the `reloaded`
            # column has to mean what that query's counts mean.
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
        },
    ),
    "reload_cost_split": Query(
        scope=Scope.CORPUS,
        params={
            # Where the split falls. No default: the bound is the claim the query makes, and
            # it moves with the pricing table a break-even was computed from and with the
            # cache lifetime a wait was racing. A defaulted one would be quoted as ours.
            "short_gap_seconds": Param(type=ParamType.INTEGER, default=REQUIRED),
            # The floor and the detector, at `idle_gaps`'s values: this splits that query's
            # population, so it has to admit and flag the same silences.
            "min_idle_seconds": Param(type=ParamType.INTEGER, default=300),
            "min_rebuilt_tokens": Param(type=ParamType.INTEGER, default=20_000),
            "min_rebuilt_pct": Param(type=ParamType.INTEGER, default=90),
        },
    ),
    "command_failures": Query(
        scope=Scope.CORPUS,
        params={
            # Keep only command lines holding this text. NULL — every command — is the survey;
            # binding it is how a command buried in a pipeline gets counted at all.
            "mentions": Param(type=ParamType.TEXT, default=None),
            # Calls a shape needs to be listed, matching the other floors in this file.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            "head_chars": Param(type=ParamType.INTEGER, default=COMMAND_HEAD_CHARS),
            "signature_chars": Param(type=ParamType.INTEGER, default=SIGNATURE_CHARS),
        },
    ),
    "cost_distribution": Query(scope=Scope.CORPUS, params={}),
    # The enrichment family reads tables an enrichment pass writes (`docs/enrichment.md`). A
    # store no pass has touched does not hold them, and these queries fail on it saying so.
    "enrichment_coverage": Query(scope=Scope.CORPUS, params={}),
    "enrichment_digest": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # One level, or NULL for all three. A real default, not a missing key: the sheet
            # a reader opens first is the whole session, at every level it was described at.
            "level": Param(type=ParamType.TEXT, default=None),
        },
    ),
    "error_records": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            # Every thread of the session unless the caller names one. Unlike the keys above,
            # a sensible default exists and it is the question readers actually ask: where in
            # this session did anything fail?
            "source": Param(type=ParamType.TEXT, default=None),
            "max_chars": Param(type=ParamType.INTEGER, default=ERROR_CHARS),
        },
    ),
    "error_signatures": Query(
        scope=Scope.CORPUS,
        params={
            # Count a phrase wherever it sits in the error text, instead of grouping by the
            # first line. NULL — group everything — is the survey a reader runs first.
            "signature": Param(type=ParamType.TEXT, default=None),
            # Occurrences a signature needs to be listed. Five, matching the other floors in
            # this file, and for the same reason: below it a group is one session's accident.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            "signature_chars": Param(type=ParamType.INTEGER, default=SIGNATURE_CHARS),
        },
    ),
    "missing_file_recovery": Query(
        scope=Scope.CORPUS,
        params={
            # Calls after the failure that count as the recovery. One, because the claim is
            # about what the thread did *next*: a listing three calls later is as likely to
            # be answering the question after it.
            "within_calls": Param(type=ParamType.INTEGER, default=1),
            # Keep only failures whose text holds this phrase — "does not exist" narrows the
            # population to the ones a listing could have prevented. NULL is every failed call
            # that named a path, which is the survey a reader runs first.
            "missing": Param(type=ParamType.TEXT, default=None),
        },
    ),
    "path_failures": Query(
        scope=Scope.CORPUS,
        params={
            # Failures a directory needs to be listed, matching the other floors in this file.
            "min_occurrences": Param(type=ParamType.INTEGER, default=5),
            # Path segments the group key keeps. One is the aggregating default: it is what
            # makes a directory count the same number whichever copy of the repository the
            # call reached into, which is the whole point of grouping paths this way.
            "tail_segments": Param(type=ParamType.INTEGER, default=1),
        },
    ),
    "records_slice": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # The line range is required for the same reason the cap exists: a default would
            # hand back a window of private transcript with nothing to say it was a guess.
            "first_line": Param(type=ParamType.INTEGER, default=REQUIRED),
            "last_line": Param(type=ParamType.INTEGER, default=REQUIRED),
            "max_chars": Param(type=ParamType.INTEGER, default=RAW_CHARS),
        },
    ),
    "run_timeline": Query(
        scope=Scope.KEYED,
        params={"session_id": SESSION_ID, "source": SOURCE, "log_chars": LOG_CHARS_PARAM},
    ),
    "select_runs": Query(
        scope=Scope.CORPUS,
        params={
            # How many runs each `agent_type` gives up per stratum. One apiece keeps the draw
            # at roughly two runs per definition, which is the reading budget the design
            # sized.
            "runs_per_stratum": Param(type=ParamType.INTEGER, default=1),
            # In-window runs an `agent_type` needs before it earns a reading slot. Matches
            # `select_sessions`'s skill threshold, and for the same reason: both sets are
            # open, and a name used once is a session's invention, not a definition.
            "min_runs": Param(type=ParamType.INTEGER, default=5),
        },
    ),
    "select_enrichments": Query(
        scope=Scope.CORPUS,
        params={
            # Which level to check. No default: the three are different populations — 2,500
            # runs, 1,400 turns, 470 sessions — and a draw over "some level" answers nobody.
            "level": Param(type=ParamType.TEXT, default=REQUIRED),
            # Items per category. Two apiece over a fourteen-member taxonomy is a sitting's
            # worth of reading, and every member gets a reader.
            "per_category": Param(type=ParamType.INTEGER, default=2),
            "seed": DRAW_SEED,
        },
    ),
    "select_sessions": Query(
        scope=Scope.CORPUS,
        params={
            "cost_quota": Param(type=ParamType.INTEGER, default=8),
            "error_quota": Param(type=ParamType.INTEGER, default=5),
            "compaction_quota": Param(type=ParamType.INTEGER, default=4),
            "discovery_quota": Param(type=ParamType.INTEGER, default=8),
            # A skill is major when this many in-window sessions used it.
            "skill_threshold": Param(type=ParamType.INTEGER, default=5),
            "seed": DRAW_SEED,
            # Api calls a session needs to be in the pool at all. One keeps out the
            # `/model`-only sessions that took three of iteration 1's eight discovery draws;
            # it is bound rather than fixed because the filter is part of what the draw
            # claims, and a citation that omits it describes a pool nobody can reconstruct.
            "min_api_calls": Param(type=ParamType.INTEGER, default=1),
            # Api calls a session needs on top of that before *discovery* will draw it. A
            # ranked stratum is exempt: what it ranks on is the reason to read the session.
            # Ten sits in the gap the corpus itself leaves — of the 117 in-window pool
            # sessions on 2026-08-13, 47 made between 1 and 9 calls and the next made 12 —
            # and it is what half of iteration 3's discovery draw fell below.
            "min_discovery_api_calls": Param(type=ParamType.INTEGER, default=10),
        },
    ),
    "session_counts": Query(scope=Scope.CORPUS, params={}),
    "session_timeline": Query(
        scope=Scope.KEYED, params={"session_id": SESSION_ID, "log_chars": LOG_CHARS_PARAM}
    ),
    "session_overview": Query(scope=Scope.KEYED, params={"session_id": SESSION_ID}),
    "session_shapes": Query(
        scope=Scope.CORPUS,
        # The classifier's cut points. Every one is a starting guess, which is why they are
        # bound: a shape that swallows half the corpus is a threshold to move, not a finding.
        params={
            # Share of a session's api calls one skill has to carry to own the session. A
            # percentage, because a bound parameter is an integer, a date, or text.
            "skill_share_pct": Param(type=ParamType.INTEGER, default=50),
            "delegating_runs": Param(type=ParamType.INTEGER, default=3),
            "editing_calls": Param(type=ParamType.INTEGER, default=5),
            # Below this a session is conversational; at or above it with no edits it is
            # analysis. One threshold, so the two shapes cannot overlap or leave a gap.
            "busy_tool_calls": Param(type=ParamType.INTEGER, default=5),
        },
    ),
    "sessions": Query(scope=Scope.CORPUS, params={}),
    "skill_activity": Query(scope=Scope.CORPUS, params={}),
    "slash_commands": Query(scope=Scope.CORPUS, params={}),
    "tool_failures": Query(scope=Scope.CORPUS, params={}),
    # The `view_` family belongs to the trace viewer (`plans/trace-viewer/design.md`). They
    # are library queries like any other — runnable and citable — and the viewer composes
    # sort and filter around them rather than embedding SQL of its own.
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
    # The numbers behind one node's row, which the tree draws as a bar and a badge. One query
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
            # Labelled at a tree row's width, because the rows link to nodes: a failure reads
            # as the same line here as it does in the tree beside its own page.
            "nav_chars": NAV_CHARS_PARAM,
            "errors": Param(type=ParamType.INTEGER, default=PAGE_ERRORS),
        },
    ),
    "view_sessions": Query(
        scope=Scope.KEYED,
        # How much of each agent definition's name a row's list carries. The viewer composes
        # the rest of a row's cuts around this query (`view/listing.py`) because its filters
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
    "view_tree_calls": Query(
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
    "view_tree_tools": Query(
        scope=Scope.KEYED,
        params={
            "session_id": SESSION_ID,
            "source": SOURCE,
            # Both NULL-able and both required for the same reason as `view_tree_calls`:
            # NULL is the question "under this turn, whichever call made it" at the first and
            # "under no turn of this thread" at the second, not a key left out.
            "api_call_id": API_CALL_ID,
            "turn_id": TURN_ID,
            "nav_chars": NAV_CHARS_PARAM,
        },
    ),
    "view_tree_turns": Query(
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
    "weekly_trend": Query(scope=Scope.CORPUS, params={}),
}
