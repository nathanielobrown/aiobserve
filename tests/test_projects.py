from pathlib import Path

import pytest

from hyphae.projects import encode_project_path, resolve_project


def test_encode_project_path_matches_claude_codes_directory_name():
    """A project's directory name is its absolute path with each separator turned into a dash."""
    assert encode_project_path(Path("/Users/nob/repos/mycelia")) == "-Users-nob-repos-mycelia"


def test_encode_project_path_resolves_a_relative_path(tmp_path: Path, monkeypatch):
    """A relative path is resolved first — the directory is named after the absolute one."""
    # If the caller passes a path relative to the working directory...
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myproject").mkdir()
    # ...then the encoding is of the absolute path, so it matches what is on disk.
    encoded = encode_project_path(Path("myproject"))
    assert encoded == encode_project_path(tmp_path / "myproject")
    assert encoded.startswith("-")


def test_a_home_relative_project_names_what_the_shell_would_have_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quoted `~/repos/x` selects the same repository the unquoted spelling does."""
    # If `~` reaches us unexpanded — a quoted argument, or one read out of a config file...
    monkeypatch.setenv("HOME", str(tmp_path))
    # ...then it names the home directory, not a directory called `~` under the working one.
    assert resolve_project(Path("~/repos/mycelia")) == tmp_path.resolve() / "repos" / "mycelia"


def test_resolve_project_corrects_case_to_match_the_filesystem(tmp_path: Path) -> None:
    """A path typed in the wrong case still names the directory Claude Code recorded."""
    # If a project sits on disk under `repos/MyProject`...
    (tmp_path / "repos" / "MyProject").mkdir(parents=True)
    # ...then typing it as `REPOS/myproject` still resolves to the real spelling, since a
    # string comparison against the recorded `cwd` would otherwise match nothing.
    assert (
        resolve_project(tmp_path / "REPOS" / "myproject")
        == tmp_path.resolve() / "repos" / "MyProject"
    )
