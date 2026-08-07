"""The `aiobserve` command."""

import argparse
from pathlib import Path

from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import refresh
from aiobserve.sessions import DEFAULT_PROJECTS_ROOT, find_sessions

# Gitignored, so an extract never lands in a commit.
DEFAULT_DB = Path("data") / "traces.duckdb"


def main(*argv: str) -> None:
    parser = argparse.ArgumentParser(prog="aiobserve", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    sessions = subcommands.add_parser("sessions", help="List the sessions recorded for a project")
    _add_common_arguments(sessions)

    extract = subcommands.add_parser("extract", help="Extract a project's sessions into DuckDB")
    _add_common_arguments(extract)
    extract.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Where to write the trace store (default: {DEFAULT_DB})",
    )

    args = parser.parse_args(argv or None)
    if args.command == "extract":
        extractor = ClaudeCodeExtractor(projects_root=args.projects_root)
        with DuckDbExporter(args.db) as exporter:
            result = refresh(args.project, extractor=extractor, exporter=exporter)
        print(f"{len(result.extracted)} session(s) extracted, {len(result.skipped)} unchanged")
        return
    for session in find_sessions(args.project, projects_root=args.projects_root):
        subagents = len(session.subagent_transcripts())
        print(f"{session.id}\t{subagents} subagent(s)\t{session.transcript}")


def _add_common_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    subcommand.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )
