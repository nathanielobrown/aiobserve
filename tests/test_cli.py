"""The command line's own surface: what each subcommand takes, and what runs it.

Every other tier drives `cli.main` to reach the code under it, so the flags those tiers
happen to pass are covered and the rest are not. This is the file that pins the surface
whole — a flag renamed, a default moved, or a subcommand wired to the wrong handler.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from aiobserve import cli
from aiobserve.cli import DEFAULT_DB
from aiobserve.enrich.client import DEFAULT_CONCURRENCY, DEFAULT_MODEL
from aiobserve.export.otlp import DEFAULT_MAX_CHARS, DEFAULT_RATE, GENERIC
from aiobserve.sessions import DEFAULT_PROJECTS_ROOT
from aiobserve.view.app import PORT

PROJECT = Path("repos/mycelia")

# What each subcommand parses to when it is given nothing but the arguments it requires: the
# whole namespace, so a flag added with no leaf here shows up as a failure rather than as an
# untested one. A store subcommand takes `--project` as a filter over a corpus already
# extracted; a discovery one takes it positionally, because it is the corpus. A default the
# clock decides is the clock itself, read when the parser is built rather than when this
# module was imported — a date pinned at import time is wrong for the run that straddles
# midnight.
SURFACES: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "sessions": (
        (str(PROJECT),),
        {"project": PROJECT, "projects_root": DEFAULT_PROJECTS_ROOT},
    ),
    "extract": (
        (str(PROJECT),),
        {"project": PROJECT, "projects_root": DEFAULT_PROJECTS_ROOT, "db": DEFAULT_DB},
    ),
    "enrich": (
        (),
        {
            "db": DEFAULT_DB,
            "project": None,
            "model": DEFAULT_MODEL,
            "dry_run": False,
            "limit": None,
            "concurrency": DEFAULT_CONCURRENCY,
        },
    ),
    "export-otlp": (
        (str(PROJECT),),
        {
            "project": PROJECT,
            "db": DEFAULT_DB,
            "backend": GENERIC,
            "service_name": None,
            "rate": DEFAULT_RATE,
            "include_text": False,
            "max_chars": DEFAULT_MAX_CHARS,
            "dry_run": False,
        },
    ),
    "query": (
        ("agent_types",),
        {
            "name": "agent_types",
            "db": DEFAULT_DB,
            "project": None,
            "since": None,
            "as_of": dt.date.today,
            "param": [],
            "csv": False,
        },
    ),
    "view": ((), {"db": DEFAULT_DB, "port": PORT, "no_browser": False}),
}


@pytest.mark.parametrize("name", sorted(SURFACES))
def test_a_subcommand_parses_to_the_arguments_it_documents(name: str) -> None:
    """Every subcommand's flags and defaults are the ones the command line promises."""
    required, expected = SURFACES[name]
    # The clock is read either side of the build, so the namespace matches one of the two
    # whichever day the parser was built on.
    before = _read_clocks(expected)
    parsed = cli.build_parser().parse_args([name, *required])
    after = _read_clocks(expected)
    assert vars(parsed) in ({"command": name, **before}, {"command": name, **after})


def _read_clocks(expected: dict[str, Any]) -> dict[str, Any]:
    """The expected namespace with each clock-valued default read now."""
    return {key: value() if callable(value) else value for key, value in expected.items()}


def test_every_subcommand_the_parser_exposes_is_pinned_above() -> None:
    """The surfaces are checked against the parser, so a seventh subcommand cannot arrive
    unpinned.

    Without this the table above is a list someone maintains rather than the whole surface.
    """
    assert set(cli.SUBCOMMANDS) == set(SURFACES)


def test_the_store_flag_is_one_flag_wherever_it_appears() -> None:
    """`--db` means the same path, typed the same way, in every subcommand that names a store.

    The default is pinned per subcommand above; what this adds is that the five share one
    declaration — same type, same flag — while `sessions` reads transcripts off disk and takes
    no store at all.
    """
    stores = {name for name, (_, options) in SURFACES.items() if "db" in options}
    assert stores == {"extract", "enrich", "export-otlp", "query", "view"}
    for name in sorted(stores):
        required, _ = SURFACES[name]
        parsed = cli.build_parser().parse_args([name, *required, "--db", "elsewhere.duckdb"])
        # A `Path`, not the string argparse hands back untyped.
        assert parsed.db == Path("elsewhere.duckdb"), name


def test_the_viewer_opens_a_browser_unless_the_run_says_not_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`aiobserve view` serves the store it was given, and `--no-browser` is what suppresses
    the tab.

    The one flag on the command line whose value is inverted between the argument and the
    call, and the only subcommand whose handler nothing else drives.
    """
    served: list[tuple[Path, int, bool]] = []
    monkeypatch.setattr(
        cli,
        "serve",
        lambda path, port, *, open_browser: served.append((path, port, open_browser)),
    )
    cli.main("view", "--db", "traces.duckdb", "--port", "9000")
    cli.main("view", "--no-browser")
    assert served == [(Path("traces.duckdb"), 9000, True), (DEFAULT_DB, PORT, False)]
