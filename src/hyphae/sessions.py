"""Finding the Claude Code sessions recorded for a project.

Claude Code writes one JSON-lines transcript per session, under a directory named
for the session's working directory:

    <projects_root>/<encoded-cwd>/<session-id>.jsonl
    <projects_root>/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl

This module locates those files. It does not read them — parsing owns the records,
and the two rot on different schedules: the layout is stable, the record shapes are
not (`docs/schema.md`).

It also owns what "a project" means to everything downstream: the absolute path a typed
one resolves to, the directory name Claude Code encodes it as, and the SQL that matches
the sessions recorded under it.
"""

import contextlib
from dataclasses import dataclass
from pathlib import Path

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


def resolve_project(project: Path) -> Path:
    """The absolute path everything that names a project matches on.

    Claude Code records a session's `cwd` absolute and symlink-free (`docs/schema.md`), so a
    project typed at a command line has to be resolved before it is compared against one:
    every filter that takes a typed path goes through this, because the one that did not
    reported a clean export of nothing for `hp export-otlp mycelia`. A trailing slash
    needs no handling — `Path` drops it, so `mycelia/` and `mycelia` are already one
    repository — but `~` does, since a quoted one reaches us unexpanded.
    """
    return _actual_case(project.expanduser().resolve())


def _actual_case(path: Path) -> Path:
    """The path as the filesystem spells it, not as it was typed.

    `Path.resolve()` fixes `..` and symlinks but leaves case alone, and macOS's default
    filesystem is case-insensitive-but-preserving: two differently-cased spellings of a path
    open the same directory but are different strings. Claude Code records the directory's
    real spelling, so a typed path with the wrong case resolves to real files on disk yet
    matches nothing in the store — silently, since every read finds zero rows rather than
    raising. Walking the tree and matching each component case-insensitively against what
    `iterdir` actually returns closes that gap; it costs nothing on a case-sensitive
    filesystem, where the match is already exact.
    """
    corrected = Path(path.anchor)
    for part in path.relative_to(path.anchor).parts:
        # Nothing on disk to correct against past this point — keep the rest as typed.
        spelled = part
        with contextlib.suppress(StopIteration, FileNotFoundError, NotADirectoryError):
            spelled = next(
                entry.name for entry in corrected.iterdir() if entry.name.lower() == part.lower()
            )
        corrected /= spelled
    return corrected


def project_predicate(column: str, parameter: str = "?") -> str:
    """SQL matching the sessions a project recorded: its own, and those of its worktrees.

    A worktree checkout sits under the repository it was cut from and its sessions are the
    project's, so every filter that takes a project matches a path prefix rather than a path.
    Written once because the `/` is a trap: without it the predicate annexes every
    neighbouring checkout whose path merely begins with this one's.

    `parameter` names the placeholder, which appears twice — a positional `?` therefore binds
    the resolved project path twice. A session recording no `project_dir` matches nothing,
    which is what leaves it out of every project.
    """
    return f"({column} = {parameter} OR starts_with({column}, {parameter} || '/'))"


def encode_project_path(project: Path) -> str:
    """Claude Code's directory name for a project: its absolute path, each `/` replaced by `-`.

    So `/Users/nob/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The leading dash
    is the encoded root separator, not a prefix.
    """
    return str(resolve_project(project)).replace("/", "-")


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
