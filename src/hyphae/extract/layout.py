"""Claude Code's layout on disk: where a project's sessions are, and what files hold one.

Claude Code writes one JSON-lines transcript per session, under a directory named for the
session's working directory:

    <projects_root>/<encoded-cwd>/<session-id>.jsonl
    <projects_root>/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl

This module finds those files and sorts them by what reads them. It parses none of them —
records belong to `extract/transcript.py`, and the two rot on different schedules: the layout
is stable, the record shapes are not (`docs/schema.md`). The one file it reads whole is an
offloaded tool result, which is text rather than records.

The layout is closed-world like the record registry: a file whose place we cannot name raises
`SessionLayoutError` (`extract/errors.py`) rather than being skipped.

What a project *is* to the layers that query the store is `hyphae/projects.py`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from hyphae.extract.errors import SessionLayoutError
from hyphae.model import OffloadFile
from hyphae.projects import encode_project_path

# Where Claude Code keeps transcripts. The tree is shared across accounts —
# ~/.claude-black/projects is a symlink to this one — so a transcript's path says
# nothing about which account produced it.
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# The names Claude Code gives the files inside a session's directory. Parsing reads these
# too — a file's place in the tree says which transcript it is — so they live here with
# the walk rather than being spelled out twice.
TRANSCRIPT_SUFFIX = ".jsonl"
SUBAGENTS_DIR = "subagents"
# Under `subagents/`, one directory per parallel fan-out. At the top of the session
# directory, the definitions and scripts of those workflows.
WORKFLOWS_DIR = "workflows"
WORKFLOW_PREFIX = "wf_"
TOOL_RESULTS_DIR = "tool-results"
AGENT_PREFIX = "agent-"
META_SUFFIX = ".meta.json"
JOURNAL_NAME = "journal.jsonl"
# The session's title, written beside the transcript that also holds it as a `custom-title`
# record. Nothing reads the sidecar: `extract/transcript.py` takes the title from the records.
TITLE_SIDECAR = "custom-title.json"

# The `source` a workflow journal records under, after its `wf_<id>/` directory.
JOURNAL_SOURCE = "journal"


@dataclass(frozen=True)
class SessionFiles:
    """One recorded Claude Code session, as the files that hold it.

    Where a session's records live, not what they say: `model.Session` is the parsed row.
    """

    # The session UUID, taken from the transcript's filename.
    id: str
    # The session's own JSONL transcript. Subagent runs are NOT in here.
    transcript: Path

    def subagent_transcripts(self) -> list[Path]:
        """Every subagent transcript this session spawned, sorted by path.

        A subagent's work is part of the session but is recorded separately, so any
        accounting over a session that ignores these undercounts it. Empty for a
        session that spawned none.
        """
        subagents = self.directory / SUBAGENTS_DIR
        if not subagents.is_dir():
            return []
        # rglob, not glob: a parallel fan-out nests its agents under workflows/wf_<id>/.
        return sorted(subagents.rglob(f"{AGENT_PREFIX}*{TRANSCRIPT_SUFFIX}"))

    @property
    def directory(self) -> Path:
        """Where Claude Code keeps everything else this session wrote. May not exist."""
        return self.transcript.with_suffix("")

    def files(self) -> list[Path]:
        """Every file this session's records live in, sorted by path.

        A whole-directory walk rather than a list of known names: subagent transcripts,
        their metas, workflow journals, and offloaded tool results all sit under here, and
        Claude Code adds shapes we have not seen. Missing one would leave a session
        looking unchanged after it changed.
        """
        below = self.directory
        walked = sorted(p for p in below.rglob("*") if p.is_file()) if below.is_dir() else []
        return [self.transcript, *walked]


def find_sessions(
    project: Path, *, projects_root: Path = DEFAULT_PROJECTS_ROOT
) -> list[SessionFiles]:
    """Every session recorded for `project`, sorted by session id.

    Raises `FileNotFoundError` when the project has no directory under `projects_root`
    — that means it was never opened in Claude Code, which is a typo in the path far
    more often than it is a real empty corpus, and an empty list would hide it.
    """
    project_dir = projects_root / encode_project_path(project)
    if not project_dir.is_dir():
        raise FileNotFoundError(
            f"No Claude Code sessions for {project} — expected "
            f"{encode_project_path(project)!r} under {projects_root}"
        )
    # Non-recursive on purpose: the per-session subdirectories hold subagent runs and
    # tool results, which belong to a session rather than being one.
    return [
        SessionFiles(id=transcript.stem, transcript=transcript)
        for transcript in sorted(project_dir.glob(f"*{TRANSCRIPT_SUFFIX}"))
    ]


class AgentFiles(NamedTuple):
    """One subagent's pair of files, and where the pair sat."""

    # The agentId: the file stem after `agent-`, and the `source` its records take.
    id: str
    # The `wf_<id>` fan-out directory it sat in, for the runs a workflow drove.
    workflow_id: str | None
    transcript: Path
    meta: Path


