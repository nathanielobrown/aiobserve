"""`aiobserve export-otlp`: the production path, end to end.

Argument parsing, environment validation, the store's single writer and `refresh()` — the
tiers below prove the parts, and these leaves prove the wiring, which is the only thing an
operator actually runs.
"""

import shutil
import traceback
from pathlib import Path

import duckdb
import pytest

from aiobserve import cli
from aiobserve.export.duckdb import open_trace_store
from aiobserve.export.otlp import (
    ENDPOINT_ENV,
    GENERIC,
    HEADERS_ENV,
    Backend,
    DeliveryError,
    OtlpExporter,
)
from aiobserve.extract.store import StoreSource
from aiobserve.pipeline import refresh
from tests.conftest import MYCELIA, locked
from tests.export.conftest import KEY_SENTINEL, Receiver, delivery_rows


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
