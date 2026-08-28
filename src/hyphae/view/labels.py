"""What a page calls each field it prints. Registered as the Jinja global `label`.

A header is a column of the store read by a person, so the two names it carries answer to
different readers: the `data-field` beside every value stays the store's own column, and the
word above it is what someone says out loud. Closed on purpose — a header field with no entry
here raises rather than falling back to the column name, and `tests/view/test_app__headers.py`
checks the registry against the facts the templates and the panes actually ask for.
"""

LABELS: dict[str, str] = {
    # What the thread was, and where it ran.
    "session_id": "Session",
    "run_id": "Run",
    "git_branch": "Branch",
    # Claude Code's own version string, which is what pins a schema fact (`docs/schema.md`).
    "version": "Version",
    "entrypoint": "Entrypoint",
    # What the spawning agent typed in the Agent tool's `description`, which is the brief the
    # run was given rather than a description of what it did.
    "brief": "Task brief",
    "agent_type": "Agent",
    "model": "Model",
    # The model a fallback replaced, present only on a call that fell back.
    "fallback_from": "Fell back from",
    "effort": "Effort",
    "stop_reason": "Stop reason",
    "attribution_skill": "Skill",
    "spawn_depth": "Depth",
    "is_fork": "Fork",
    # When it ran and for how long. Both spans print as a duration, so neither label names the
    # milliseconds the column holds.
    "started_at": "Started",
    "wall_ms": "Wall time",
    "active_ms": "Active time",
    # How much it did.
    "turns": "Turns",
    # Where one turn sits in its thread, which is what a node page is keyed by.
    "turn_index": "Turn",
    "api_calls": "API calls",
    "tool_calls": "Tool calls",
    # The tool calls one api call made, named rather than counted.
    "tool_titles": "Tools",
    "tool_errors": "Tool errors",
    "agent_runs": "Subagent runs",
    "compactions": "Compactions",
    # What it cost, and how much of that our price table could not price.
    "cost_usd": "Cost",
    "unpriced_api_calls": "Unpriced calls",
    "input_tokens": "Input tokens",
    "output_tokens": "Output tokens",
    "cache_read_tokens": "Cache read",
    "cache_creation_tokens": "Cache written",
    # The skills a session loaded, cut in SQL and counted by the pane.
    "skills": "Skills",
    # Where one call and one tool call sit in the thread that made them.
    "call_index": "Call",
    "tool_index": "Tool call",
    "name": "Tool",
    "server_side": "Server-side",
    "is_error": "Error",
    "incomplete": "Incomplete",
    # A replayed turn is one a resume re-read, not one the model answered again.
    "replayed": "Replayed",
    # The command a slash turn ran needs no label — a pane leads with it, in the form it was
    # typed — but what followed it is previewed like any other value, under this heading.
    "command_args": "Command arguments",
    # What a compaction was, when it ran, and what it cost the thread's context.
    "trigger": "Trigger",
    "timestamp": "At",
    "pre_tokens": "Tokens before",
    "post_tokens": "Tokens after",
    "duration_ms": "Took",
    # The fat columns a pane previews, each with its own way to the whole of it, and the
    # lengths a children log prints in their place — a row says how much was said, the page
    # under it says what.
    "prompt": "Prompt",
    "text": "Said",
    "text_chars": "Said (chars)",
    "thinking": "Thought",
    "input": "Arguments",
    # What a `Bash` call ran, lifted out of its arguments so a reader meets the shell first.
    "command": "Command",
    "result": "Result",
    "result_chars": "Result (chars)",
    # And the two lines an enrichment pass wrote, which the pane previews like any other value
    # a fetch stands behind. Neither prints under a heading — the glyph beside them says who
    # wrote both — so the words are here for the registry to stay closed over what the panes
    # preview rather than for a reader.
    "description": "Description",
    "friction": "Friction",
    # The two columns a children log prints that no query returns. `title` is what the viewer
    # calls a node in the most readable form the record supports (`docs/viewer-titles.md`), and
    # `body` is the column holding the control that opens one under its row.
    "title": "Title",
    "body": "Body",
}


def label(name: str) -> str:
    """What a reader calls the field `name`. Raises for a field no page has named yet."""
    return LABELS[name]
