"""Scaffolding for the enrichment tier: a store built from recorded fixtures, and no network.

The enrichment renders read rows, so their evidence is a real DuckDB built by running the
existing pipeline over `tests/fixtures/` — the same keys the pipeline really writes. Building
it costs an extraction per fixture, so `fixture_db` builds once per test session and
`mutable_db` hands out a copy to any test that plants or deletes rows.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import build_store, fixture_transcripts

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
# The two sessions that record no main turn and no agent run: nothing to describe, so
# enrichment skips them rather than sending an empty prompt.
COMPACTION = "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"
DUP_UUID = "8ee00a94-b01a-4394-b447-b065f74b11af"
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
)


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


class LiveApiForbidden(BaseException):
    """A test tried to reach the network.

    Deliberately not an `Exception`: the Anthropic SDK catches `Exception` around every
    request, wraps it as a connection error and retries it, which would both hide this
    message and make an accidental live call slow rather than loud.
    """


@pytest.fixture(autouse=True)
def refuse_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any HTTP the Anthropic SDK attempts raise, unless the test is marked `live`.

    "No test calls the real API" is otherwise a convention held up by review alone: nothing
    stops a test from constructing a real client, and a billed call that quietly succeeds
    looks exactly like a passing test.
    """
    if request.node.get_closest_marker("live"):
        return

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise LiveApiForbidden(
            "a test tried to reach the network — enrichment tests drive a fake BatchClient; "
            "mark the test `live` if it is the opt-in check"
        )

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)


@pytest.fixture
def anthropic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A stand-in API key in the environment, so a test reaches past key validation."""
    key = "sk-ant-fixture-key-not-real"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    yield key
