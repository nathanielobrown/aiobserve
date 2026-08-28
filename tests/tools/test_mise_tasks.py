"""What a task line in `mise.toml` has to keep saying when it names paths by hand.

A task that walks a bare `.` needs no gate: the tree is its argument. djLint's two tasks name
their paths instead — `mise.toml` says why — and the browser tier's three run inside one, so
those are enumerations that can rot, and this is the leaf that reads them back against the tree.
"""

import os
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

# How a task spells the repo root in its `dir`. mise resolves a bare relative path against the
# directory the caller was in, so this prefix is what makes the path mean one place.
CONFIG_ROOT = "{{config_root}}/"

# What `e2e-chromatic` says when it has no token to upload with.
REFUSAL = "CHROMATIC_PROJECT_TOKEN is missing or empty"

# What it runs when it has one: the Chromatic CLI over the archives `mise run e2e` wrote,
# reporting a changed page rather than failing the job while the baselines settle.
UPLOAD = "chromatic --playwright --exit-zero-on-changes"


def tasks() -> dict[str, dict]:
    """Every task `mise.toml` declares, as data."""
    return tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]


def walked(task: str) -> list[Path]:
    """The paths a djLint task hands the binary: the arguments between it and its first flag."""
    run = tasks()[task]["run"]
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


def test_every_task_that_runs_outside_the_root_names_a_directory_the_tree_holds() -> None:
    """A task's `dir` is a real directory, named from the repo root and not from a caller's cwd.

    The browser tier's tasks run inside `tests/e2e`, where its package manifest and its specs
    are. A `dir` that has moved fails the task at the point it tries to run — and a relative one
    would run wherever the reader happened to be standing, which is worse than failing.
    """
    named = {name: task["dir"] for name, task in tasks().items() if "dir" in task}
    # The file holds tasks with a `dir` to begin with, so this cannot pass by finding none.
    assert named, "no task in `mise.toml` names a `dir`, so this leaf is checking nothing"
    for name, spelling in named.items():
        assert spelling.startswith(CONFIG_ROOT), (
            f"`{name}` runs in `{spelling}`, which mise resolves against wherever it was called"
        )
        directory = ROOT / spelling.removeprefix(CONFIG_ROOT)
        assert directory.is_dir(), f"`{name}` runs in `{spelling}`, which the tree does not hold"


def uploader() -> Path:
    """The script `e2e-chromatic` runs, resolved through the task rather than typed twice here."""
    task = tasks()["e2e-chromatic"]
    directory = ROOT / task["dir"].removeprefix(CONFIG_ROOT)
    script = (directory / task["run"]).resolve()
    assert script.is_file(), (
        f"`e2e-chromatic` runs `{task['run']}`, which {directory} does not hold"
    )
    return script


def test_the_chromatic_upload_refuses_to_run_on_an_empty_token() -> None:
    """`mise run e2e-chromatic` stops and names the variable when it holds no token.

    The upload is the one thing in this repo that carries a credential to a third party, so the
    failure a reader must never see is a run that starts, reaches the network, and then says it
    was not authorized. The whole task is run here, not the script alone, because the guard is
    only worth anything if it stands in the way of the command a reader actually types.
    """
    # If the task is run with the token explicitly emptied...
    environment = dict(os.environ) | {"CHROMATIC_PROJECT_TOKEN": ""}
    refused = subprocess.run(
        ["mise", "run", "e2e-chromatic"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # ...then it stops, and says which variable it wanted. The phrase and not the bare name:
    # mise echoes the script it is about to run, so the name is in that output either way.
    assert refused.returncode != 0
    assert REFUSAL in refused.stderr


def test_the_chromatic_upload_hands_a_token_it_was_given_to_the_uploader(tmp_path: Path) -> None:
    """Given a token, the script gets past the guard and runs the CLI with the flags we chose.

    Nothing reaches Chromatic: the `npx` first on this run's PATH is a stub that writes down the
    command it was handed and exits. That stub is also what makes the refusal above about the
    token rather than about a script that stops whatever it is handed — and running the file
    directly is the only way to put a stub in front of it, since mise puts the node it pins at
    the head of a task's PATH.
    """
    # If `npx` is a stub that records its arguments...
    recorded = tmp_path / "npx-arguments"
    stub = tmp_path / "npx"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" > "{recorded}"\n')
    stub.chmod(0o755)
    environment = dict(os.environ) | {
        "CHROMATIC_PROJECT_TOKEN": "not-a-real-token",
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
    }

    # ...then the uploader runs to the end...
    upload = subprocess.run(
        [uploader()], env=environment, capture_output=True, text=True, timeout=60, check=False
    )
    assert upload.returncode == 0, upload.stderr
    assert REFUSAL not in upload.stderr
    # ...and what it asked for is the Chromatic CLI, reading the sweep's archives and reporting a
    # changed page rather than failing on it. The token is not a word on that line: the CLI reads
    # it from the environment, so it stays out of any log that echoes the command.
    assert recorded.read_text().strip() == UPLOAD
