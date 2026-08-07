# Claude Code project config

Rationale for `settings.json` entries that have no code home (JSON takes no comments; each hook's why lives in its script's header under `hooks/`).

- `permissions.deny` — `Read(./.env)` and `Read(./data/**)`: the first holds telemetry backend ingest keys, the second raw extracted sessions, which carry whatever the analyzed agent read. Neither belongs in a session's context, and a denied read is cheaper than a leaked one
- `skillOverrides` — trims built-in skills that don't apply to a Python analysis repo. `name-only` keeps the skill invocable but drops its body from context; `off` removes it
- `agents/telemetry.md` declares an `mcpServers` entry that this repo does not configure. Point it at your backend's MCP server in `~/.claude.json` (or a local `.mcp.json`) before dispatching that agent — see the agent's own header
