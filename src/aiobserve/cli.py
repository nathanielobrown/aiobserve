"""The `aiobserve` command."""

import argparse
from pathlib import Path

from aiobserve.sessions import DEFAULT_PROJECTS_ROOT, find_sessions


def main() -> None:
    parser = argparse.ArgumentParser(prog="aiobserve", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    sessions = subcommands.add_parser("sessions", help="List the sessions recorded for a project")
    sessions.add_argument("project", type=Path, help="Path to the analyzed repository")
    sessions.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )

    args = parser.parse_args()
    # One subcommand today, and argparse already rejected anything else.
    for session in find_sessions(args.project, projects_root=args.projects_root):
        subagents = len(session.subagent_transcripts())
        print(f"{session.id}\t{subagents} subagent(s)\t{session.transcript}")
