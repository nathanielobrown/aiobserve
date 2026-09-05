"""`hp export-otlp`: the production path, end to end.

Argument parsing, environment validation, the store's single writer and `refresh()` — the
tiers below prove the parts, and these leaves prove the wiring, which is the only thing an
operator actually runs.
"""

import shutil
import traceback
from pathlib import Path
from typing import Any

import pytest

from hyphae import cli
from hyphae.export.duckdb import StoreLocked, open_trace_store
from hyphae.export.otlp import Census, TextPolicy, census
from hyphae.export.otlp_delivery import (
    ENDPOINT_ENV,
    GENERIC,
    HEADERS_ENV,
    Backend,
    DeliveryError,
    DeliveryLedger,
    OtlpCensus,
    OtlpExporter,
)
from hyphae.extract.store import StoreSource
from hyphae.pipeline import refresh
from tests.conftest import MYCELIA, NO_WAIT, locked
from tests.export.conftest import (
    FIRST,
    KEY_SENTINEL,
    SECOND,
    Receiver,
    attributes,
    deliver,
    delivery_rows,
    trace_of,
)
from tests.export.test_duckdb__locking import IMPATIENT


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
    with open_trace_store(path, read_only=True, wait=NO_WAIT) as connection:
        return [row[:5] for row in delivery_rows(connection)]


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
    with (
        open_trace_store(direct, read_only=False, wait=NO_WAIT) as connection,
        OtlpExporter(
            Backend(name=GENERIC, endpoint=receiver.url),
            DeliveryLedger(connection, backend=GENERIC),
        ) as exporter,
    ):
        refresh(Path(MYCELIA), extractor=StoreSource(connection), exporter=exporter)
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
    with open_trace_store(store_path, read_only=True, wait=NO_WAIT) as connection:
        assert connection.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
        ).fetchone() == (0,)


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


