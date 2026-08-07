"""Scaffolding for the analysis tier: the fixture corpus as one read-only trace store.

The queries are the subject here, so their evidence has to be rows the real pipeline wrote:
`analyze_db` extracts every recorded fixture transcript into one DuckDB file, once per test
session. Tests read it through `aiobserve query` and never write to it — a test that plants
a row copies the file first, as `worktree_db` does.
"""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from aiobserve import cli
from aiobserve.export.duckdb import DuckDbExporter
from aiobserve.extract.claude_code import ClaudeCodeExtractor
from aiobserve.pipeline import SessionSource
from aiobserve.sessions import Session
from tests.conftest import FIXTURES, build_store, fixture_transcripts

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

# Sessions the leaves below name. `spine/` is the deepest run tree; `resume_pair/` holds the
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
# The source name of a session's own thread, which is the scope `session_digest` covers.
MAIN = "main"
# The two sessions `worktree_db` re-exports under a planted `project_dir`, chosen because no
# other leaf asserts on them.
WORKTREE_SESSION = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba"
SIBLING_SESSION = "4b443ab7-98f8-4c1d-859f-9bdcafbabdd3"

# Measured on 2026-08-08 by building the store below: 13 mycelia sessions between
# 2026-06-30 and 2026-07-27, in five unevenly filled ISO weeks.
MYCELIA_SESSIONS = 13
WEEKS = {"2026-W27": 4, "2026-W28": 4, "2026-W29": 3, "2026-W30": 1, "2026-W31": 1}
# `$as_of` values with a window each side of the corpus: 2026-08-07 opens the trailing 28
# days at 2026-07-10 and covers 6 sessions; 2026-07-28 opens at 2026-06-30 and covers all 13.
AS_OF_PARTIAL = "2026-08-07"
IN_WINDOW_AT_PARTIAL = 6
AS_OF_WHOLE = "2026-07-28"


def analyze_transcripts() -> tuple[Path, ...]:
    """Every fixture transcript that exports cleanly, discovered rather than listed."""
    directories = sorted(
        path.name for path in FIXTURES.iterdir() if path.is_dir() and path.name != "invented"
    )
    invented = tuple(FIXTURES / "invented" / f"{stem}.jsonl" for stem in CLEAN_INVENTED)
    return fixture_transcripts(*directories) + invented


@pytest.fixture(scope="session")
def analyze_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture corpus as one trace store: 13 mycelia sessions and three outside them."""
    path = tmp_path_factory.mktemp("analysis") / "traces.duckdb"
    build_store(path, analyze_transcripts())
    return path


@dataclass(frozen=True)
class Output:
    """What one `aiobserve query` run printed, split by stream."""

    stdout: str
    stderr: str

    def csv_rows(self) -> list[list[str]]:
        """The `--csv` stdout as rows, header included."""
        return list(csv.reader(io.StringIO(self.stdout)))

    def column(self, name: str) -> list[str]:
        """One column of the `--csv` stdout, by header name."""
        header, *rows = self.csv_rows()
        index = header.index(name)
        return [row[index] for row in rows]


QueryRunner = Callable[..., Output]


def query(db: Path, capsys: pytest.CaptureFixture[str], name: str, *arguments: str) -> Output:
    """Run `aiobserve query` against one store, returning what it printed."""
    cli.main("query", name, "--db", str(db), *arguments)
    captured = capsys.readouterr()
    return Output(captured.out, captured.err)


@pytest.fixture
def run_query(analyze_db: Path, capsys: pytest.CaptureFixture[str]) -> QueryRunner:
    """Run `aiobserve query` against the fixture corpus, returning what it printed."""

    def run(name: str, *arguments: str) -> Output:
        return query(analyze_db, capsys, name, *arguments)

    return run


@pytest.fixture(scope="session")
def worktree_db(analyze_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The corpus plus two sessions whose `project_dir` is planted, not recorded.

    No recorded fixture sits under `<project>/.claude/worktrees/`, and none sits under a
    checkout that merely shares mycelia's path prefix. Both are re-exports of real traces
    with the one column replaced — the value is invented, the rest of the session is not.
    """
    path = tmp_path_factory.mktemp("worktree") / "traces.duckdb"
    path.write_bytes(analyze_db.read_bytes())
    with DuckDbExporter(path) as exporter:
        for directory, stem, project_dir in (
            ("legacy_title", WORKTREE_SESSION, f"{MYCELIA}/.claude/worktrees/planted"),
            ("legacy_entrypoint", SIBLING_SESSION, f"{MYCELIA}-old"),
        ):
            transcript = FIXTURES / directory / f"{stem}.jsonl"
            session = Session(id=stem, transcript=transcript)
            source = SessionSource(id=stem, files=tuple(session.files()), fingerprint="planted")
            trace = ClaudeCodeExtractor().extract(source)
            trace = replace(trace, session=replace(trace.session, project_dir=project_dir))
            exporter.export(trace, source.fingerprint)
    return path
