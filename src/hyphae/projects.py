"""What "a project" means to every layer: the path one resolves to, and the SQL that matches it.

A project is the absolute, symlink-free working directory a session ran in. A path typed at a
command line is none of those things yet, so everything that takes one resolves it here before
comparing it against a recorded `cwd`, and every filter that narrows to a project builds its
clause here.

Where Claude Code keeps the files a project's sessions were recorded in is
`extract/layout.py` — the same word, one layer down.
"""

import contextlib
from pathlib import Path


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
