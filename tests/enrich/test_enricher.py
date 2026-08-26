"""A whole enrichment run, driven by a fake client: what gets sent, written, and refused.

The store is real — built by running the pipeline over `spine/`, the fixture with four main
turns. Only the model is fake: `FakeClient` records what it was asked and answers from a
script, so every claim about rounds, staleness and failure handling is checked against real
rows without a request leaving the machine. Its answers are invented, as model output must
be — there is no recorded session to draw them from.
"""

import json
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pytest

from aiobserve import cli
from aiobserve.enrich.client import (
    DEFAULT_CONCURRENCY,
    CliClient,
    EnrichRequest,
    Failed,
    Result,
    Succeeded,
)
from aiobserve.enrich.cost import Prompt, estimate
from aiobserve.enrich.enricher import (
    ROUND_ORDER,
    EnrichmentFailed,
    EnrichReport,
    enrich,
    plan,
)
from aiobserve.enrich.prompts import (
    PROMPT_VERSION,
    AgentRunItem,
    Level,
    TurnItem,
    input_hash,
    render_turn,
)
from aiobserve.enrich.store import LEVELS, EnrichmentStore
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION
from aiobserve.enrich.validation import FailureKind
from tests.conftest import MODEL_ONLY, MYCELIA, build_store, fixture_transcripts
from tests.enrich.conftest import (
    AUDITOR_RUN,
    MODEL,
    ORIGIN_RUN,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TEAM_RUN,
    enrichment,
    session_item,
    stamp,
)

# The recorded `claude` envelopes, shared with `test_client.py`.
FIXTURES = Path(__file__).parent / "fixtures"

# An invented credential, in a shape the screen knows, for the answer that must be refused.
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"

# A sentinel inside an out-of-vocabulary answer: if it reaches the crash summary, so would
# whatever a real answer had said there.
FAKE_CATEGORY = "SENTINEL-5c1a-out-of-vocabulary"


class FakeClient:
    """Answers every request, records every round, and never starts a process.

    `answers` overrides the reply for one key — a failure, or an answer the validator will
    refuse. Everything else gets a well-formed description naming its own key, so a row can
    be traced back to the request that wrote it.
    """

    def __init__(self, model: str = MODEL, answers: Mapping[str, Result] | None = None) -> None:
        self.model = model
        self.answers = answers or {}
        self.rounds: list[tuple[EnrichRequest, ...]] = []

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        self.rounds.append(tuple(requests))
        return [
            self.answers.get(request.key, Succeeded(key=request.key, output=answer(request.key)))
            for request in requests
        ]

    @property
    def keys(self) -> list[str]:
        """Every key the client was asked about, in the order it was asked."""
        return [request.key for sent in self.rounds for request in sent]


def answer(key: str, **overrides: object) -> dict[str, Any]:
    """A well-formed model answer (invented) for one item."""
    return {
        "description": f"Described {key}.",
        "category": "test",
        "outcome": "completed",
        "friction": None,
    } | overrides


@pytest.fixture(scope="module")
def spine_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`spine/` alone as a trace store: four main turns, two of them slash commands."""
    path = tmp_path_factory.mktemp("enricher") / "traces.duckdb"
    build_store(path, fixture_transcripts("spine"))
    return path


@pytest.fixture
def store(spine_store: Path, tmp_path: Path) -> Iterator[EnrichmentStore]:
    """A private copy of the `spine/` store, open for enrichment."""
    copy = tmp_path / "traces.duckdb"
    copy.write_bytes(spine_store.read_bytes())
    with EnrichmentStore(copy) as opened:
        yield opened


@pytest.fixture(scope="module")
def forest_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three sessions holding every shape the rounds have to order.

    `spine/` nests a run under a run under a main turn, `fork_origin/` nests a fork under an
    auditor with no main turn above either, and `teammate/` holds a run nothing spawned.
    """
    path = tmp_path_factory.mktemp("forest") / "traces.duckdb"
    build_store(path, fixture_transcripts("spine", "fork_origin", "teammate"))
    return path


@pytest.fixture
def forest(forest_store: Path, tmp_path: Path) -> Iterator[EnrichmentStore]:
    """A private copy of the three-session store, open for enrichment."""
    copy = tmp_path / "forest.duckdb"
    copy.write_bytes(forest_store.read_bytes())
    with EnrichmentStore(copy) as opened:
        yield opened


@pytest.fixture
def logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the CLI's auth question without asking it.

    Every real run preflights, which starts a process the autouse guard refuses. The real
    call is pinned in `test_a_dry_run_asks_no_auth_question` and
    `test_the_auth_blob_never_reaches_the_output`; everything else here is about what the run
    does afterwards.
    """
    monkeypatch.setattr(cli, "preflight", lambda: None)


def turns(store: EnrichmentStore) -> list[TurnItem]:
    """The store's main turns in the order a run sends them — the order they happened in."""
    return store.turn_items()


def runs(store: EnrichmentStore) -> list[AgentRunItem]:
    """The store's agent runs, in no particular order — the rounds decide what goes when."""
    return store.run_items()


def key_of(store: EnrichmentStore, agent_run_id: str) -> str:
    """The item key one agent run is sent and stored under."""
    return next(item.key for item in runs(store) if item.agent_run_id == agent_run_id)


def turn_key(store: EnrichmentStore, prefix: str) -> str:
    """The item key of the one main turn whose id starts with `prefix`."""
    return next(item.key for item in turns(store) if item.turn_id.startswith(prefix))


def session_key(store: EnrichmentStore, session_id: str) -> str:
    """The item key one session is sent and stored under."""
    return next(item.key for item in store.session_items() if item.session_id == session_id)


def stored_sessions(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT session_id, description, input_hash FROM session_enrichments ORDER BY session_id"
    ).fetchall()


def stored_runs(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT agent_run_id, description, input_hash, enriched_at"
        " FROM agent_run_enrichments ORDER BY agent_run_id"
    ).fetchall()


def written_at(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    """Every enrichment row of every level, against the moment it was written."""
    return store.connection.execute(
        "SELECT turn_id, enriched_at FROM turn_enrichments"
        " UNION ALL SELECT agent_run_id, enriched_at FROM agent_run_enrichments"
        " UNION ALL SELECT session_id, enriched_at FROM session_enrichments"
        " ORDER BY 1"
    ).fetchall()


def stored(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT session_id, source, turn_id, description, category, outcome, friction,"
        " input_hash, prompt_version, taxonomy_version, model"
        " FROM turn_enrichments ORDER BY turn_id"
    ).fetchall()


def test_every_level_the_store_can_write_gets_a_round() -> None:
    """A pass describes all three levels — a level with no round would be described by nothing.

    The rounds are ordered bottom-up and the store's levels are a closed set, so the two are
    the same members read for different reasons; only their equality is checked here.
    """
    assert set(ROUND_ORDER) == set(LEVELS)


def test_a_run_writes_a_row_for_every_stale_item(store: EnrichmentStore) -> None:
    """One pass describes every enrichable item and records what it was described under."""
    # If a run enriches the `spine/` store...
    client = FakeClient()
    report = enrich(store, client)
    # ...then it reports what it did, having swept nothing — there are no orphans yet...
    assert report == EnrichReport(swept=0, enriched=7)
    # ...the client was asked about the two agent runs, the deeper one first, then about every
    # main turn, once each, and last about the session those turns belong to...
    items = turns(store)
    assert client.keys == [
        key_of(store, SPINE_LEAF),
        key_of(store, SPINE_RUN),
        *(item.key for item in items),
        session_key(store, SPINE),
    ]
    # ...and each turn row holds the answer that came back, keyed by the turn it describes
    # and stamped with everything that decides whether it is still current. The hashes are
    # taken now, not before the run: the turn that spawned a subagent renders differently
    # once that subagent has a description.
    assert stored(store) == [
        (
            item.session_id,
            item.source,
            item.turn_id,
            f"Described {item.key}.",
            "test",
            "completed",
            None,
            input_hash(render_turn(item)),
            PROMPT_VERSION[Level.turn],
            TAXONOMY_VERSION,
            MODEL,
        )
        # Stored rows come back by turn id; the run sent them in the order they happened.
        for item in sorted(items, key=lambda item: item.turn_id)
    ]


def test_a_pass_never_sends_a_gated_session_and_reports_the_row_it_deleted(
    tmp_path: Path,
) -> None:
    """A session with no model response is not described, and the row it had is swept away.

    The whole of the gate as an operator meets it: 45 rows the corpus already holds go, the
    count reaches the console through `EnrichReport.swept`, and nothing is billed to replace
    them. Neither half is visible from the store alone.
    """
    path = tmp_path / "gated.duckdb"
    build_store(path, fixture_transcripts("spine", "model_only"))
    with EnrichmentStore(path) as store:
        # If a store holds a session whose turns drove no api call, described by an earlier
        # pass that had no gate...
        store.upsert(session_item(MODEL_ONLY), enrichment(), stamp())
        client = FakeClient()
        report = enrich(store, client)
        # ...then the run sweeps that row and says so — the one place a reader learns the
        # rows went...
        assert report.swept == 1
        assert [session_id for session_id, *_ in stored_sessions(store)] == [SPINE]
        # ...and the gated session was never sent, so nothing is billed to describe it again.
        assert f"{Level.session}|{MODEL_ONLY}" not in client.keys
        # ...while its `/model` turn was, since turns are not gated.
        assert any(key.startswith(f"{Level.turn}|{MODEL_ONLY}|") for key in client.keys)


def test_a_second_run_over_an_unchanged_store_sends_nothing(forest: EnrichmentStore) -> None:
    """Running again with nothing changed submits nothing and rewrites nothing.

    This is what makes `enrich` safe to run beside `extract` on a schedule. Over the forest
    rather than the spine because `fork_origin/`'s fork replayed its own spawning call into
    its transcript: a render that let that call carry a description would embed the fork's
    description in the fork's own prompt, so the hash would never settle and the run would be
    re-described — and re-billed — every night. 43 recorded runs hold such a self-copy.
    """
    # If a store is enriched, and then enriched again with nothing changed...
    enrich(forest, FakeClient())
    before = written_at(forest)
    second = FakeClient()
    report = enrich(forest, second)
    # ...then the second run sends no round at all — not an empty one...
    assert second.rounds == []
    assert report == EnrichReport(swept=0, enriched=0)
    # ...and every row of all three levels is untouched, down to when it was written.
    assert written_at(forest) == before


def test_a_prompt_version_bump_re_enriches_the_level(
    store: EnrichmentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the instructions the hash cannot see re-enriches everything they cover."""
    enrich(store, FakeClient())
    # If the turn level's prompt version moves — an instruction or output-schema edit...
    monkeypatch.setitem(PROMPT_VERSION, Level.turn, 99)
    client = FakeClient()
    enrich(store, client)
    # ...then every turn is re-sent, and every row records the new version.
    assert client.keys == [item.key for item in turns(store)]
    assert {row[8] for row in stored(store)} == {99}


def test_a_taxonomy_bump_re_enriches(
    store: EnrichmentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A taxonomy revision makes existing rows stale without invalidating them."""
    enrich(store, FakeClient())
    monkeypatch.setattr("aiobserve.enrich.enricher.TAXONOMY_VERSION", 99)
    client = FakeClient()
    enrich(store, client)
    assert client.keys == [
        key_of(store, SPINE_LEAF),
        key_of(store, SPINE_RUN),
        *(item.key for item in turns(store)),
        session_key(store, SPINE),
    ]
    assert {row[9] for row in stored(store)} == {99}


def test_a_model_switch_re_enriches(store: EnrichmentStore) -> None:
    """`--model` re-enriches automatically: a description is an answer from one model."""
    enrich(store, FakeClient())
    client = FakeClient(model="claude-sonnet-4-5")
    enrich(store, client)
    assert client.keys == [
        key_of(store, SPINE_LEAF),
        key_of(store, SPINE_RUN),
        *(item.key for item in turns(store)),
        session_key(store, SPINE),
    ]
    assert {row[10] for row in stored(store)} == {"claude-sonnet-4-5"}


def test_a_round_of_mixed_failures_crashes_naming_keys_and_kinds(store: EnrichmentStore) -> None:
    """Failed items crash the run at the end, classified by kind and named by key alone.

    Nothing the model wrote reaches the summary — the natural implementation, formatting the
    failed response into the message, is the one that leaks a credential out of a transcript.
    """
    # If one round fails three ways at once — an item the breaker abandoned unsent, an answer
    # outside the taxonomy, and an answer carrying something shaped like a credential...
    items = turns(store)
    abandoned, invalid, refused = items[0], items[1], items[2]
    client = FakeClient(
        answers={
            # The abandoned item carries no sentinel because `Failed` has no field to put one
            # in: a failure record cannot repeat model output it never received. It is also
            # the kind that makes the round-level claim below matter — a breaker trip returns
            # the paid answers alongside the abandoned ones rather than raising over them.
            abandoned.key: Failed(abandoned.key, FailureKind.aborted),
            invalid.key: Succeeded(
                key=invalid.key,
                output=answer(invalid.key, category=f"refactoring-{FAKE_CATEGORY}"),
            ),
            refused.key: Succeeded(
                key=refused.key,
                output=answer(refused.key, description=f"Rotated {FAKE_SECRET} and re-ran."),
            ),
        }
    )
    # ...then the run crashes, because a silent failure here is a hole in the coverage the
    # hash would then call current forever...
    with pytest.raises(EnrichmentFailed) as failure:
        enrich(store, client)
    summary = str(failure.value)
    # ...the summary names each item and how it failed...
    assert [key in summary for key in (abandoned.key, invalid.key, refused.key)] == [True] * 3
    assert [
        kind in summary
        for kind in (FailureKind.aborted, FailureKind.invalid_output, FailureKind.secret_shape)
    ] == [True] * 3
    # ...and carries nothing either answer said...
    assert FAKE_SECRET not in summary
    assert FAKE_CATEGORY not in summary
    # ...the three failed turns hold no row, so rerunning is the retry...
    assert [row[2] for row in stored(store)] == [items[3].turn_id]
    # ...and the sibling that succeeded in the same round was kept.
    assert stored(store)[0][3] == f"Described {items[3].key}."


def test_a_failed_request_leaves_its_item_stale(store: EnrichmentStore, tmp_path: Path) -> None:
    """An item the CLI could not answer writes nothing, and the next run picks it up again.

    Staleness is the whole resume mechanism: there is no state to keep, so a crashed run
    leaves nothing behind to clean up or to go stale itself. `timeout` is the kind that makes
    the point — the client already retried this item and gave up, so the only retry left is
    the rerun below.
    """
    items = turns(store)
    dropped = items[0]
    with pytest.raises(EnrichmentFailed):
        enrich(store, FakeClient(answers={dropped.key: Failed(dropped.key, FailureKind.timeout)}))
    # If the next run is the retry, it asks about exactly the item that failed — and about the
    # session it belongs to, which the first run refused to describe from a hole...
    client = FakeClient()
    assert enrich(store, client) == EnrichReport(swept=0, enriched=2)
    assert client.keys == [dropped.key, session_key(store, SPINE)]
    # ...and the crash wrote no resume file to find it by: the store and DuckDB's own
    # write-ahead log are everything on disk.
    assert {path.name for path in tmp_path.iterdir()} <= {"traces.duckdb", "traces.duckdb.wal"}


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
    """`aiobserve enrich` leaves the same rows as calling `enrich` directly, and needs no key.

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


def test_the_auth_blob_never_reaches_the_output(
    store: EnrichmentStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing prints what the CLI said about the account, including on the failure path.

    `preflight` really runs here, over the recorded logged-in envelope, so the account blob
    is in the process rather than assumed absent — an email, an org id and an org name that
    would leak from any implementation echoing what the auth check read.
    """
    status = json.loads((FIXTURES / "auth_status_logged_in.json").read_text())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(status), ""),
    )

    # If every item fails, which is the noisiest a run gets...
    def failing(model: str, *, concurrency: int) -> FakeClient:
        keys = [item.key for item in turns(store)]
        return FakeClient(answers={key: Failed(key, FailureKind.api_error) for key in keys})

    monkeypatch.setattr(cli, "build_client", failing)
    with pytest.raises(EnrichmentFailed) as failure:
        cli.main("enrich", "--db", str(store.path))
    # ...then no value the envelope carried is in what the run said, or in what it raised.
    printed = capsys.readouterr()
    said = printed.out + printed.err + str(failure.value)
    blobs = [status["email"], status["orgId"], status["orgName"]]
    assert [blob in said for blob in blobs] == [False] * 3


def test_rounds_send_children_before_parents(forest: EnrichmentStore) -> None:
    """Every run is described after the runs it spawned, and every main turn after both.

    A parent's prompt embeds its children's descriptions, so a parent sent first would be
    described from a hole — and the hash would then call that description current forever.
    """
    # If three sessions are enriched at once — a run under a run under a turn, a fork under
    # an auditor under no turn at all, and a run nothing spawned...
    client = FakeClient()
    enrich(forest, client)
    # ...then the rounds are the levels of the forest, deepest first: every leaf run...
    assert [{request.key for request in sent} for sent in client.rounds] == [
        {key_of(forest, SPINE_LEAF), key_of(forest, ORIGIN_RUN), key_of(forest, TEAM_RUN)},
        # ...then the runs that spawned them...
        {key_of(forest, SPINE_RUN), key_of(forest, AUDITOR_RUN)},
        # ...then the main turns, because a turn embeds the runs it spawned...
        {item.key for item in turns(forest)},
        # ...and the sessions last of all, each embedding its own turns and the runs nothing
        # else in it embeds.
        {item.key for item in forest.session_items()},
    ]


def test_a_rootless_run_is_a_root(forest: EnrichmentStore) -> None:
    """A run no tool call spawned is a leaf of nobody's tree, and goes out in the first round.

    46 recorded runs carry no spawning call — mostly teammates, which the team mechanism
    starts rather than an agent. Waiting for a parent they do not have would strand them.
    """
    client = FakeClient()
    enrich(forest, client)
    first = {request.key for request in client.rounds[0]}
    # The teammate run, which names neither a spawning call nor a parent agent, goes in the
    # first round; a run that does name a parent waits for it.
    assert key_of(forest, TEAM_RUN) in first
    assert key_of(forest, SPINE_RUN) not in first


def test_a_run_naming_a_missing_parent_crashes(forest: EnrichmentStore) -> None:
    """A child whose parent run is not in the store crashes the run, naming the child.

    Planted, not recorded: no run of the corpus names a parent the store lacks (2,459
    scanned). Ordering cannot be right for a tree with a gap in it, and guessing a root
    would send the child before a parent that may yet arrive.
    """
    # If the run that spawned `spine/`'s leaf is deleted, standing for a store missing an
    # agent that some other agent named as its parent...
    forest.connection.execute("DELETE FROM agent_runs WHERE id = ?", [SPINE_RUN])
    # ...then the run refuses to order anything, and says which child it could not place.
    with pytest.raises(ValueError, match=f"{SPINE_LEAF}.*{SPINE_RUN}"):
        enrich(forest, FakeClient())


def test_a_childs_new_description_makes_its_ancestors_stale(store: EnrichmentStore) -> None:
    """A description that changes re-describes everything above it, in the same invocation.

    The stale set has to be recomputed after each round's upserts. Computing it once up
    front passes every other check here while silently never cascading.
    """
    # If `spine/` is fully enriched, and then the leaf run alone is made stale — by renaming
    # a tool call only that run's prompt renders...
    enrich(store, FakeClient())
    before = {row[0]: row[2] for row in stored_runs(store)} | {
        row[2]: row[7] for row in stored(store)
    }
    before_session = stored_sessions(store)[0][2]
    store.connection.execute(
        "UPDATE tool_calls SET name = 'Grep' WHERE id = 'toolu_01SzCMuLzJk8ag5BnK545sWY'"
    )
    # ...and the model answers with new text each time it is asked again, as a re-read of
    # changed work would...
    rewritten = {
        key: Succeeded(key=key, output=answer(key, description=f"Rewrote {key}."))
        for key in (
            key_of(store, SPINE_LEAF),
            key_of(store, SPINE_RUN),
            turn_key(store, "818588ad"),
        )
    }
    client = FakeClient(answers=rewritten)
    enrich(store, client)
    # ...then the run goes up the tree: the leaf, then the run whose prompt embeds its
    # description, then the main turn whose prompt embeds *that*, and last the session whose
    # prompt embeds the turn — none of which was stale when the round started.
    assert client.keys == [
        key_of(store, SPINE_LEAF),
        key_of(store, SPINE_RUN),
        turn_key(store, "818588ad"),
        session_key(store, SPINE),
    ]
    # ...and each of their stored inputs moved.
    after = {row[0]: row[2] for row in stored_runs(store)} | {
        row[2]: row[7] for row in stored(store)
    }
    changed = {key for key, value in after.items() if before[key] != value}
    assert changed == {SPINE_LEAF, SPINE_RUN, "818588ad-3849-48fe-a546-573163768e04"}
    assert stored_sessions(store)[0][2] != before_session


def test_a_child_re_described_identically_stops_the_cascade(store: EnrichmentStore) -> None:
    """A re-described child whose text did not change leaves its ancestors alone.

    The other half of the hash contract, and the reason a dry run's count is an upper bound.
    """
    # If the same leaf run is made stale, and the model answers it with the same description
    # as before...
    enrich(store, FakeClient())
    turns_before = stored(store)
    parent_before = [row for row in stored_runs(store) if row[0] == SPINE_RUN]
    store.connection.execute(
        "UPDATE tool_calls SET name = 'Grep' WHERE id = 'toolu_01SzCMuLzJk8ag5BnK545sWY'"
    )
    client = FakeClient()
    enrich(store, client)
    # ...then the leaf is the only item sent: its parent's prompt reads the same as it did,
    # so nothing above it is stale...
    assert client.keys == [key_of(store, SPINE_LEAF)]
    # ...and no ancestor's row was rewritten, down to when it was written.
    assert stored(store) == turns_before
    assert [row for row in stored_runs(store) if row[0] == SPINE_RUN] == parent_before


def test_a_failed_childs_parents_are_skipped(store: EnrichmentStore) -> None:
    """When a child fails, the items whose prompts embed it write nothing at all.

    Writing a parent whose child failed bakes a hole into a description that the hash then
    calls current forever — the one failure mode a rerun cannot heal.
    """
    # If the leaf run fails and everything else answers normally...
    leaf = key_of(store, SPINE_LEAF)
    client = FakeClient(answers={leaf: Failed(leaf, FailureKind.api_error)})
    with pytest.raises(EnrichmentFailed, match=str(FailureKind.api_error)):
        enrich(store, client)
    # ...then nothing above it was sent — not the run that spawned it, not the main turn that
    # spawned *that*, and not the session, whose prompt embeds the turn — and none wrote a
    # row...
    assert client.keys == [
        leaf,
        *(item.key for item in turns(store) if not item.turn_id.startswith("818588ad")),
    ]
    assert stored_runs(store) == []
    assert stored_sessions(store) == []
    # ...while the session's three other main turns were enriched as usual: a skip is not a
    # failure, and it takes only the ancestors with it.
    assert [row[2][:8] for row in stored(store)] == ["30aad8e5", "5b848af7", "8cdceb31"]
