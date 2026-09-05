"""The `hp` command."""

import argparse
import csv
import datetime as dt
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from dotenv import load_dotenv

from hyphae.analyze.manifest import catalog
from hyphae.analyze.queries import REQUIRED, QueryError
from hyphae.analyze.runner import Result, run
from hyphae.enrich.client import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MODEL,
    BatchClient,
    CliClient,
    preflight,
)
from hyphae.enrich.cost import Prompt, estimate
from hyphae.enrich.enricher import PlannedItem, enrich, plan
from hyphae.enrich.levels import ROUND_ORDER
from hyphae.enrich.stamp import Versions
from hyphae.enrich.store import EnrichmentStore
from hyphae.export.duckdb import CLI_WAIT, DuckDbExporter, open_trace_store
from hyphae.export.otlp import DEFAULT_MAX_CHARS, Census, TextPolicy
from hyphae.export.otlp_delivery import (
    BACKEND_NAMES,
    DEFAULT_RATE,
    ENDPOINT_ENV,
    GENERIC,
    Backend,
    ConfigurationError,
    DeliveryLedger,
    OtlpCensus,
    OtlpExporter,
    named_backend,
)
from hyphae.extract.claude_code import ClaudeCodeExtractor
from hyphae.extract.layout import DEFAULT_PROJECTS_ROOT, find_sessions
from hyphae.extract.pricing import MODELS, SYNTHETIC_MODEL
from hyphae.extract.store import StoreSource, UnknownProjectError
from hyphae.pipeline import refresh
from hyphae.projects import resolve_project
from hyphae.view.app import PORT, serve

# Gitignored, so an extract never lands in a commit.
DEFAULT_DB = Path("data") / "traces.duckdb"


class Subcommand(NamedTuple):
    """One `hp` subcommand: what it is for, what it takes, and what runs it."""

    help: str
    # Declares the subcommand's own arguments on the parser it is handed.
    arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], None]


def build_parser() -> argparse.ArgumentParser:
    """The whole command line, built from the table at the bottom of this file.

    Public so a test can read the surface `main` dispatches on without running anything.
    """
    parser = argparse.ArgumentParser(prog="hp", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name, subcommand in SUBCOMMANDS.items():
        subcommand.arguments(subcommands.add_parser(name, help=subcommand.help))
    return parser


def main(*argv: str) -> None:
    args = build_parser().parse_args(argv or None)
    SUBCOMMANDS[args.command].run(args)


def build_client(model: str, *, concurrency: int) -> BatchClient:
    """The client an enrichment run calls: `claude -p`, that many processes at a time.

    The one place a real client is built, so a test can put a fake in its place.
    """
    return CliClient(model, concurrency=concurrency)


def _sessions(args: argparse.Namespace) -> None:
    """List a project's transcripts on disk, with the subagents each session spawned."""
    for session in find_sessions(args.project, projects_root=args.projects_root):
        subagents = len(session.subagent_transcripts())
        print(f"{session.id}\t{subagents} subagent(s)\t{session.transcript}")


def _extract(args: argparse.Namespace) -> None:
    """Parse a project's transcripts into the trace store, skipping what has not changed."""
    extractor = ClaudeCodeExtractor(projects_root=args.projects_root)
    exporter = DuckDbExporter(args.db, wait=CLI_WAIT)
    result = refresh(args.project, extractor=extractor, exporter=exporter)
    print(f"{len(result.extracted)} session(s) extracted, {len(result.skipped)} unchanged")
    # A field no model declares is news, not a failure: the archive kept it either way, and the
    # exit code stays 0. Silence means the models still describe what Claude Code writes.
    report = extractor.unknown_fields.report()
    if report:
        print(f"Fields no model declares:\n{report}")


def _extract_arguments(subcommand: argparse.ArgumentParser) -> None:
    _add_discovery_arguments(subcommand)
    _add_db_argument(subcommand, "Where to write the trace store")


def _view(args: argparse.Namespace) -> None:
    """Serve the store in a local browser until interrupted."""
    serve(args.db, args.port, open_browser=not args.no_browser, dev=args.dev)


def _view_arguments(subcommand: argparse.ArgumentParser) -> None:
    _add_db_argument(subcommand, "The trace store")
    subcommand.add_argument(
        "--port", type=int, default=PORT, help=f"The port to serve on (default: {PORT})"
    )
    subcommand.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser on startup"
    )
    subcommand.add_argument(
        "--dev",
        action="store_true",
        help="Reload the open page when a component or a stylesheet is saved",
    )


