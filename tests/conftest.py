"""Scaffolding shared by every tier: the recorded fixtures, and traces built from them.

The fixtures sit at `tests/fixtures/` rather than beside the extractor tests because the
exporter and pipeline tests want the same recorded sessions — a trace built from a real
transcript is better evidence than one assembled by hand. Each fixture directory's README
names its source session and Claude Code version.
"""

import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest

from aiobserve.enrich.prompts import PROMPT_VERSION, Level
from aiobserve.enrich.store import EnrichmentStore, Stamp
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION, Category, Outcome
from aiobserve.enrich.validation import Enrichment
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.model import SessionTrace
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session

FIXTURES = Path(__file__).parent / "fixtures"

# The project every recorded fixture was captured under. `tests/fixtures/*/README.md` names
# the session behind each one.
MYCELIA = "/Users/nob/repos/mycelia"

# The six transcripts under `invented/` that carry unknown record shapes crash on export by
# design, so the corpus takes the two that do not by name. They are the only fixtures
# recorded under another project, which is what makes the corpus predicate testable.
CLEAN_INVENTED = ("invented-no-cache-creation", "invented-truncated-tail")
# `/invented/project` and `/repo` respectively — outside the corpus whatever `--project` says.
INVENTED_PROJECT_SESSION = "invented-no-cache-creation"
OTHER_PROJECT_SESSION = "invented-truncated-tail"
# `fork_byref`'s fork: NULL `project_dir` and NULL `started_at`, the recorded twin of the
# store's zero-cost bookkeeping stubs. The corpus predicate cannot judge it either way.
NO_PROJECT_SESSION = "07a769d7-828c-4edb-b3ce-af51e2712aa3"
NON_CORPUS = (INVENTED_PROJECT_SESSION, OTHER_PROJECT_SESSION, NO_PROJECT_SESSION)

# Sessions the tiers below name. `spine/` is the deepest run tree; `resume_pair/` holds the
# resume whose api calls all sit under no turn; `server_tools/` carries an agent-source call
# with no turn either.
SPINE = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"
SPINE_RUN = "ac461ef46b4bb8e32"
SPINE_LEAF = "af6473ae437c9608d"
RESUME = "0a76f771-5f5b-447e-852a-664fc972ea7c"
# The line of `RESUME`'s longest recorded raw record, 3,054 chars — the one record past the
# `records_slice` cap.
RESUME_LONG_RECORD = 5
SERVER_TOOLS = "088d63aa-71d3-4108-965e-5147e3eaddbd"
# `server_tools/`'s one agent source, which carries a NULL-`turn_id` api call outside `main`.
SERVER_TOOLS_RUN = "a3b37063695183556"
# The source name of a session's own thread.
MAIN = "main"
# The two sessions `worktree_db` re-exports under a planted `project_dir`, chosen because no
# other leaf asserts on them.
WORKTREE_SESSION = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba"
SIBLING_SESSION = "4b443ab7-98f8-4c1d-859f-9bdcafbabdd3"

# The densest shapes the corpus records, which the viewer's paging leaves page *below* so
# that a page boundary is a real overflow of recorded data rather than a staged one:
# `DENSE_TURN` is `ANCESTOR`'s main-thread turn holding 4 api calls, and `DENSE_CALL` is the
# api call in `FORK_ORIGIN_RUN` holding 4 tool calls.
DENSE_TURN = "55309e59-0fae-4ef1-9251-877e27487bda"
DENSE_TURN_CALL = "msg_011Ccs78BfVLQfyQqhkxnpkm"
DENSE_CALL = "msg_011CdFxfStgUUn3Q59b4RFii"
DENSE_TOOL = "toolu_015wiqbosE2nUYZBYdd9urjA"

# `ANCESTOR` is the session `RESUME` resumed, and one of the two pool sessions that compacted.
# `FORK_ORIGIN` holds the fork whose spawning call sits in the fork's own transcript:
# `FORK_ORIGIN_RUN` is the run that spawned it — the fork's `parent_agent_id`, and the source
# `DENSE_CALL` was made from. `BYREF_FORK` is the second recorded fork, the one whose own
# api calls sit under no turn.
ANCESTOR = "2352492b-1437-4427-ad51-70f35c75f663"
FORK_ORIGIN = "5a88789c-1da7-4f32-b631-40a7e243334b"
FORK_ORIGIN_RUN = "acbc29008a04b9702"
FORK_RUN = "a61a059e3610e6fb4"
BYREF_FORK = "afa3946951a08a798"
REGISTRY_ZOO = "registry-zoo-0000-0000-0000-000000000000"
# The pool session no other leaf asserts on, so a copied store can strip its api calls and
# leave it the shape a `/model`-only session has: one turn, nothing the model answered.
CONFIG_ONLY = "7e37bb35-4dcb-4e16-85be-55ac510c168e"
# The corpus's one offloaded tool result — Claude Code wrote the output to a file beside the
# transcript instead of into it. `CONFIG_ONLY` recorded it: a 159-character file, and the tool
# call it belongs to. The name is Claude Code's, not ours, which is the reason it is untrusted.
OFFLOAD_FILE = "bosvr1kjx.txt"
OFFLOAD_CHARS = 159
OFFLOAD_TOOL = "toolu_01JXs55LXLHvzWt8KczuYfyD"
# The `deep-research` user, and the only session `pr-and-document` reaches from the pool.
DEEP_RESEARCH_SESSION = "8d930c77-9e60-4784-9885-6d4c226280f7"
# `teammate/`'s session and the `architect` run the team mechanism started inside it: the
# corpus's one orphan, a run with no spawning tool call behind it.
TEAMMATE = "10d0349d-0705-4e23-aa64-5b1b97698b2e"
TEAMMATE_RUN = "aarchitect-5144001ac50718bc"
# `compaction/`'s session, which holds two recorded main-thread compactions.
COMPACTED = "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"

# What the planted enrichment rows say. Invented, and it has to be: the four fields are a
# model's words about a private transcript, and no fixture records one. The tiers under test
# group, draw on and render these values rather than reading them, so what matters is that
# the cycles vary independently — see `enriched_db`.
PLANTED_CATEGORIES = (Category.implement, Category.test, Category.debug)
PLANTED_OUTCOMES = (Outcome.completed, Outcome.partial, Outcome.failed)
PLANTED_MODELS = ("claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929")

SourceFactory = Callable[[str, str], SessionSource]
TraceFactory = Callable[[str, str], SessionTrace]
PlantedFactory = Callable[[str, str, dict[str, str]], SessionSource]


def fixture_transcripts(*directories: str) -> tuple[Path, ...]:
    """Every recorded transcript under the named fixture directories, in a stable order."""
    return tuple(
        transcript
        for directory in directories
        for transcript in sorted((FIXTURES / directory).glob("*.jsonl"))
    )


def build_store(path: Path, transcripts: Iterable[Path]) -> None:
    """Extract each transcript into a store at `path`, as `refresh()` would.

    Tiers that query the store want their evidence to be rows the real pipeline wrote, so
    they build one from recorded transcripts rather than inserting rows by hand. Building
    costs an extraction per transcript — build once per test session and copy the file for
    any test that plants or deletes rows.
    """
    with DuckDbExporter(path) as exporter:
        for transcript in transcripts:
            session = Session(id=transcript.stem, transcript=transcript)
            source = SessionSource(
                id=transcript.stem, files=tuple(session.files()), fingerprint="fixture"
            )
            exporter.export(ClaudeCodeExtractor().extract(source), source.fingerprint)


def corpus_transcripts() -> tuple[Path, ...]:
    """Every fixture transcript that exports cleanly, discovered rather than listed."""
    directories = sorted(
        path.name for path in FIXTURES.iterdir() if path.is_dir() and path.name != "invented"
    )
    invented = tuple(FIXTURES / "invented" / f"{stem}.jsonl" for stem in CLEAN_INVENTED)
    return fixture_transcripts(*directories) + invented


def exportable_transcripts() -> tuple[Path, ...]:
    """Every fixture transcript the OTLP source filter can place, discovered rather than listed.

    `fork_byref/`'s session records no `project_dir` and holds rows, so listing a store that
    holds it is a crash by design (`plans/otlp-export/design.md`). A store meant to be listed
    or exported leaves that one transcript out and keeps everything else.
    """
    return tuple(
        transcript for transcript in corpus_transcripts() if transcript.stem != NO_PROJECT_SESSION
    )


# What a writer does to the store: opens it read-write and holds it. The connection has to
# stay referenced — an unnamed one is freed at once, and the lock goes with it.
_HOLDER = "import duckdb, sys, time; held = duckdb.connect(sys.argv[1]); time.sleep(30)"

# How long to wait for that subprocess to take the lock before giving up on the test.
LOCK_TIMEOUT = 10.0


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold a store's write lock from another process for the length of the block.

    A subprocess, not a second connection here: DuckDB answers the same process's second
    open differently from the file lock it takes across processes, so an in-process holder
    tests the wrong failure.
    """
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path)])
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                duckdb.connect(str(path), read_only=True).close()
            except duckdb.IOException:
                break
            if time.monotonic() > deadline:
                pytest.fail(f"nothing took the lock on {path} within {LOCK_TIMEOUT}s")
            time.sleep(0.05)
        yield
    finally:
        holder.terminate()
        holder.wait(timeout=LOCK_TIMEOUT)


@pytest.fixture(scope="session")
def corpus_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture corpus as one trace store: 13 mycelia sessions and three outside them.

    Built once for the whole run and read by every tier that queries a store — the analysis
    queries and the viewer's routes ask their questions of the same 16 sessions. Read-only:
    a test that plants or deletes a row copies the file first.
    """
    path = tmp_path_factory.mktemp("corpus") / "traces.duckdb"
    build_store(path, corpus_transcripts())
    return path


