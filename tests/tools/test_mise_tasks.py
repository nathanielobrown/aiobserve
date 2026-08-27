"""What a task line in `mise.toml` has to keep saying when it names paths by hand.

A task that walks a bare `.` needs no gate: the tree is its argument. djLint's two tasks name
their paths instead — `mise.toml` says why — so the list is an enumeration that can rot, and
this is the leaf that reads it back against the tree.
"""

import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Both halves of the formatter: the writer `check-fast` runs and the gate `check` runs. They
# carry the same list because djLint has no config key for it, so a path dropped from either
# one is a template nobody formats or nobody checks.
HTML_TASKS = ("format-html", "format-html-check")


def walked(task: str) -> list[Path]:
    """The paths a djLint task hands the binary: the arguments between it and its first flag."""
    run = tomllib.loads((ROOT / "mise.toml").read_text())["tasks"][task]["run"]
    words = shlex.split(run)
    after = words[words.index("djlint") + 1 :]
    named = []
    for word in after:
        if word.startswith("-"):
            break
        named.append(ROOT / word)
    assert named, f"`{task}` hands djLint no path at all"
    return named


@pytest.mark.parametrize("task", HTML_TASKS)
def test_every_template_in_the_tree_is_one_the_formatter_walks(task: str) -> None:
    """No tracked `.html` file sits outside the paths djLint is pointed at.

    The formatter's claim is one canonical layout for every template, and the list that
    delivers it is hand-written and duplicated. Without this, a template added anywhere but
    under `view/templates/` is simply unformatted, and every gate stays green about it: the
    check reports the files it was given and finds no fault in them.
    """
    listed = walked(task)
    tracked = subprocess.run(
        ["git", "ls-files", "*.html"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    # The tree holds templates to begin with, so a `git ls-files` that came back empty — a
    # renamed extension, a broken checkout — cannot pass this by having nothing to say.
    assert len(tracked) > 10
    for name in tracked:
        template = ROOT / name
        assert any(template == path or path in template.parents for path in listed), (
            f"`{name}` is under no path `{task}` walks, so the formatter never sees it"
        )