def _query_listing() -> str:
    """Every query in the library: its name, its scope, and what a caller has to bind.

    Read off the library, so a query that ships is a line here the day its file lands. Only
    the parameters with no default are listed — they are what a run refuses without — and
    every viewer query takes several (`view/manifest.py`).
    """
    described = catalog()
    width = max(len(name) for name in described)
    lines = []
    for name, query in described.items():
        needed = " ".join(
            parameter for parameter, spec in query.params.items() if spec.default is REQUIRED
        )
        lines.append(f"{name:<{width}}  {query.scope:<6}  {needed}".rstrip())
    return "\n".join(lines)


def _query(args: argparse.Namespace) -> None:
    """Run one library query and print its citation, its commentary, and its rows."""
    if args.list:
        print(_query_listing())
        return
    if args.name is None:
        raise SystemExit("hp query takes the name of a query, or --list to see them")
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


def _query_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("name", nargs="?", help="The query to run — a file in analyze/queries/")
    _add_db_argument(subcommand, "The trace store")
    subcommand.add_argument(
        "--project", type=Path, help="The analyzed repository — required by a corpus query"
    )
    subcommand.add_argument(
        "--since",
        type=dt.date.fromisoformat,
        help="Only count sessions started on or after this date (default: the whole corpus)",
    )
    subcommand.add_argument(
        "--as-of",
        type=dt.date.fromisoformat,
        # UTC, not the reader's local day: the runner measures the window in UTC, so a local
        # date west of it would close the window hours before now and drop today's sessions.
        default=dt.datetime.now(tz=dt.UTC).date(),
        help="The date the trailing window is measured back from (default: today, UTC)",
    )
    subcommand.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Bind one of the query's parameters, overriding its production default",
    )
    subcommand.add_argument(
        "--csv", action="store_true", help="Write CSV to stdout, commentary to stderr"
    )
    subcommand.add_argument(
        "--list",
        action="store_true",
        help="List every query with its scope and the parameters it needs bound",
    )


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
    # One value for the quote and the pass: what the declarations say right now.
    versions = Versions.current()
    with EnrichmentStore(args.db) as store:
        if args.dry_run:
            _report_plan(
                plan(store, args.model, versions=versions, project=project, limit=args.limit),
                args.model,
            )
            return
        client = build_client(args.model, concurrency=args.concurrency)
        report = enrich(store, client, versions=versions, project=project, limit=args.limit)
    print(f"{report.enriched} item(s) enriched, {report.swept} orphaned row(s) swept")


def _enrich_arguments(subcommand: argparse.ArgumentParser) -> None:
    _add_db_argument(subcommand, "The trace store")
    subcommand.add_argument(
        "--project", type=Path, help="Only enrich the sessions recorded for this repository"
    )
    subcommand.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        # A model the price table lacks would quote nothing and then burn the breaker cycle
        # on every item, so the accepted names are the priced ones — minus the placeholder,
        # which is in the table only to price Claude Code's own zero-token records.
        choices=[model for model in MODELS if model != SYNTHETIC_MODEL],
        # Without this argparse prints the whole table in every usage line.
        metavar="MODEL",
        help=f"The model to describe with (default: {DEFAULT_MODEL})",
    )
    subcommand.add_argument(
        "--dry-run",
        action="store_true",
        help="Say what would be sent and stop, spending nothing "
        "(creates the empty enrichment tables if absent)",
    )
    subcommand.add_argument("--limit", type=int, help="Send at most this many items")
    subcommand.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"How many `claude` processes run at once (default: {DEFAULT_CONCURRENCY}). "
        "They spend the same 5-hour allowance this machine's own agents do",
    )


def _export_otlp(args: argparse.Namespace) -> None:
    """Ship every session of a project that this backend has not already confirmed."""
    load_dotenv()
    text = TextPolicy(include=args.include_text, max_chars=args.max_chars)
    # Resolved before the store is opened: a run with nowhere to ship refuses now rather than
    # after reading a corpus. A dry run resolves nothing, because counting what a send would
    # ship is what an operator does *before* they have a key.
    backend = None if args.dry_run else _backend(args.backend)
    # A project the store holds nothing under is a mistyped argument, whichever half of the
    # command reads it: worth a line an operator can act on rather than a traceback.
    try:
        # One connection for both halves — DuckDB admits a single writer, and a send has to
        # write its ledger into the store the source is reading. A dry run writes nothing, so
        # it opens read-only and never takes that lock.
        with open_trace_store(args.db, read_only=args.dry_run, wait=CLI_WAIT) as connection:
            ledger = DeliveryLedger(connection, backend=args.backend)
            if backend is None:
                counting = OtlpCensus(ledger, text=text)
                counted = refresh(
                    args.project, extractor=StoreSource(connection), exporter=counting
                )
                print(_census_line(counting.counts, args.backend, len(counted.skipped)))
                return
            with OtlpExporter(
                backend,
                ledger,
                service_name=args.service_name,
                text=text,
                rate=args.rate,
            ) as exporter:
                result = refresh(args.project, extractor=StoreSource(connection), exporter=exporter)
    except UnknownProjectError as error:
        raise SystemExit(str(error)) from error
    print(f"{len(result.extracted)} session(s) exported, {len(result.skipped)} unchanged")


