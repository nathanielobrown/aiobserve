"""What the CLI does around a run: dry runs, the price it quotes, the flags it forwards.

Same real store and same fake model as `test_enricher.py` (`passes.py`), driven through
`hp enrich` rather than the library — so a leaf here is about what a person typing the
command sees and what reaches the client, not about what a round writes.
"""

from pathlib import Path

import duckdb
import pytest

from hyphae import cli
from hyphae.enrich.client import (
    DEFAULT_CONCURRENCY,
    CliClient,
)
from hyphae.enrich.cost import Prompt, estimate
from hyphae.enrich.enricher import (
    enrich,
    plan,
)
from hyphae.enrich.store import EnrichmentStore
from tests.conftest import MYCELIA
from tests.enrich.conftest import (
    MODEL,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.enrich.passes import (
    FakeClient,
    key_of,
    session_key,
    stored,
    stored_runs,
    stored_sessions,
    turn_key,
)


@pytest.fixture
def logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the CLI's auth question without asking it.

    Every real run preflights, which starts a process the autouse guard refuses. The real
    call is pinned in `test_a_dry_run_asks_no_auth_question` and
    `test_the_auth_blob_never_reaches_the_output`; everything else here is about what the run
    does afterwards.
    """
    monkeypatch.setattr(cli, "preflight", lambda: None)


def test_a_dry_run_asks_no_auth_question(
    store: EnrichmentStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Quoting a run asks nothing about auth; a run that would spend asks before it renders.

    Whoever decides whether to pay for a pass is not always whoever is logged in. The autouse
    subprocess guard is the assertion for the first half: `preflight` shells out to `claude`,
    so a dry run that checked would raise here instead of printing a quote.
    """
    # If a store is priced with `preflight` left alone...
    cli.main("enrich", "--db", str(store.path), "--dry-run")
    # ...then it quotes the plan and writes no row...
    assert "at most 7 item(s) would be sent" in capsys.readouterr().out
    assert stored(store) == []
    # ...and a real run over the same store asks first: the auth question comes before the
    # client that would spend, so a logged-out machine fails before it renders a prompt.
    order: list[str] = []

    def client(model: str, *, concurrency: int) -> FakeClient:
        order.append("client")
        return FakeClient()

    monkeypatch.setattr(cli, "preflight", lambda: order.append("preflight"))
    monkeypatch.setattr(cli, "build_client", client)
    cli.main("enrich", "--db", str(store.path), "--limit", "1")
    assert order == ["preflight", "client"]


def test_the_removed_batch_flag_is_rejected(store: EnrichmentStore) -> None:
    """`--no-batch` is gone: a script still passing it stops rather than silently batching.

    There is one path now, and it is neither of the two the flag chose between.
    """
    with pytest.raises(SystemExit):
        cli.main("enrich", "--db", str(store.path), "--no-batch")


def test_a_dry_run_creates_the_enrichment_tables_it_finds_missing(
    spine_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run over a store nothing has enriched leaves its three tables behind, empty.

    Deliberate, and the one thing a dry run does write: opening a store is the single path
    that creates the enrichment schema, and a read-only second path would be a second way to
    be wrong about it. The tables are empty, and any run would have created them anyway.
    """
    # If a store the pipeline wrote and enrichment has never opened is priced...
    fresh = tmp_path / "fresh.duckdb"
    fresh.write_bytes(spine_store.read_bytes())
    cli.main("enrich", "--db", str(fresh), "--dry-run")
    assert "at most 7 item(s) would be sent" in capsys.readouterr().out
    # ...then all three tables are there afterwards — the query would raise if one were
    # missing — and every one of them is empty.
    connection = duckdb.connect(str(fresh), read_only=True)
    assert connection.execute(
        "SELECT (SELECT count(*) FROM turn_enrichments),"
        " (SELECT count(*) FROM agent_run_enrichments),"
        " (SELECT count(*) FROM session_enrichments)"
    ).fetchone() == (0, 0, 0)
    connection.close()


def test_a_dry_run_writes_nothing_and_sends_nothing(
    store: EnrichmentStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` says how much a run would send, broken down by level."""
    # If a dry run is asked for...
    cli.main("enrich", "--db", str(store.path), "--dry-run")
    # ...then it reports the two stale runs, the four stale turns and the session, and writes
    # no row.
    printed = capsys.readouterr().out
    assert "at most 7 item(s) would be sent" in printed
    assert "2 agent_run, 4 turn, 1 session" in printed
    assert stored(store) == []


def test_a_dry_run_scoped_to_a_project_places_a_relative_path(
    store: EnrichmentStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--project` names a repository from any working directory, relative spelling included."""
    # If the project is named the way a shell in its parent directory would name it — and a
    # recorded `project_dir` is absolute, so the root is the one such directory here...
    monkeypatch.chdir("/")
    cli.main(
        "enrich",
        "--db",
        str(store.path),
        "--dry-run",
        "--project",
        str(Path(MYCELIA).relative_to("/")),
    )
    relative = capsys.readouterr().out
    # ...then it prices what the absolute spelling prices, rather than the nothing an
    # unresolved path finds.
    cli.main("enrich", "--db", str(store.path), "--dry-run", "--project", MYCELIA)
    assert relative == capsys.readouterr().out
    assert "at most 7 item(s) would be sent" in relative


def test_a_dry_run_counts_the_ancestors_of_what_is_stale(
    store: EnrichmentStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """One stale leaf is quoted as four items: itself and everything that embeds it.

    Whether the cascade really reaches that far is unknowable before the answers come back,
    which is why the report says "at most" rather than naming a price.
    """
    # If a fully enriched store has one leaf run made stale — by renaming a tool call only
    # that run's prompt renders...
    enrich(store, FakeClient())
    store.connection.execute(
        "UPDATE tool_calls SET name = 'Grep' WHERE id = 'toolu_01SzCMuLzJk8ag5BnK545sWY'"
    )
    # ...then a dry run quotes the leaf, the run that spawned it, the main turn that spawned
    # *that*, and the session holding the turn — one per level of the chain above it.
    planned = plan(store, MODEL, project=None, limit=None)
    assert {entry.item.key for entry in planned} == {
        key_of(store, SPINE_LEAF),
        key_of(store, SPINE_RUN),
        turn_key(store, "818588ad"),
        session_key(store, SPINE),
    }
    cli.main("enrich", "--db", str(store.path), "--dry-run")
    assert "at most 4 item(s) would be sent" in capsys.readouterr().out


def test_a_dry_run_quotes_a_price_it_computed_itself(
    store: EnrichmentStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The quoted dollars are arithmetic over the prompts, checkable without a network.

    The autouse subprocess guard is what proves the "without a network" half: an
    implementation that asked the model what it charges would raise here rather than print.
    """
    # If a dry run reports on a store nothing has enriched...
    planned = plan(store, MODEL, project=None, limit=None)
    cli.main("enrich", "--db", str(store.path), "--dry-run")
    printed = capsys.readouterr().out
    # ...then the price it printed is the one `estimate` derives from the same prompts —
    # one figure now, because there is one way to send an item...
    quote = estimate([Prompt(entry.item.level, entry.rendered) for entry in planned], MODEL)
    assert f"at most ${quote.usd:.2f}" in printed
    # ...and seven short fixture prompts cost a fraction of a cent, so the report has to
    # carry the token counts to be worth reading at all.
    assert f"~{quote.input_tokens:,} input" in printed


def test_the_cli_writes_what_the_library_writes(
    spine_store: Path, tmp_path: Path, logged_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hp enrich` leaves the same rows as calling `enrich` directly, and needs no key.

    The command is a thin wrapper by intent; a check on the library alone would miss an
    argument the CLI forgets to pass through.
    """
    # If the same store is enriched twice — once through the command, once through the
    # function — with the same fake answering both, on a machine holding no API key at all...
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    through_cli, direct = tmp_path / "cli.duckdb", tmp_path / "direct.duckdb"
    for copy in (through_cli, direct):
        copy.write_bytes(spine_store.read_bytes())
    monkeypatch.setattr(cli, "build_client", lambda model, *, concurrency: FakeClient())
    cli.main("enrich", "--db", str(through_cli))
    with EnrichmentStore(direct) as store:
        enrich(store, FakeClient())
        expected = stored(store) + [row[:3] for row in stored_runs(store)] + stored_sessions(store)
    # ...then both stores hold the same rows at every level, `enriched_at` aside — the one
    # column a second run cannot reproduce.
    with EnrichmentStore(through_cli) as store:
        assert (
            stored(store) + [row[:3] for row in stored_runs(store)] + stored_sessions(store)
            == expected
        )


def test_the_cli_limits_what_it_sends(
    store: EnrichmentStore, logged_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit N` sends at most N items, which is what makes a dev run cheap."""
    client = FakeClient()
    monkeypatch.setattr(cli, "build_client", lambda model, *, concurrency: client)
    cli.main("enrich", "--db", str(store.path), "--limit", "2")
    assert len(client.keys) == 2
    # The limit is spent from the deepest round outwards, so it buys the two agent runs
    # before it reaches a turn.
    assert len(stored_runs(store)) == 2
    assert stored(store) == []


def test_the_concurrency_flag_reaches_the_client(
    store: EnrichmentStore, logged_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--concurrency N` sets how many `claude` processes a round runs at once, defaulting to 4."""
    # If the one place a client is built is asked for one, it answers with the real client,
    # holding the model the rows will be stamped with and the width it was given...
    built = cli.build_client(MODEL, concurrency=2)
    assert isinstance(built, CliClient)
    assert (built.model, built.concurrency) == (MODEL, 2)
    # ...and the flag is what decides that width, with a default a bare run can afford.
    asked: list[int] = []

    def record(model: str, *, concurrency: int) -> FakeClient:
        asked.append(concurrency)
        return FakeClient()

    monkeypatch.setattr(cli, "build_client", record)
    cli.main("enrich", "--db", str(store.path), "--limit", "1")
    cli.main("enrich", "--db", str(store.path), "--limit", "1", "--concurrency", "2")
    assert asked == [DEFAULT_CONCURRENCY, 2]
    assert DEFAULT_CONCURRENCY == 4
