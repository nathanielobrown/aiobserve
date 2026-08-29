"""What a task line in `mise.toml` has to keep saying when it names a path by hand.

A task that walks a bare `.` needs no gate: the tree is its argument. The browser tier's three
run inside a directory they name, which is an enumeration that can rot, and this is the leaf
that reads it back against the tree. Beside it, what `e2e-chromatic` does with the one
credential in the repo.
"""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# How a task spells the repo root in its `dir`. mise resolves a bare relative path against the
# directory the caller was in, so this prefix is what makes the path mean one place.
CONFIG_ROOT = "{{config_root}}/"

# The one credential in this repo, and what `e2e-chromatic` says when it has no value for it.
TOKEN = "CHROMATIC_PROJECT_TOKEN"
REFUSAL = f"{TOKEN} is missing or empty"

# What it runs when it has one: the Chromatic CLI over the archives `mise run e2e` wrote,
# reporting a changed page rather than failing the job while the baselines settle.
UPLOAD = "chromatic --playwright --exit-zero-on-changes"


def tasks() -> dict[str, dict]:
    """Every task `mise.toml` declares, as data."""
    return tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]


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


def planted(tmp_path: Path, dotenv: str) -> Path:
    """The uploader copied into a tree of its own, with the given `.env` at the root it reads.

    The script finds `.env` two directories above itself, so the copy is what redirects it: this
    repo's own root is never read, and the token planted below is a string in a temporary file.
    """
    script = tmp_path / "tests" / "e2e" / "chromatic-upload.sh"
    script.parent.mkdir(parents=True)
    shutil.copy(uploader(), script)
    (tmp_path / ".env").write_text(dotenv)
    return script


def stub_npx(directory: Path) -> tuple[Path, Path]:
    """An `npx` in `directory` that writes down what it was handed and the environment it got.

    Put that directory first on PATH and nothing reaches the network. The two files it writes
    are what the leaves below read; neither exists if the uploader never got that far.
    """
    arguments = directory / "npx-arguments"
    handed = directory / "npx-environment"
    stub = directory / "npx"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" > "{arguments}"\nenv > "{handed}"\n')
    stub.chmod(0o755)
    return arguments, handed


def running(tmp_path: Path, **named: str) -> dict[str, str]:
    """This run's environment, with `tmp_path` first on PATH and the token only if named here.

    The variable is dropped unless a leaf sets it, because whether it is set at all — not what
    it holds — is the door `.env` is read behind, and a developer's own shell may hold one.
    """
    inherited = {key: value for key, value in os.environ.items() if key != TOKEN}
    return inherited | {"PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"} | named


def test_the_chromatic_upload_refuses_to_run_on_an_empty_token() -> None:
    """`mise run e2e-chromatic` stops and names the variable when it holds no token.

    The upload is the one thing in this repo that carries a credential to a third party, so the
    failure a reader must never see is a run that starts, reaches the network, and then says it
    was not authorized. The whole task is run here, not the script alone, because the guard is
    only worth anything if it stands in the way of the command a reader actually types.
    """
    # If the task is run with the token explicitly emptied...
    environment = dict(os.environ) | {TOKEN: ""}
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
    recorded, _ = stub_npx(tmp_path)
    environment = running(tmp_path, **{TOKEN: "not-a-real-token"})

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


def test_an_emptied_token_in_the_environment_answers_over_one_in_the_env_file(
    tmp_path: Path,
) -> None:
    """A caller who empties the variable gets the refusal even when `.env` holds a token.

    The file is a fallback for an environment that says nothing, not an answer over one that
    says something — and on a machine with no `.env`, which is this checkout and every CI
    runner, the two read alike. So the script is copied into a tree that does have one, where a
    version that read the file first would upload the token it found rather than refuse.
    """
    # If `.env` beside the script holds a token, `npx` is a stub, and the environment names the
    # variable but empties it...
    script = planted(tmp_path, f"{TOKEN}=a-token-from-the-file\n")
    arguments, _ = stub_npx(tmp_path)

    # ...then the caller's word wins, and nothing is handed to an uploader.
    refused = subprocess.run(
        [script],
        env=running(tmp_path, **{TOKEN: ""}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert refused.returncode != 0
    assert REFUSAL in refused.stderr
    assert not arguments.exists(), f"the uploader ran: {arguments.read_text()}"


def test_the_uploader_takes_its_token_out_of_the_env_file_and_leaves_the_rest_at_home(
    tmp_path: Path,
) -> None:
    """With the variable unset the token comes from `.env` — and travels there on its own.

    `.env` is the Python CLI's file: the OTLP ingest keys sit in it beside the token. This is
    the one script that hands its environment to a third party's CLI, so a key that crosses into
    it goes somewhere it was never meant to go, and no failure ever says so.
    """
    # If `.env` holds the token and an ingest key beside it, and the environment names neither...
    script = planted(tmp_path, f"{TOKEN}=a-token-from-the-file\nHONEYCOMB_API_KEY=stays-at-home\n")
    arguments, handed = stub_npx(tmp_path)

    # ...then the file answers, the uploader runs...
    upload = subprocess.run(
        [script], env=running(tmp_path), capture_output=True, text=True, timeout=60, check=False
    )
    assert upload.returncode == 0, upload.stderr
    assert arguments.read_text().strip() == UPLOAD
    # ...and the environment it was given holds the token it needs and nothing else off that file.
    assert f"{TOKEN}=a-token-from-the-file" in handed.read_text()
    assert "stays-at-home" not in handed.read_text()
