"""The `aiobserve` command."""

import argparse
import csv
import datetime as dt
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

from aiobserve.analyze.runner import QueryError, Result, run
from aiobserve.enrich.batches import (
    DEFAULT_MODEL,
    AnthropicBatchClient,
    BatchClient,
    SyncClient,
)
from aiobserve.enrich.cost import Prompt, estimate
from aiobserve.enrich.enricher import LEVELS, PlannedItem, enrich, plan
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import refresh
from aiobserve.sessions import DEFAULT_PROJECTS_ROOT, find_sessions
from aiobserve.view.app import PORT, serve

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
        help="Say what would be sent and stop, spending nothing "
        "(creates the empty enrichment tables if absent)",
    )
    enrichment.add_argument("--limit", type=int, help="Send at most this many items")
    enrichment.add_argument(
        "--no-batch",
        action="store_true",
        help="Call the Messages API directly: minutes instead of hours, at full price",
    )

    library = subcommands.add_parser("query", help="Run a library query against the trace store")
    library.add_argument("name", help="The query to run — a file in analyze/queries/")
    library.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"The trace store (default: {DEFAULT_DB})"
    )
    library.add_argument(
        "--project", type=Path, help="The analyzed repository — required by a corpus query"
    )
    library.add_argument(
        "--since",
        type=dt.date.fromisoformat,
        help="Only count sessions started on or after this date (default: the whole corpus)",
    )
    library.add_argument(
        "--as-of",
        type=dt.date.fromisoformat,
        default=dt.date.today(),
        help="The date the trailing window is measured back from (default: today)",
    )
    library.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Bind one of the query's parameters, overriding its production default",
    )
    library.add_argument(
        "--csv", action="store_true", help="Write CSV to stdout, commentary to stderr"
    )

    viewer = subcommands.add_parser("view", help="Read the trace store in a local web viewer")
    viewer.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"The trace store (default: {DEFAULT_DB})"
    )
    viewer.add_argument(
        "--port", type=int, default=PORT, help=f"The port to serve on (default: {PORT})"
    )
    viewer.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser on startup"
    )

    args = parser.parse_args(argv or None)
    if args.command == "view":
        serve(args.db, args.port, open_browser=not args.no_browser)
        return
    if args.command == "query":
        _query(args)
        return
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


def _query(args: argparse.Namespace) -> None:
    """Run one library query and print its citation, its commentary, and its rows."""
    try:
        params = dict(pair.split("=", 1) for pair in args.param)
    except ValueError:
        raise SystemExit("--param takes KEY=VALUE") from None
    try:
        result = run(
            args.db,
            args.name,
            project=args.project,
            since=args.since,
            as_of=args.as_of,
            params=params,
        )
    except QueryError as error:
        raise SystemExit(str(error)) from error
    # The count the corpus predicate could not place, and the citation under `--csv`, go to
    # stderr: a piped analysis reads stdout, and a line of prose in it breaks silently.
    if result.unplaceable_sessions is not None:
        print(
            f"excluded {result.unplaceable_sessions} session(s) with no project_dir",
            file=sys.stderr,
        )
    print(result.citation, file=sys.stderr if args.csv else sys.stdout)
    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(result.columns)
        writer.writerows(result.rows)
        return
    print(_table(result))


def _table(result: Result) -> str:
    """The rows as an aligned table, wide enough for the values it holds."""
    cells = [[_cell(value) for value in row] for row in result.rows]
    widths = [
        max(len(column), *(len(row[index]) for row in cells)) if cells else len(column)
        for index, column in enumerate(result.columns)
    ]
    lines = [
        "  ".join(column.ljust(width) for column, width in zip(result.columns, widths, strict=True))
    ]
    lines.append("  ".join("-" * width for width in widths))
    lines += [
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        for row in cells
    ]
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _enrich(args: argparse.Namespace) -> None:
    """Describe the store's stale items, or say what a run would send and stop."""
    load_dotenv()
    # Before anything reads the store or renders a prompt: a run that would fail on its first
    # request fails now instead. A dry run makes no request, so it needs no key — whoever
    # decides whether to pay for a pass is not always whoever holds the key.
    if not args.dry_run and not os.environ.get(API_KEY, "").strip():
        raise SystemExit(f"{API_KEY} is unset or empty. Put it in .env or the environment")
    project = str(args.project.resolve()) if args.project else None
    with EnrichmentStore(args.db) as store:
        if args.dry_run:
            _report_plan(plan(store, args.model, project=project, limit=args.limit), args.model)
            return
        client = build_client(args.model, batched=not args.no_batch)
        report = enrich(store, client, project=project, limit=args.limit)
    print(f"{report.enriched} item(s) enriched, {report.swept} orphaned row(s) swept")


def _report_plan(planned: Sequence[PlannedItem], model: str) -> None:
    """Say what a run would send and what it would cost, per level and in total.

    Every count here is an upper bound: the plan holds each stale item and every item whose
    prompt embeds one, and a child re-described in the same words stops that cascade.
    """
    quote = estimate([Prompt(entry.item.level, entry.rendered) for entry in planned], model)
    counts = Counter(entry.item.level for entry in planned)
    breakdown = ", ".join(f"{counts[level]} {level}" for level in LEVELS)
    print(f"at most {quote.items} item(s) would be sent to {model} — {breakdown}")
    print(
        f"at most ${quote.batched_usd:.2f} batched (${quote.unbatched_usd:.2f} with --no-batch): "
        f"~{quote.input_tokens:,} input and ~{quote.output_tokens:,} output tokens, "
        "counting no prompt caching"
    )


def _add_common_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    subcommand.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )
