"""A whole enrichment run, driven by a fake client: what gets sent, written, and refused.

The store is real — built by running the pipeline over `spine/`, the fixture with four main
turns. Only the model is fake (`passes.py`). What the CLI does around a run is in
`test_enricher__cli.py`; here every leaf is about the rounds themselves: what a pass sends,
what it writes, what makes an item stale, and how a new description travels up.
"""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from hyphae import cli
from hyphae.enrich.client import (
    Failed,
    Succeeded,
)
from hyphae.enrich.enricher import (
    ROUND_ORDER,
    EnrichmentFailed,
    EnrichReport,
    enrich,
)
from hyphae.enrich.items import Level
from hyphae.enrich.prompts import PROMPT_VERSION, input_hash, render_turn
from hyphae.enrich.store import LEVELS, EnrichmentStore
from hyphae.enrich.taxonomy import TAXONOMY_VERSION
from hyphae.enrich.validation import FailureKind
from tests.conftest import MODEL_ONLY, build_store, fixture_transcripts
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
from tests.enrich.passes import (
    FAKE_CATEGORY,
    FAKE_SECRET,
    FIXTURES,
    FakeClient,
    answer,
    key_of,
    session_key,
    stored,
    stored_runs,
    stored_sessions,
    turn_key,
    turns,
    written_at,
)


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
    monkeypatch.setattr("hyphae.enrich.enricher.TAXONOMY_VERSION", 99)
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