@pytest.fixture(scope="session")
def exportable_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture corpus minus the session the OTLP source filter refuses to place.

    `fork_byref/`'s session carries no `project_dir` and holds rows, so `StoreSource.sessions()`
    crashes on any store holding it — by design. A store meant to be listed or shipped leaves
    that one out. Read-only: copy the file before planting a row.
    """
    path = tmp_path_factory.mktemp("exportable") / "traces.duckdb"
    build_store(path, exportable_transcripts())
    return path


@pytest.fixture(scope="session")
def enriched_db(corpus_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture corpus with an enrichment row on every item but the last of each level.

    The pipeline writes no enrichment row, so anything that reads one — the enrichment
    queries, the viewer's pages — has nothing to read until a pass has run. Rows go in
    through `EnrichmentStore.upsert` over the items the store itself lists, so the keys are
    the ones a real pass writes; only the four model-written fields are invented, and they
    have to be — no fixture records a model answer. The last item of each level is left
    undescribed, which is both the gap coverage reports and the partly-enriched store the
    viewer has to render.
    """
    path = tmp_path_factory.mktemp("enriched") / "traces.duckdb"
    path.write_bytes(corpus_db.read_bytes())
    with EnrichmentStore(path) as store:
        for level in Level:
            for index, item in enumerate(store.items(level)[:-1]):
                store.upsert(item, planted_enrichment(index), planted_stamp(level, index))
    return path


def planted_enrichment(index: int) -> Enrichment:
    """What the planted row says, cycling so a distribution has something to distribute."""
    return Enrichment(
        description=f"Planted description {index}.",
        category=PLANTED_CATEGORIES[index % len(PLANTED_CATEGORIES)],
        outcome=PLANTED_OUTCOMES[(index // len(PLANTED_CATEGORIES)) % len(PLANTED_OUTCOMES)],
        # Every fourth row, which is coprime with both cycles above: friction that tracked a
        # category could not tell a count of one from a count of the other.
        friction="Planted friction." if index % 4 == 0 else None,
    )


def planted_stamp(level: Level, index: int) -> Stamp:
    """What the planted row was written under — real versions, so nothing reads as drift."""
    return Stamp(
        input_hash=f"planted-{level}-{index}",
        # A version behind on every fifth row: the stamp breakdown splits on the model and on
        # the prompt version, axes that moved together could not say which, and the viewer's
        # stale tag needs a row on each side of the current version.
        prompt_version=PROMPT_VERSION[level] - (1 if index % 5 == 0 else 0),
        taxonomy_version=TAXONOMY_VERSION,
        model=PLANTED_MODELS[index % len(PLANTED_MODELS)],
    )


@pytest.fixture
def fixture_source() -> SourceFactory:
    """Build a `SessionSource` over one fixture session, the way `sessions()` would.

    The whole session directory, not just the transcript: subagent transcripts, workflow
    journals and offloaded tool outputs are part of the session, and a builder that
    skipped them would let a test pass on files the real pipeline never sees.

    Fingerprints belong to discovery, not parsing, so the value here is a placeholder —
    `extract()` never reads it.
    """

    def build(directory: str, stem: str) -> SessionSource:
        session = Session(id=stem, transcript=FIXTURES / directory / f"{stem}.jsonl")
        return SessionSource(
            id=stem, files=tuple(session.files()), fingerprint="fixture-fingerprint"
        )

    return build


@pytest.fixture
def planted_source(tmp_path: Path) -> PlantedFactory:
    """A fixture session copied into `tmp_path`, with extra files in its directory.

    The transcript is the recorded one; only the planted file *names* are invented, which is
    the point — they stand for layouts Claude Code writes, or might write next.
    """

    def build(directory: str, stem: str, files: dict[str, str]) -> SessionSource:
        transcript = tmp_path / f"{stem}.jsonl"
        shutil.copy(FIXTURES / directory / transcript.name, transcript)
        for relative, content in files.items():
            path = tmp_path / stem / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        session = Session(id=stem, transcript=transcript)
        return SessionSource(id=stem, files=tuple(session.files()), fingerprint="planted")

    return build


@pytest.fixture
def fixture_trace(fixture_source: SourceFactory) -> TraceFactory:
    """Extract one fixture transcript, for tests that need a trace but not the parsing."""

    def build(directory: str, stem: str) -> SessionTrace:
        return ClaudeCodeExtractor().extract(fixture_source(directory, stem))

    return build
