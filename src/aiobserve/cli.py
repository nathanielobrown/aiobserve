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

from dotenv import load_dotenv

from aiobserve.analyze.runner import QueryError, Result, run
from aiobserve.enrich.client import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MODEL,
    BatchClient,
    CliClient,
    preflight,
)
from aiobserve.enrich.cost import Prompt, estimate
from aiobserve.enrich.enricher import LEVELS, PlannedItem, enrich, plan
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.export.duckdb import DuckDbExporter, open_trace_store
from aiobserve.export.otlp import (
    BACKEND_NAMES,
    DEFAULT_MAX_CHARS,
    DEFAULT_RATE,
    ENDPOINT_ENV,
    GENERIC,
    ConfigurationError,
    OtlpExporter,
    TextPolicy,
    census,
    named_backend,
)
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.extract.store import StoreSource, UnknownProjectError
from aiobserve.pipeline import refresh
from aiobserve.sessions import DEFAULT_PROJECTS_ROOT, find_sessions, resolve_project
from aiobserve.view.app import PORT, serve

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
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"How many `claude` processes run at once (default: {DEFAULT_CONCURRENCY}). "
        "They spend the same 5-hour allowance this machine's own agents do",
    )

    otlp = subcommands.add_parser(
        "export-otlp", help="Ship the store's sessions to an OTLP backend as spans"
    )
    otlp.add_argument("project", type=Path, help="Path to the analyzed repository")
    otlp.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"The trace store (default: {DEFAULT_DB})"
    )
    otlp.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        default=GENERIC,
        help="Where to ship, and whose delivery ledger this run reads and writes "
        f"(default: {GENERIC}, configured by {ENDPOINT_ENV}). A named backend reads its own "
        f"key variable, and {ENDPOINT_ENV} overrides its endpoint",
    )
    otlp.add_argument(
        "--service-name",
        help="Send every session to this service instead of one named for its project directory",
    )
    otlp.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE,
        help=f"Spans per second, across the whole run (default: {DEFAULT_RATE:g})",
    )
    otlp.add_argument(
        "--include-text",
        action="store_true",
        help="Also send prompts, model text, tool arguments and results — untrusted "
        "transcript content, published to a third party",
    )
    otlp.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Characters kept per included text field (default: {DEFAULT_MAX_CHARS})",
    )
    otlp.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what a send would ship and send nothing. Needs no backend and no key",
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
    if args.command == "export-otlp":
        _export_otlp(args)
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


def build_client(model: str, *, concurrency: int) -> BatchClient:
    """The client an enrichment run calls: `claude -p`, that many processes at a time.

    The one place a real client is built, so a test can put a fake in its place.
    """
    return CliClient(model, concurrency=concurrency)


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
    # Before anything reads the store or renders a prompt: a run whose CLI cannot spend the
    # subscription fails now instead of on its first item. A dry run asks nothing — whoever
    # decides whether to pay for a pass is not always whoever is logged in.
    if not args.dry_run:
        preflight()
    project = str(resolve_project(args.project)) if args.project else None
    with EnrichmentStore(args.db) as store:
        if args.dry_run:
            _report_plan(plan(store, args.model, project=project, limit=args.limit), args.model)
            return
        client = build_client(args.model, concurrency=args.concurrency)
        report = enrich(store, client, project=project, limit=args.limit)
    print(f"{report.enriched} item(s) enriched, {report.swept} orphaned row(s) swept")


def _export_otlp(args: argparse.Namespace) -> None:
    """Ship every session of a project that this backend has not already confirmed."""
    load_dotenv()
    text = TextPolicy(include=args.include_text, max_chars=args.max_chars)
    # A project the store holds nothing under is a mistyped argument, whichever half of the
    # command reads it: worth a line an operator can act on rather than a traceback.
    try:
        if args.dry_run:
            _census_otlp(args, text)
            return
        # Before the store is opened: a run with nowhere to ship refuses now rather than after
        # reading a corpus.
        try:
            backend = named_backend(args.backend, os.environ)
        except ConfigurationError as error:
            raise SystemExit(str(error)) from error
        # One connection for both halves — DuckDB admits a single writer, and the exporter
        # needs to write its ledger into the store the source is reading.
        connection = open_trace_store(args.db, read_only=False)
        try:
            with OtlpExporter(
                backend, connection, service_name=args.service_name, text=text, rate=args.rate
            ) as exporter:
                result = refresh(args.project, extractor=StoreSource(connection), exporter=exporter)
        finally:
            connection.close()
    except UnknownProjectError as error:
        raise SystemExit(str(error)) from error
    print(f"{len(result.extracted)} session(s) exported, {len(result.skipped)} unchanged")


def _census_otlp(args: argparse.Namespace, text: TextPolicy) -> None:
    """Say what a send would ship, without a backend, a key, or the store's write lock."""
    connection = open_trace_store(args.db, read_only=True)
    try:
        source = StoreSource(connection)
        counts = census(
            (source.extract(session) for session in source.sessions(args.project)), text
        )
    finally:
        connection.close()
    print(f"{counts.sessions} session(s) and {counts.spans} span(s) would ship — nothing sent")


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
        f"at most ${quote.usd:.2f}: ~{quote.input_tokens:,} input and "
        f"~{quote.output_tokens:,} output tokens, counting no prompt caching"
    )


def _add_common_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    subcommand.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )
