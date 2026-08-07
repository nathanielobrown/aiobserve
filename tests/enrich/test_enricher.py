"""A whole enrichment run, driven by a fake client: what gets sent, written, and refused.

The store is real — built by running the pipeline over `spine/`, the fixture with four main
turns. Only the model is fake: `FakeClient` records what it was asked and answers from a
script, so every claim about rounds, staleness and failure handling is checked against real
rows without a request leaving the machine. Its answers are invented, as model output must
be — there is no recorded session to draw them from.
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from aiobserve import cli
from aiobserve.enrich.batches import EnrichRequest, Failed, Result, Succeeded
from aiobserve.enrich.enricher import EnrichmentFailed, EnrichReport, enrich
from aiobserve.enrich.prompts import PROMPT_VERSION, Level, TurnItem, input_hash, render_turn
from aiobserve.enrich.store import EnrichmentStore
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION
from aiobserve.enrich.validation import FailureKind
from tests.enrich.conftest import build_store

MODEL = "claude-haiku-4-5-20251001"

# An invented credential, in a shape the screen knows, for the answer that must be refused.
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"


class FakeClient:
    """Answers every request, records every batch, and never touches the network.

    `answers` overrides the reply for one key — a failure, or an answer the validator will
    refuse. Everything else gets a well-formed description naming its own key, so a row can
    be traced back to the request that wrote it.
    """

    def __init__(self, model: str = MODEL, answers: Mapping[str, Result] | None = None) -> None:
        self.model = model
        self.answers = answers or {}
        self.batches: list[tuple[EnrichRequest, ...]] = []

    def submit(self, requests: Sequence[EnrichRequest]) -> list[Result]:
        self.batches.append(tuple(requests))
        return [
            self.answers.get(request.key, Succeeded(key=request.key, output=answer(request.key)))
            for request in requests
        ]

    @property
    def keys(self) -> list[str]:
        """Every key the client was asked about, in the order it was asked."""
        return [request.key for batch in self.batches for request in batch]


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
    build_store(path, ("spine",))
    return path


@pytest.fixture
def store(spine_store: Path, tmp_path: Path) -> Iterator[EnrichmentStore]:
    """A private copy of the `spine/` store, open for enrichment."""
    copy = tmp_path / "traces.duckdb"
    copy.write_bytes(spine_store.read_bytes())
    with EnrichmentStore(copy) as opened:
        yield opened


def turns(store: EnrichmentStore) -> list[TurnItem]:
    """The store's main turns in the order a run sends them — the order they happened in."""
    return store.turn_items()


def stored(store: EnrichmentStore) -> list[tuple[Any, ...]]:
    return store.connection.execute(
        "SELECT session_id, source, turn_id, description, category, outcome, friction,"
        " input_hash, prompt_version, taxonomy_version, model"
        " FROM turn_enrichments ORDER BY turn_id"
    ).fetchall()


def test_a_run_writes_a_row_for_every_stale_turn(store: EnrichmentStore) -> None:
    """One pass describes every enrichable turn and records what it was described under."""
    # If a run enriches the `spine/` store...
    client = FakeClient()
    items = turns(store)
    report = enrich(store, client)
    # ...then it reports what it did, having swept nothing — there are no orphans yet...
    assert report == EnrichReport(swept=0, enriched=4)
    # ...the client was asked about every main turn, once...
    assert client.keys == [item.key for item in items]
    # ...and each row holds the answer that came back, keyed by the turn it describes and
    # stamped with everything that decides whether it is still current.
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


def test_a_second_run_over_an_unchanged_store_sends_nothing(store: EnrichmentStore) -> None:
    """Running again with nothing changed submits nothing and rewrites nothing.

    This is what makes `enrich` safe to run beside `extract` on a schedule.
    """
    # If a store is enriched, and then enriched again with nothing changed...
    enrich(store, FakeClient())
    before = store.connection.execute(
        "SELECT turn_id, enriched_at FROM turn_enrichments ORDER BY turn_id"
    ).fetchall()
    second = FakeClient()
    report = enrich(store, second)
    # ...then the second run sends no batch at all — not an empty one...
    assert second.batches == []
    assert report == EnrichReport(swept=0, enriched=0)
    # ...and every row is untouched, down to when it was written.
    assert (
        store.connection.execute(
            "SELECT turn_id, enriched_at FROM turn_enrichments ORDER BY turn_id"
        ).fetchall()
        == before
    )


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
    assert client.keys == [item.key for item in turns(store)]
    assert {row[9] for row in stored(store)} == {99}


def test_a_model_switch_re_enriches(store: EnrichmentStore) -> None:
    """`--model` re-enriches automatically: a description is an answer from one model."""
    enrich(store, FakeClient())
    client = FakeClient(model="claude-sonnet-4-5")
    enrich(store, client)
    assert client.keys == [item.key for item in turns(store)]
    assert {row[10] for row in stored(store)} == {"claude-sonnet-4-5"}


def test_an_answer_carrying_a_secret_shape_writes_no_row(store: EnrichmentStore) -> None:
    """A refused answer fails its item alone, and the crash names the key, never the answer."""
    # If the model returns a credential-shaped description for one turn...
    items = turns(store)
    refused = items[1]
    client = FakeClient(
        answers={
            refused.key: Succeeded(
                key=refused.key,
                output=answer(refused.key, description=f"Rotated {FAKE_SECRET} and re-ran."),
            )
        }
    )
    # ...then the run crashes, because a silent failure here is a hole in the coverage the
    # hash would then call current forever...
    with pytest.raises(EnrichmentFailed) as failure:
        enrich(store, client)
    # ...the summary names the item and how it failed, and carries nothing the model wrote...
    assert refused.key in str(failure.value)
    assert FailureKind.secret_shape in str(failure.value)
    assert FAKE_SECRET not in str(failure.value)
    # ...that turn holds no row, so rerunning is the retry...
    assert [row[2] for row in stored(store)] == [
        item.turn_id for item in items if item is not refused
    ]
    # ...and the turns that succeeded in the same batch were kept.
    assert len(stored(store)) == 3


def test_a_failed_request_leaves_its_item_stale(store: EnrichmentStore) -> None:
    """An item the API could not answer writes nothing, and the next run picks it up again."""
    items = turns(store)
    dropped = items[0]
    with pytest.raises(EnrichmentFailed):
        enrich(store, FakeClient(answers={dropped.key: Failed(dropped.key, FailureKind.expired)}))
    # If the next run is the retry, it asks about exactly the item that failed.
    client = FakeClient()
    assert enrich(store, client) == EnrichReport(swept=0, enriched=1)
    assert client.keys == [dropped.key]


def test_the_cli_refuses_without_a_key(
    store: EnrichmentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`enrich` refuses at command start when the API key is missing or empty."""
    # A developer's real `.env` must not decide this test.
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    for absent in ("", "   "):
        monkeypatch.setenv("ANTHROPIC_API_KEY", absent)
        with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
            cli.main("enrich", "--db", str(store.path), "--dry-run")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main("enrich", "--db", str(store.path))
    # ...and it refuses before it writes anything, whatever it was asked to do.
    assert stored(store) == []


def test_a_dry_run_writes_nothing_and_sends_nothing(
    store: EnrichmentStore, anthropic_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` says how much a run would send, without building a client at all."""
    # If a dry run is asked for — with `build_client` left as it is, so constructing one
    # would fail the test rather than pass it...
    cli.main("enrich", "--db", str(store.path), "--dry-run")
    # ...then it reports the four stale turns and writes no row.
    assert "4 item(s) would be sent" in capsys.readouterr().out
    assert stored(store) == []


def test_the_cli_limits_what_it_sends(
    store: EnrichmentStore, anthropic_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit N` sends at most N items, which is what makes a dev run cheap."""
    client = FakeClient()
    monkeypatch.setattr(cli, "build_client", lambda model, *, batched: client)
    cli.main("enrich", "--db", str(store.path), "--limit", "2")
    assert len(client.keys) == 2
    assert len(stored(store)) == 2


def test_the_key_never_reaches_the_output(
    store: EnrichmentStore,
    anthropic_env: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing prints the API key, including the failure path."""

    # If every item fails, which is the noisiest a run gets...
    def failing(model: str, *, batched: bool) -> FakeClient:
        keys = [item.key for item in turns(store)]
        return FakeClient(answers={key: Failed(key, FailureKind.api_error) for key in keys})

    monkeypatch.setattr(cli, "build_client", failing)
    with pytest.raises(EnrichmentFailed) as failure:
        cli.main("enrich", "--db", str(store.path))
    # ...then the key is in none of what the run said, and none of what it raised.
    printed = capsys.readouterr()
    assert anthropic_env not in printed.out + printed.err + str(failure.value)
