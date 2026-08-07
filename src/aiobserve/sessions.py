"""Finding the Claude Code sessions recorded for a project.

Claude Code writes one JSON-lines transcript per session, under a directory named
for the session's working directory:

    <projects_root>/<encoded-cwd>/<session-id>.jsonl
    <projects_root>/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl

This module locates those files. It does not read them — parsing owns the records,
and the two rot on different schedules: the layout is stable, the record shapes are
not (`docs/schema.md`).
"""

from dataclasses import dataclass
from pathlib import Path

# Where Claude Code keeps transcripts. The tree is shared across accounts —
# ~/.claude-black/projects is a symlink to this one — so a transcript's path says
# nothing about which account produced it.
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def encode_project_path(project: Path) -> str:
    """Claude Code's directory name for a project: its absolute path, each `/` replaced by `-`.

    So `/Users/nob/repos/mycelia` becomes `-Users-nob-repos-mycelia`. The leading dash
    is the encoded root separator, not a prefix.
    """
    return str(project.resolve()).replace("/", "-")


@dataclass(frozen=True)
class Session:
    """One recorded Claude Code session."""

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
        subagents = self.transcript.with_suffix("") / "subagents"
        if not subagents.is_dir():
            return []
        # rglob, not glob: a parallel fan-out nests its agents under workflows/wf_<id>/.
        return sorted(subagents.rglob("agent-*.jsonl"))


def find_sessions(project: Path, *, projects_root: Path = DEFAULT_PROJECTS_ROOT) -> list[Session]:
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
        Session(id=transcript.stem, transcript=transcript)
        for transcript in sorted(project_dir.glob("*.jsonl"))
    ]
