"""The `aiobserve` command."""

import argparse
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from aiobserve.enrich.batches import (
    DEFAULT_MODEL,
    AnthropicBatchClient,
    BatchClient,
    SyncClient,
)
from aiobserve.enrich.enricher import enrich, plan
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import refresh
from aiobserve.sessions import DEFAULT_PROJECTS_ROOT, find_sessions

# Gitignored, so an extract never lands in a commit.
DEFAULT_DB = Path("data") / "traces.duckdb"

# Read from `.env` or the environment, and validated before `enrich` reads anything else.
API_KEY = "ANTHROPIC_API_KEY"


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

    enrichment = subcommands.add_parser(
        "enrich", help="Describe the extracted sessions with an AI model"
    )
    enrichment.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"The trace store (default: {DEFAULT_DB})"
    )
    enrichment.add_argument(
        "--project", type=Path, help="Only enrich the sessions recorded for this repository"
    )
    enrichment.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"The model to describe with (default: {DEFAULT_MODEL})",
    )
    enrichment.add_argument(
        "--dry-run",
        action="store_true",
        help="Say what would be sent and stop, without writing or spending anything",
    )
    enrichment.add_argument("--limit", type=int, help="Send at most this many items")
    enrichment.add_argument(
        "--no-batch",
        action="store_true",
        help="Call the Messages API directly: minutes instead of hours, at full price",
    )

    args = parser.parse_args(argv or None)
    if args.command == "enrich":
        _enrich(args)
        return
    if args.command == "extract":
        extractor = ClaudeCodeExtractor(projects_root=args.projects_root)
        with DuckDbExporter(args.db) as exporter:
            result = refresh(args.project, extractor=extractor, exporter=exporter)
        print(f"{len(result.extracted)} session(s) extracted, {len(result.skipped)} unchanged")
        return
    for session in find_sessions(args.project, projects_root=args.projects_root):
        subagents = len(session.subagent_transcripts())
        print(f"{session.id}\t{subagents} subagent(s)\t{session.transcript}")


def build_client(model: str, *, batched: bool) -> BatchClient:
    """The client an enrichment run calls — batched for a corpus pass, direct for a dev run.

    The one place a real client is built, so a test can put a fake in its place.
    """
    # Reads the same key `_enrich` validated a moment ago, from the environment `load_dotenv`
    # populated.
    client = anthropic.Anthropic()
    if batched:
        return AnthropicBatchClient(client, model)
    return SyncClient(client, model)


def _enrich(args: argparse.Namespace) -> None:
    """Describe the store's stale items, or say what a run would send and stop."""
    # Before anything reads the store or renders a prompt: a run that would fail on its first
    # request should fail now instead.
    load_dotenv()
    if not os.environ.get(API_KEY, "").strip():
        raise SystemExit(f"{API_KEY} is unset or empty. Put it in .env or the environment")
    project = str(args.project.resolve()) if args.project else None
    with EnrichmentStore(args.db) as store:
        if args.dry_run:
            planned = plan(store, args.model, project=project, limit=args.limit)
            print(f"at most {len(planned)} item(s) would be sent to {args.model}")
            return
        client = build_client(args.model, batched=not args.no_batch)
        report = enrich(store, client, project=project, limit=args.limit)
    print(f"{report.enriched} item(s) enriched, {report.swept} orphaned row(s) swept")


def _add_common_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    subcommand.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )
