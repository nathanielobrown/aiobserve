"""`hp export-otlp`: the production path, end to end.

Argument parsing, environment validation, the store's single writer and `refresh()` — the
tiers below prove the parts, and these leaves prove the wiring, which is the only thing an
operator actually runs.
"""

import shutil
import traceback
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyphae import cli
from hyphae.export.duckdb import open_trace_store
from hyphae.export.otlp import TextPolicy
from hyphae.export.otlp_delivery import (
    ENDPOINT_ENV,
    GENERIC,
    HEADERS_ENV,
    Backend,
    DeliveryError,
    OtlpExporter,
    census,
)
from hyphae.extract.store import StoreSource
from hyphae.pipeline import refresh
from tests.conftest import MYCELIA, locked
from tests.export.conftest import KEY_SENTINEL, Receiver, attributes, delivery_rows


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, receiver: Receiver) -> None:
    """The environment a run reads: this test's receiver, and a planted key beside it.

    A developer's real `.env` must not decide any leaf here.
    """
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setenv(ENDPOINT_ENV, receiver.url)
    monkeypatch.setenv(HEADERS_ENV, f"x-key={KEY_SENTINEL}")


def ledger(path: Path) -> list[tuple[object, ...]]:
    """The delivery rows a finished run left, minus the clock in the last column."""
    connection = open_trace_store(path, read_only=True)
    rows = [row[:5] for row in delivery_rows(connection)]
    connection.close()
    return rows


