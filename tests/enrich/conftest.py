"""Scaffolding for the enrichment tier: a store built from recorded fixtures, and no `claude`.

The enrichment renders read rows, so their evidence is a real DuckDB built by running the
existing pipeline over `tests/fixtures/` — the same keys the pipeline really writes. Building
it costs an extraction per fixture, so `fixture_db` builds once per test session and
`mutable_db` hands out a copy to any test that plants or deletes rows.
"""

import shutil
import subprocess
from pathlib import Path

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
# The session holding a main turn whose last api call stopped `end_turn` — the one recorded
# value the `Ended:` line exists for, and no other enrichment fixture carries it.
LEGACY_TITLE = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba"
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


# The opt-in for anything that really runs a process. Set it and the `live` tests run.
LIVE_CLI = "AIOBSERVE_LIVE_CLI"


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
