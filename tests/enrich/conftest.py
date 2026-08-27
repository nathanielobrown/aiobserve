"""Scaffolding for the enrichment tier: a store built from recorded fixtures, and no `claude`.

The enrichment renders read rows, so their evidence is a real DuckDB built by running the
existing pipeline over `tests/fixtures/` — the same keys the pipeline really writes. Building
it costs an extraction per fixture, so `fixture_db` builds once per test session and
`mutable_db` hands out a copy to any test that plants or deletes rows.
"""

import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from hyphae.enrich.client import CLAUDE
from hyphae.enrich.prompts import SessionItem
from hyphae.enrich.store import EnrichmentStore, Stamp
from hyphae.enrich.taxonomy import TAXONOMY_VERSION, Category, Outcome
from hyphae.enrich.validation import Enrichment
from tests.conftest import build_store, fixture_transcripts
from tests.enrich.fake_cli import FakeCli, Reply

# The model the fake answers are attributed to, at both doors that write rows.
MODEL = "claude-haiku-4-5-20251001"

# The fixture directories enrichment reads, and the session ids their transcripts carry.
# `plans/enrichment/testing_plan.md` maps each one to the shapes it carries; the rest of
# `tests/fixtures/` is left out so the build stays cheap.
SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
SERVER_TOOLS = "088d63aa-71d3-4108-965e-5147e3eaddbd"
WORKFLOW = "8d930c77-9e60-4784-9885-6d4c226280f7"
TEAMMATE = "10d0349d-0705-4e23-aa64-5b1b97698b2e"
# The two fork sessions: one whose fork carries no turn at all, one whose fork replays the
# turn its origin ran.
FORK_BYREF = "07a769d7-828c-4edb-b3ce-af51e2712aa3"
FORK_ORIGIN = "5a88789c-1da7-4f32-b631-40a7e243334b"
# The session that records no main turn and no agent run: nothing to describe, so enrichment
# skips it rather than sending an empty prompt.
DUP_UUID = "8ee00a94-b01a-4394-b447-b065f74b11af"
# The session holding a main turn whose last api call stopped `end_turn` — the one recorded
# value the `Ended:` line exists for, and no other enrichment fixture carries it.
LEGACY_TITLE = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba"
# `resume_pair/`'s ancestor session and the plain turn its `local-command-stdout` record hangs
# off: the recorded negative for the archive read, since a command's output attached to a turn
# that ran no command belongs to no turn's prompt. 183 recorded records are in this shape.
RESUME_ANCESTOR = "2352492b-1437-4427-ad51-70f35c75f663"
RESUME_PLAIN_TURN = "55309e59-0fae-4ef1-9251-877e27487bda"
# The resume itself: every api call it holds sits under no turn of its own, so it is the third
# session with nothing of its own to describe.
RESUME = "0a76f771-5f5b-447e-852a-664fc972ea7c"
# The agent runs the render and rounds leaves are built on, one per shape: a multi-turn
# teammate, a subagent and the leaf it spawned in turn, a turnless fork, a fork whose only
# turn is a replay, and the run that really ran that turn.
TEAM_RUN = "aarchitect-5144001ac50718bc"
SPINE_RUN = "ac461ef46b4bb8e32"
SPINE_LEAF = "af6473ae437c9608d"
BYREF_RUN = "afa3946951a08a798"
WORKFLOW_RUN = "a6f04bb0e6eff6013"
ORIGIN_RUN = "a61a059e3610e6fb4"
AUDITOR_RUN = "acbc29008a04b9702"
ENRICHMENT_FIXTURES = (
    "spine",
    "server_tools",
    "workflow",
    "teammate",
    "fork_byref",
    "fork_origin",
    "compaction",
    "dup_uuid",
    "model_only",
    "legacy_title",
    "resume_pair",
)


def stamp(input_hash: str = "hash-1") -> Stamp:
    """What a planted row was written under, at a version nothing reads as drift."""
    return Stamp(
        input_hash=input_hash,
        prompt_version=1,
        taxonomy_version=TAXONOMY_VERSION,
        model=MODEL,
    )


def enrichment(description: str = "Read two files and ran the suite.") -> Enrichment:
    """What a planted row says. Invented, as any model answer in a test must be."""
    return Enrichment(
        description=description,
        category=Category.test,
        outcome=Outcome.completed,
        friction=None,
    )


def session_item(session_id: str) -> SessionItem:
    """A session item built by hand, for a session the store will not hand one out for."""
    return SessionItem(
        session_id=session_id,
        title=None,
        git_branch=None,
        wall_ms=None,
        active_ms=None,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=0.0,
        children=(),
    )


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


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The recorded fixtures as one trace store. Read from it; copy it before writing."""
    path = tmp_path_factory.mktemp("enrichment") / "traces.duckdb"
    build_store(path, fixture_transcripts(*ENRICHMENT_FIXTURES))
    return path


@pytest.fixture
def mutable_db(fixture_db: Path, tmp_path: Path) -> Path:
    """A private copy of `fixture_db`, for tests that enrich, plant, or delete rows."""
    copy = tmp_path / "traces.duckdb"
    shutil.copy(fixture_db, copy)
    return copy


# The opt-in for anything that really runs a process. Set it and the `live` tests run.
LIVE_CLI = "HYPHAE_LIVE_CLI"


class SubprocessForbidden(Exception):
    """A test tried to start a process.

    A plain `Exception`: nothing in the enrichment path catches broadly, so this reaches the
    test runner as itself.
    """


@pytest.fixture(autouse=True)
def refuse_subprocess(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any process an enrichment test starts raise, unless the test is marked `live`.

    `CliClient` spends the subscription allowance one `claude -p` at a time, so an accidental
    real call is billed work that looks exactly like a passing test. Both doors are shut:
    `subprocess.run` is the one the client uses, and `Popen` is a door already in use
    elsewhere in the suite (`tests/conftest.py`).
    """
    if request.node.get_closest_marker("live"):
        return

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise SubprocessForbidden(
            "a test tried to start a process — enrichment tests fake `subprocess.run`; "
            "mark the test `live` if it is the opt-in check"
        )

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, refuse_subprocess: None) -> Callable[..., FakeCli]:
    """Install a `FakeCli` over the guard, so the test's own seam is the one in place."""

    def install(replies: Mapping[str, Reply], *, gate: Callable[[str], None] | None = None):
        return FakeCli(replies, gate=gate).install(monkeypatch)

    return install


@pytest.fixture
def refuse_binary(monkeypatch: pytest.MonkeyPatch, refuse_subprocess: None) -> None:
    """A machine with no `claude` on PATH, as `subprocess.run` reports one."""

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", CLAUDE)

    monkeypatch.setattr(subprocess, "run", missing)