class ClassifiedFiles(NamedTuple):
    """One session's files, sorted by what reads them."""

    transcript: Path
    agents: list[AgentFiles]
    # Each workflow journal, paired with its `wf_<id>/journal` source. Archive only: the
    # runs it logs write their own transcripts.
    journals: list[tuple[str, Path]]
    offloads: list[Path]


def classify(session: SessionFiles) -> ClassifiedFiles:
    """Sort a session's files by what reads them. An unplaceable file stops the run."""
    transcript = session.transcript
    directory = session.directory
    # Each agent's two files arrive independently; they are paired once both are seen.
    transcripts: dict[str, Path] = {}
    metas: dict[str, Path] = {}
    workflows: dict[str, str | None] = {}
    journals: list[tuple[str, Path]] = []
    offloads: list[Path] = []
    for path in session.files():
        if path == transcript:
            continue
        parts = path.relative_to(directory).parts
        if parts[:1] == (TOOL_RESULTS_DIR,) and len(parts) == 2:
            offloads.append(path)
            continue
        # A workflow's definition and the script that ran it, beside the runs they drove.
        if parts[:1] == (WORKFLOWS_DIR,):
            continue
        if parts == (TITLE_SIDECAR,):
            continue
        place = _companion(parts, session.id)
        if place.agent_id is None:
            journals.append((f"{place.workflow_id}/{JOURNAL_SOURCE}", path))
            continue
        (metas if place.meta else transcripts)[place.agent_id] = path
        workflows[place.agent_id] = place.workflow_id
    if transcripts.keys() != metas.keys():
        odd = transcripts.keys() ^ metas.keys()
        raise SessionLayoutError(
            f"Session {session.id}: agent runs {sorted(odd)} have a transcript or a meta, not both"
        )
    agents = [
        AgentFiles(
            id=agent, workflow_id=workflows[agent], transcript=transcripts[agent], meta=metas[agent]
        )
        for agent in transcripts
    ]
    return ClassifiedFiles(
        transcript=transcript, agents=agents, journals=journals, offloads=offloads
    )


class _Companion(NamedTuple):
    """Where one file under the session directory sits, and what it is."""

    # The `wf_<id>` directory it sat in, when a fan-out wrote it.
    workflow_id: str | None
    # The agentId its name carries, or None for a workflow's journal.
    agent_id: str | None
    # The `.meta.json` beside a subagent's transcript rather than the transcript.
    meta: bool


def _companion(parts: tuple[str, ...], session_id: str) -> _Companion:
    """Place one file under `subagents/`. A file we cannot place stops the run."""
    unknown = SessionLayoutError(
        f"Session {session_id}: unknown file {'/'.join(parts)} in its directory"
    )
    workflow = None
    if parts[:2] == (SUBAGENTS_DIR, WORKFLOWS_DIR):
        if len(parts) != 4 or not parts[2].startswith(WORKFLOW_PREFIX):
            raise unknown
        workflow = parts[2]
    elif parts[:1] != (SUBAGENTS_DIR,) or len(parts) != 2:
        raise unknown
    name = parts[-1]
    if workflow and name == JOURNAL_NAME:
        return _Companion(workflow_id=workflow, agent_id=None, meta=False)
    if not name.startswith(AGENT_PREFIX):
        raise unknown
    stem = name[len(AGENT_PREFIX) :]
    # `.meta.json` first: it is the longer suffix, and both end in "json".
    if stem.endswith(META_SUFFIX):
        return _Companion(workflow, stem[: -len(META_SUFFIX)], meta=True)
    if stem.endswith(TRANSCRIPT_SUFFIX):
        return _Companion(workflow, stem[: -len(TRANSCRIPT_SUFFIX)], meta=False)
    raise unknown


def read_offload_file(path: Path, session_id: str) -> OffloadFile:
    """One `tool-results/` file, read whole — it is the only copy once Claude Code prunes."""
    data = path.read_bytes()
    try:
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(),
            lossy_decode=False,
            size_bytes=len(data),
        )
    except UnicodeDecodeError:
        # Not text at all — a fetched PDF — or text cut mid-character. Archived anyway:
        # the file is gone in a few weeks, and its size and name still say what ran.
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(errors="replace"),
            lossy_decode=True,
            size_bytes=len(data),
        )