def _backend(name: str) -> Backend:
    """Where `--backend` ships, or a line an operator can act on instead of a traceback."""
    try:
        return named_backend(name, os.environ)
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error


def _census_line(counts: Census, backend: str, skipped: int) -> str:
    """What a dry run prints: what the next send to this backend ships, and what it skips.

    The compaction count is broken out because a compaction is where a session's account of
    itself gets lossy, and the unchanged count because "6 would ship" with nothing beside it
    reads like a shrunken corpus rather than a corpus already delivered.
    """
    return (
        f"{counts.sessions} session(s) and {counts.spans} span(s) would ship to {backend}, "
        f"{counts.compactions} of them compactions; {skipped} unchanged — nothing sent"
    )


def _export_otlp_arguments(subcommand: argparse.ArgumentParser) -> None:
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    _add_db_argument(subcommand, "The trace store")
    subcommand.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        default=GENERIC,
        help="Where to ship, and whose delivery ledger this run reads and writes "
        f"(default: {GENERIC}, configured by {ENDPOINT_ENV}). A named backend reads its own "
        f"key variable, and {ENDPOINT_ENV} overrides its endpoint",
    )
    subcommand.add_argument(
        "--service-name",
        help="Send every session to this service instead of one named for its project directory",
    )
    subcommand.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE,
        help=f"Spans per second, across the whole run (default: {DEFAULT_RATE:g})",
    )
    subcommand.add_argument(
        "--include-text",
        action="store_true",
        help="Also send prompts, model text, tool arguments and results — untrusted "
        "transcript content, published to a third party",
    )
    subcommand.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Characters kept per included text field (default: {DEFAULT_MAX_CHARS})",
    )
    subcommand.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what a send to --backend would ship, and send nothing. Needs no key",
    )


def _report_plan(planned: Sequence[PlannedItem], model: str) -> None:
    """Say what a run would send and what it would cost, per level and in total.

    Every count here is an upper bound: the plan holds each stale item and every item whose
    prompt embeds one, and a child re-described in the same words stops that cascade.
    """
    quote = estimate([Prompt(entry.item.level, entry.rendered) for entry in planned], model)
    counts = Counter(entry.item.level for entry in planned)
    breakdown = ", ".join(f"{counts[level]} {level}" for level in ROUND_ORDER)
    print(f"at most {quote.items} item(s) would be sent to {model} — {breakdown}")
    print(
        f"at most ${quote.usd:.2f}: ~{quote.input_tokens:,} input and "
        f"~{quote.output_tokens:,} output tokens, counting no prompt caching"
    )


def _add_discovery_arguments(subcommand: argparse.ArgumentParser) -> None:
    """What a subcommand that reads transcripts off disk takes: where to look, and for what."""
    subcommand.add_argument("project", type=Path, help="Path to the analyzed repository")
    subcommand.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Where Claude Code keeps transcripts (default: {DEFAULT_PROJECTS_ROOT})",
    )


def _add_db_argument(subcommand: argparse.ArgumentParser, description: str) -> None:
    """The trace store flag, defaulted in one place — `description` says read or write."""
    subcommand.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"{description} (default: {DEFAULT_DB})"
    )


# Every subcommand, in the order `--help` lists them. A project is a positional argument where
# it names the corpus itself — transcripts to discover, or a store's sessions to ship — and
# `--project` where it narrows a store the subcommand would otherwise read whole.
SUBCOMMANDS: dict[str, Subcommand] = {
    "sessions": Subcommand(
        help="List the sessions recorded for a project",
        arguments=_add_discovery_arguments,
        run=_sessions,
    ),
    "extract": Subcommand(
        help="Extract a project's sessions into DuckDB",
        arguments=_extract_arguments,
        run=_extract,
    ),
    "enrich": Subcommand(
        help="Describe the extracted sessions with an AI model",
        arguments=_enrich_arguments,
        run=_enrich,
    ),
    "export-otlp": Subcommand(
        help="Ship the store's sessions to an OTLP backend as spans",
        arguments=_export_otlp_arguments,
        run=_export_otlp,
    ),
    "query": Subcommand(
        help="Run a library query against the trace store",
        arguments=_query_arguments,
        run=_query,
    ),
    "view": Subcommand(
        help="Read the trace store in a local web viewer",
        arguments=_view_arguments,
        run=_view,
    ),
}