def test_a_locked_store_stops_the_run(
    store_path: Path, receiver: Receiver, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store another writer holds stops the run at the open, rather than half-delivering."""
    # If an extract is running against the same store — held from another process, since
    # DuckDB answers a second open in this one differently — then the command waits its
    # budget out and stops at the open: one writer at a time, and the source and the
    # exporter share that connection. The budget is cut to keep the wait out of the suite;
    # what production spends on it is `CLI_WAIT`.
    monkeypatch.setattr(cli, "CLI_WAIT", IMPATIENT)
    with locked(store_path), pytest.raises(StoreLocked, match="still held"):
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


def would_ship(path: Path, *only: str) -> Census:
    """The census a dry run should print, computed from the store the command reads."""
    with open_trace_store(path, read_only=True, wait=NO_WAIT) as connection:
        source = StoreSource(connection)
        return census(
            [
                source.extract(session)
                for session in source.sessions(Path(MYCELIA))
                if not only or session.id in only
            ]
        )


def census_line(counts: Census, backend: str, skipped: int) -> str:
    """The line a dry run prints, spelled here rather than imported from the command."""
    return (
        f"{counts.sessions} session(s) and {counts.spans} span(s) would ship to {backend}, "
        f"{counts.compactions} of them compactions; {skipped} unchanged — nothing sent"
    )


def test_a_dry_run_counts_what_the_send_after_it_would_ship(
    store_path: Path,
    receiver: Receiver,
    unconfigured: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--dry-run` counts the sessions a send would ship now — not the corpus over again."""
    # If one compaction is planted on the first recorded session — invented, because neither
    # session in this store compacted, and a compaction count of zero would prove nothing
    # about the line that reports it...
    with open_trace_store(store_path, read_only=False, wait=NO_WAIT) as planted:
        planted.execute(
            "INSERT INTO compactions"
            " SELECT 'planted-compaction', id, 'main', started_at, 'auto', 100, 10, 5, false"
            " FROM sessions WHERE id = ?",
            [FIRST],
        )
    # ...and every store the command opens is recorded, so the mode it opens in is an
    # assertion rather than a code reading...
    opened: list[bool] = []
    opener = cli.open_trace_store

    def recording(path: Path, *, read_only: bool, wait: float) -> Any:
        opened.append(read_only)
        return opener(path, read_only=read_only, wait=wait)

    monkeypatch.setattr(cli, "open_trace_store", recording)
    # ...then counting the store rather than shipping it prints the mapper's own numbers,
    # session for session and span for span, down to the compactions among those spans, with
    # nothing yet unchanged...
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--dry-run")
    whole = would_ship(store_path)
    assert capsys.readouterr().out.strip() == census_line(whole, GENERIC, skipped=0)
    # ...which the corpus has some of, so the line is a number rather than a zero...
    assert whole.compactions > 0
    # ...and the run reached no backend and refused nothing for want of a key: a dry run is
    # what an operator does *before* they have one. It opened the store read-only, so it
    # never took the write lock, and left it without even the ledger table an export creates.
    assert receiver.bodies == []
    assert opened == [True]
    with open_trace_store(store_path, read_only=True, wait=NO_WAIT) as check:
        assert check.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'"
        ).fetchone() == (0,)
    # ...and once one of the two sessions has really shipped, the next dry run counts the
    # other one alone and says the delivered one is unchanged. Before this change both runs
    # printed the same line, which is the whole reason for it.
    with open_trace_store(store_path, read_only=False, wait=NO_WAIT) as connection:
        extracted = dict(
            connection.execute("SELECT session_id, fingerprint FROM extract_state").fetchall()
        )
        with OtlpExporter(
            Backend(name=GENERIC, endpoint=receiver.url),
            DeliveryLedger(connection, backend=GENERIC),
        ) as exporter:
            exporter.export(trace_of(connection, SECOND), extracted[SECOND])
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--dry-run")
    remaining = would_ship(store_path, FIRST)
    assert capsys.readouterr().out.strip() == census_line(remaining, GENERIC, skipped=1)
    assert (remaining.sessions, remaining.compactions) == (1, whole.compactions)


def test_a_dry_run_needs_no_key_for_a_named_backend(
    store_path: Path,
    receiver: Receiver,
    unconfigured: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry run counts a named backend's remainder without that backend's key."""
    # If the whole store has shipped to the generic backend...
    with open_trace_store(store_path, read_only=False, wait=NO_WAIT) as connection:
        assert deliver(connection, receiver).extracted == [FIRST, SECOND]
    # ...then a dry run naming honeycomb, whose key is unset, counts both sessions rather than
    # refusing: the census answers the question one asks before having a key, and it answers
    # it for the backend that was named — a ledger read under the wrong name would print zero.
    cli.main("export-otlp", MYCELIA, "--db", str(store_path), "--dry-run", "--backend", "honeycomb")
    assert capsys.readouterr().out.strip() == census_line(
        would_ship(store_path), "honeycomb", skipped=0
    )


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
        def __init__(self, backend: Backend, ledger: DeliveryLedger, **kwargs: Any):
            captured.update(kwargs)
            super().__init__(backend, ledger, **kwargs)

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
    # ...and a dry run of the same flags reaches the census that stands in for the exporter,
    # which holds the shaping flags and none of the sending ones: a census paces nothing.
    counted: dict[str, object] = {}

    class RecordingCensus(OtlpCensus):
        def __init__(self, ledger: DeliveryLedger, **kwargs: Any):
            counted.update(kwargs)
            super().__init__(ledger, **kwargs)

    monkeypatch.setattr(cli, "OtlpCensus", RecordingCensus)
    cli.main(
        "export-otlp",
        MYCELIA,
        "--db",
        str(store_path),
        "--dry-run",
        "--include-text",
        "--max-chars",
        "20",
    )
    assert counted == {"text": TextPolicy(include=True, max_chars=20)}


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
