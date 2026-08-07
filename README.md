# aiobserve 🔭

Analyze AI coding agents from their own telemetry, to find ways to make them faster, cheaper, and better at the work.

Coding sessions leave a detailed trail: every tool call, every file read, every retry, every token. aiobserve extracts that trail — mostly as OpenTelemetry traces — turns it into something queryable, and produces findings you can act on: where time goes, where cost goes, which guidance the agent ignores, which tools it fumbles.

The first target is **Claude Code**. The tool takes a project path, so it works on any repository; `~/repos/mycelia` is just the first corpus.

## Status

Early. The repository carries its own AI-coding guidance and a Python skeleton; the extraction pipeline lands next, ported from `mac_settings/claude-otel/`.

## Getting started

```bash
mise run sync     # install the virtualenv from uv.lock
mise run check    # format, lint, type-check, test
```

Every task lives in `mise.toml`. `mise run check-fast` is the loop while you iterate.

## Where session data lives

Claude Code writes one JSON-per-line transcript per session:

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl
```

`<encoded-cwd>` is the session's working directory with each `/` replaced by `-`, so `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. A subagent's work is part of its session but recorded separately, so any accounting that ignores those files undercounts.

```bash
uv run aiobserve sessions ~/repos/mycelia
```

Field meanings go in `docs/schema.md`, each with the recorded session that established it — never from memory, because the harness changes these shapes without notice.

## Handling the data

A transcript records everything the analyzed agent read, including file contents and credentials. Raw extracts go in `data/` and telemetry keys in `.env`; both are gitignored, and neither is ever committed. Test fixtures are redacted excerpts trimmed to the records the test needs.

## For agents working in this repo

`CLAUDE.md` is the entry point. The house guides live in `docs/`, the rules and subagents in `.claude/`.
