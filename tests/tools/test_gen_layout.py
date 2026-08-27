"""What the Layout tree in `CLAUDE.md` has to hold: real paths, every tracked one, lifted words.

The tree is the index a reader of `CLAUDE.md` navigates the repo by, so its failure modes are
a path that has moved and a directory nobody added. Both are properties of the live tree, which
is what these leaves read; the order of the entries is an editorial choice and is not asserted.
"""

import subprocess
from pathlib import Path

import pytest

import hyphae.extract
from tools import gen_layout

ROOT = Path(__file__).resolve().parents[2]


def tracked_directories() -> set[str]:
    """Every top-level directory git tracks something under."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return {f"{path.split('/')[0]}/" for path in listed if "/" in path}


def test_every_entry_is_a_path_that_exists_or_is_gitignored_scratch() -> None:
    # A tree naming a path that moved is worse than no tree. The two scratch directories are
    # the exception the rule allows: they are gitignored, so a fresh clone has neither.
    for entry in gen_layout.ENTRIES:
        if (ROOT / entry.path).exists():
            continue
        ignored = subprocess.run(["git", "check-ignore", entry.path], cwd=ROOT, check=False)
        assert ignored.returncode == 0, f"`{entry.path}` is neither in the repo nor ignored"
        assert entry.scratch, f"`{entry.path}` does not exist and is not marked as scratch"


def test_every_tracked_top_level_directory_is_in_the_tree_or_named_as_left_out() -> None:
    # The ratchet: a new directory of the repo reaches the tree, or says here why it does not.
    named = {entry.path.split("/")[0] + "/" for entry in gen_layout.ENTRIES}
    undocumented = tracked_directories() - named - set(gen_layout.UNLISTED)
    assert not undocumented, f"directories the tree does not mention: {sorted(undocumented)}"


def test_nothing_is_named_as_left_out_that_the_repo_no_longer_holds() -> None:
    # And the excuses are pruned with what they excused.
    assert set(gen_layout.UNLISTED) <= tracked_directories()


def test_every_project_document_is_in_the_tree() -> None:
    # `docs/` is the tree's longest branch and the one that grows: a document nobody lists is
    # a document nobody finds, which is the whole reason the index exists.
    listed = {entry.path for entry in gen_layout.ENTRIES}
    assert {str(path.relative_to(ROOT)) for path in (ROOT / "docs").glob("*.md")} <= listed


def test_a_gloss_is_the_packages_own_first_sentence() -> None:
    # The docstring beside the code is the single source, so the tree cannot drift from it
    # without the package drifting first.
    glossed = dict(gen_layout.lines())
    docstring = hyphae.extract.__doc__ or ""
    lifted = glossed["  extract/"]
    assert lifted and docstring.strip().startswith(lifted)
    # And it is the *first sentence* of it, not the whole paragraph: what follows the gloss in
    # the docstring is either nothing or the rest of the prose, past the period.
    assert docstring.strip().removeprefix(lifted) in ("", ".") or docstring.strip().removeprefix(
        lifted
    ).startswith(". ")


def test_a_package_with_no_docstring_crashes_the_generator() -> None:
    # A silent blank in the tree would read as a package nobody could describe.
    with pytest.raises(ValueError, match=r"tests\.tools\.undescribed"):
        gen_layout.glossed(gen_layout.Module("tests.tools.undescribed"))


def test_the_tree_is_fenced_and_ends_without_its_own_newline() -> None:
    # The fence is inside the cog block, because a marker inside a fence is an example rather
    # than a live block — so the generator owns both fence lines and neither newline around them.
    tree = gen_layout.generate()
    assert tree.startswith("```\n")
    assert tree.endswith("\n```")
