"""What a page calls each field it prints. Registered as the Jinja global `label`.

A header is a column of the store read by a person, so the two names it carries answer to
different readers: the `data-field` beside every value stays the store's own column, and the
word above it is what someone says out loud. Closed on purpose — a header field with no entry
here raises rather than falling back to the column name, and `tests/view/test_app.py` checks
the registry against the facts the templates actually ask for.
"""

LABELS: dict[str, str] = {
    # What the thread was, and where it ran.
    "session_id": "Session",
    "run_id": "Run",
    "project_dir": "Project",
    "git_branch": "Branch",
    # Claude Code's own version string, which is what pins a schema fact (`docs/schema.md`).
    "version": "Version",
    "entrypoint": "Entrypoint",
    "skills": "Skills",
    "description": "Description",
    "model": "Model",
    "spawn_depth": "Depth",
    "is_fork": "Fork",
    # When it ran and for how long. Both spans print as a duration, so neither label names the
    # milliseconds the column holds.
    "started_at": "Started",
    "wall_ms": "Wall time",
    "active_ms": "Active time",
    # How much it did.
    "turns": "Turns",
    "api_calls": "API calls",
    "tool_calls": "Tool calls",
    "tool_errors": "Tool errors",
    "agent_runs": "Subagent runs",
    "compactions": "Compactions",
    # What it cost, and how much of that our price table could not price.
    "cost_usd": "Cost",
    "unpriced_api_calls": "Unpriced calls",
    "output_tokens": "Output tokens",
}


def label(name: str) -> str:
    """What a reader calls the field `name`. Raises for a field no page has named yet."""
    return LABELS[name]