def test_the_command_ships_what_a_refresh_ships(
    store_path: Path,
    delivered_db: Path,
    tmp_path: Path,
    receiver: Receiver,
    configured: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command is the same pass the pipeline runs directly, plus a printed count."""
    # If one copy of the store is exported by calling `refresh()` in the test...
    direct = tmp_path / "direct.duckdb"
    shutil.copyfile(delivered_db, direct)
    connection = open_trace_store(direct, read_only=False)
    with OtlpExporter(Backend(name=GENERIC, endpoint=receiver.url), connection) as exporter:
        refresh(Path(MYCELIA), extractor=StoreSource(connection), exporter=exporter)
    connection.close()
    expected = receiver.spans
    receiver.bodies.clear()
    # ...and another copy through the command...
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--backend", GENERIC)
    # ...then the same spans arrive and the same ledger rows land: the command adds argument
    # parsing and a line of output, and nothing that shapes or records a span.
    assert receiver.spans == expected
    assert ledger(store_path) == ledger(direct)
    assert capsys.readouterr().out.strip() == "2 session(s) exported, 0 unchanged"


def test_the_service_name_flag_reaches_the_backend(
    store_path: Path, receiver: Receiver, configured: None
) -> None:
    """`--service-name` sends a run to a dataset other than the project directory's name."""
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--service-name", "mycelia-backfill")
    assert receiver.attributes(receiver.resources[0])["service.name"] == "mycelia-backfill"


def test_missing_configuration_refuses_before_anything_is_read(
    store_path: Path, receiver: Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with no endpoint configured refuses at command start, naming the variable."""
    # If nothing says where to ship — neither the environment nor a `.env`...
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    for absent in ("", "   "):
        monkeypatch.setenv(ENDPOINT_ENV, absent)
        with pytest.raises(SystemExit, match=ENDPOINT_ENV):
            cli.main("export-otlp", MYCELIA, "--db", str(store_path))
    monkeypatch.delenv(ENDPOINT_ENV)
    with pytest.raises(SystemExit, match=ENDPOINT_ENV):
        cli.main("export-otlp", MYCELIA, "--db", str(store_path))
    # ...then it refuses before it opens the store: no request went out, and the store came
    # away without even the ledger table a first export creates.
    assert receiver.bodies == []
    connection = open_trace_store(store_path, read_only=True)
    assert connection.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
    ).fetchone() == (0,)
    connection.close()


def test_a_failing_run_never_prints_the_key(
    store_path: Path,
    receiver: Receiver,
    configured: None,
    capsys: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
) -> None:
    """The credential that authorized a run appears in nothing the run writes."""
    # If the backend refuses the first batch outright — the crash path, which is where a
    # header gets interpolated into a message by accident...
    receiver.reply.status = 400
    with pytest.raises(DeliveryError) as raised:
        cli.main("export-otlp", MYCELIA, "--db", str(store_path))
    # ...then the key is in none of what the run produced: not the rendered traceback, not
    # stdout or stderr, not a warning...
    printed = capsys.readouterr()
    rendered = "".join(traceback.format_exception(raised.value))
    assert KEY_SENTINEL not in rendered
    assert KEY_SENTINEL not in printed.out + printed.err
    assert KEY_SENTINEL not in "".join(str(warning.message) for warning in recwarn)
    # ...and the request did carry it, so there was something to leak.
    assert receiver.sent_headers[0]["x-key"] == KEY_SENTINEL


def test_a_locked_store_fails_fast(store_path: Path, receiver: Receiver, configured: None) -> None:
    """A store another writer holds stops the run at the open, rather than half-delivering."""
    # If an extract is running against the same store — held from another process, since
    # DuckDB answers a second open in this one differently — then the command stops at the
    # open: one writer at a time, and the source and the exporter share that connection...
    with locked(store_path), pytest.raises(duckdb.IOException, match="lock"):
        cli.main("export-otlp", MYCELIA, "--db", str(store_path))
    # ...then nothing was shipped: a run that cannot record what it delivered must not
    # deliver, or the next run duplicates the corpus.
    assert receiver.bodies == []


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No endpoint, no key, and no `.env` — nowhere for a run to ship."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(HEADERS_ENV, raising=False)


def test_a_dry_run_counts_without_a_backend(
    store_path: Path,
    receiver: Receiver,
    unconfigured: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run` says what a send would ship, and needs neither a key nor a send."""
    # If one compaction is planted on a recorded session — invented, because neither session
    # in this store compacted, and a compaction count of zero would prove nothing about the
    # line that reports it...
    planted = open_trace_store(store_path, read_only=False)
    planted.execute(
        "INSERT INTO compactions"
        " SELECT 'planted-compaction', id, 'main', started_at, 'auto', 100, 10, 5"
        " FROM sessions LIMIT 1"
    )
    planted.close()
    # ...and the store is counted rather than shipped...
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--dry-run")
    # ...then the printed count is the mapper's own, session for session and span for span,
    # down to the compactions among those spans — the one number no query reproduces, since
    # the replay rule that drops a fork's inherited copies lives in the mapper...
    connection = open_trace_store(store_path, read_only=True)
    source = StoreSource(connection)
    counted = census([source.extract(session) for session in source.sessions(Path(MYCELIA))])
    connection.close()
    assert capsys.readouterr().out.strip() == (
        f"{counted.sessions} session(s) and {counted.spans} span(s) would ship, "
        f"{counted.compactions} of them compactions — nothing sent"
    )
    # ...which the corpus has some of, so the line is a number rather than a zero...
    assert counted.compactions > 0
    # ...and the run reached no backend and refused nothing for want of a key: a dry run is
    # what an operator does *before* they have one. It leaves the store as it found it,
    # without even the ledger table an export creates, and never takes the write lock.
    assert receiver.bodies == []
    check = open_trace_store(store_path, read_only=True)
    assert check.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
    ).fetchone() == (0,)
    check.close()


@pytest.mark.parametrize("arguments", [(), ("--dry-run",)], ids=["send", "dry-run"])
def test_a_project_the_store_holds_nothing_under_stops_the_run(
    store_path: Path, receiver: Receiver, configured: None, arguments: tuple[str, ...]
) -> None:
    """A project no recorded session sits under is refused, however the command is run."""
    # If the project names a repository the store holds nothing under — a typo, or a path
    # typed from the wrong directory...
    with pytest.raises(SystemExit, match="No session in this store"):
        cli.main("export-otlp", "/no/such/repo", "--db", str(store_path), *arguments)
    # ...then the run says so and stops, rather than reporting a clean delivery of nothing.
    assert receiver.bodies == []


def test_the_delivery_flags_reach_the_exporter(
    store_path: Path, receiver: Receiver, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every flag that shapes or paces a send arrives at the exporter that performs it."""
    # If a run names a rate and opts transcript text in...
    captured: dict[str, object] = {}

    class Recording(OtlpExporter):
        def __init__(self, backend: Backend, connection: duckdb.DuckDBPyConnection, **kwargs: Any):
            captured.update(kwargs)
            super().__init__(backend, connection, **kwargs)

    monkeypatch.setattr(cli, "OtlpExporter", Recording)
    cli.main(
        "export-otlp",
        MYCELIA,
        "--db",
        str(store_path),
        # A rate the fixture sessions cannot reach, so the leaf pays no real wait for it.
        "--rate",
        "100000",
        "--include-text",
        "--max-chars",
        "20",
    )
    # ...then the whole set arrives, compared whole so a flag the wiring drops fails here...
    assert captured == {
        "service_name": None,
        "text": TextPolicy(include=True, max_chars=20),
        "rate": 100_000.0,
    }
    # ...and the text policy is honored rather than merely passed: the excluded fields ship,
    # cut to the length the flag named.
    prompts = [
        value
        for span in receiver.spans
        for key, value in attributes(span).items()
        if key == "claude_code.turn.prompt"
    ]
    assert prompts and all(len(prompt) <= 20 for prompt in prompts)


def test_a_named_backend_refuses_without_its_key(
    store_path: Path, receiver: Receiver, unconfigured: None
) -> None:
    """A run naming a backend whose key is unset stops at the command, naming the variable."""
    # If a backend is named but nothing holds its key...
    with pytest.raises(SystemExit, match="HONEYCOMB_API_KEY"):
        cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--backend", "honeycomb")
    # ...then nothing was read and nothing was sent...
    assert receiver.bodies == []
    # ...and a backend the registry does not hold is refused by the parser itself, so no run
    # ever reaches an endpoint we never verified.
    with pytest.raises(SystemExit):
        cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--backend", "jaeger")
