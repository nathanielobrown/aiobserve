"""What `tools/gate.py` promises the reader of a green run, and the reader of a red one.

Every gate in `check` and `check-fast` runs through the wrapper, so two of its guarantees hold
the whole quality suite up: a passing command collapses to one line, and a failing one replays
everything it captured *and* exits with the command's own code. A wrapper that flattened that
code would turn every red gate green and nothing else in the repo would notice.

The wrapper is driven here the way mise drives it — a subprocess with `MISE_TASK_NAME` in its
environment — rather than by calling `main()`, because the exit code and the single stream
write are the contract, and neither is observable in-process. The leaves at the foot read
`mise.toml` instead: they are what keeps a new gate from leaking raw tool chatter back in.
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.tools.conftest import commands, mise_config, tasks
from tools import gate

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "gate.py"

# What a success line looks like once the name has been padded out to its column: the mark, the
# task's name, a run of padding, and the elapsed time. The padding is matched loosely because
# its width is a tunable constant, and a test that pinned it would fail on every bump.
PASSED = r"✅ {name} +\d+\.\d\ds\n"


def pinned(**named: str) -> dict[str, str]:
    """This run's environment with `GATE_VERBOSE` emptied, and whatever else a leaf names.

    Every leaf below that spawns a gate — straight through the wrapper or through mise — builds
    its environment here. The audit run is `GATE_VERBOSE=1 mise run check`, and that runs this
    suite: inherit the flag and every leaf asserting a green gate stays quiet goes red under the
    one command the escape hatch exists for.
    """
    return {**os.environ, "GATE_VERBOSE": ""} | named


def run_gate(
    *command: str, name: str = "demo", verbose: bool = False
) -> subprocess.CompletedProcess[str]:
    """The wrapper over `command`, labelled `name` the way mise labels it."""
    return subprocess.run(
        [sys.executable, str(GATE), *command],
        capture_output=True,
        text=True,
        env=pinned(MISE_TASK_NAME=name, **({"GATE_VERBOSE": "1"} if verbose else {})),
        timeout=60,
        check=False,
    )


def python(source: str) -> tuple[str, str, str]:
    """A `python -c <source>` command, as a tuple to splat into `run_gate`."""
    return (sys.executable, "-c", source)


def test_a_passing_gate_says_only_that_it_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A green command's own chatter is swallowed — one line is left, and it names the task."""
    # If the suite is itself running under the audit flag, which is where this claim is easiest
    # to lose — the gate under test must read the environment its caller pinned, not ours...
    monkeypatch.setenv("GATE_VERBOSE", "1")
    # ...and the wrapped command prints what a tool prints and then exits 0...
    result = run_gate(*python("print('245 files already formatted')"), name="format-check")
    # ...then the wrapper passes too...
    assert result.returncode == 0
    # ...and the whole of what it wrote is the one line, with none of that chatter in it.
    assert re.fullmatch(PASSED.format(name="format-check"), result.stdout)


def test_the_marks_line_up_across_names_of_different_length() -> None:
    """Names pad to a shared column, so the elapsed times read down a green run as a table."""
    # If two gates whose names are nothing like the same length both pass...
    short = run_gate(*python("pass"), name="test")
    long = run_gate(*python("pass"), name="lint-docs-check")
    # ...then their elapsed times start at the same offset, which is the column that makes the
    # run scannable rather than a ragged list.
    elapsed = [re.search(r"\d+\.\d\ds", output.stdout) for output in (short, long)]
    assert all(match is not None for match in elapsed)
    assert len({match.start() for match in elapsed if match is not None}) == 1


def test_a_failing_gate_replays_everything_it_captured() -> None:
    """A red command's two streams both come back, in the order it emitted them."""
    # If the wrapped command writes to stdout, then to stderr, and then fails — flushing as it
    # goes, because into a pipe Python holds stdout back and lets stderr through, and the order
    # a tool's own buffering picks is not the wrapper's to fix...
    result = run_gate(
        *python(
            "import sys; print('what it checked', flush=True);"
            " print('why it failed', file=sys.stderr, flush=True); sys.exit(1)"
        ),
        name="typecheck",
    )
    # ...then both are replayed, merged — a diagnostic on stderr is the half a reader needs...
    assert "what it checked\nwhy it failed\n" in result.stdout
    # ...and the last line marks the failure and names which gate owns the block above it,
    # which is what keeps two failing gates apart when they run in parallel.
    assert re.search(r"❌ typecheck  \d+\.\d\ds\n\Z", result.stdout)

    # ...and a tool whose last line has no newline of its own — a progress counter, a prompt —
    # gets one, rather than having the mark glued onto the end of what it was saying.
    unterminated = run_gate(
        *python("import sys; sys.stdout.write('cut off mid-'); sys.exit(1)"), name="test"
    )
    assert re.search(r"^cut off mid-\n❌ test  \d+\.\d\ds\n\Z", unterminated.stdout)


def test_a_gate_replays_output_that_no_codec_can_read() -> None:
    """A byte that isn't UTF-8 costs its own character, not the whole failure replay."""
    # If a failing tool writes a byte no decoder accepts — this repo reads transcripts holding
    # whatever an agent read, so a tool that echoes one back is not far-fetched...
    result = run_gate(
        *python(
            "import sys; sys.stdout.buffer.write(b'a \\xff byte\\n'); sys.stdout.flush();"
            " sys.exit(1)"
        ),
        name="test",
    )
    # ...then the gate still reports the failure the tool's way, with the rest of the line
    # intact. Decoding strictly would raise here and lose the output at the one moment it
    # matters, and the traceback would name the gate rather than the tool that failed.
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert re.search(r"^a . byte\n❌ test  \d+\.\d\ds\n\Z", result.stdout)


def test_an_interrupted_gate_keeps_what_the_tool_had_printed(tmp_path: Path) -> None:
    """Ctrl-C during a gate leaves the tool's output on screen and says the run was interrupted.

    `test` is silent for as long as the suite runs, so it is the gate a reader interrupts — and
    what they wanted was the pytest output the run had reached. Captured output is the gate's to
    hand back; a traceback out of the wrapper is not what the reader asked about.
    """
    # If a wrapped command prints, says so, and then hangs...
    printed = tmp_path / "printed"
    child = subprocess.Popen(
        [
            sys.executable,
            str(GATE),
            *python(
                "import pathlib, sys, time; print('the failures so far', flush=True);"
                f" pathlib.Path({str(printed)!r}).touch(); time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=pinned(MISE_TASK_NAME="test"),
        # Its own process group, so the signal below reaches the gate and the child together —
        # which is what a terminal does to the foreground group on Ctrl-C.
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while not printed.exists():
        assert time.monotonic() < deadline, "the wrapped command never got as far as printing"
        time.sleep(0.02)

    # ...and the reader interrupts it...
    os.killpg(os.getpgid(child.pid), signal.SIGINT)
    stdout, stderr = child.communicate(timeout=30)

    # ...then what the tool had said is still there, the mark says why the run stopped, and the
    # exit code is the one a shell reports for an interrupted process.
    assert child.returncode == 130
    assert stdout == "the failures so far\n❌ test  interrupted\n"
    assert "Traceback" not in stderr


def test_a_gates_exit_code_is_the_commands_own() -> None:
    """The wrapped command's exit code passes through, never flattened to 0 or to 1."""
    # If a gate fails with a code no wrapper would pick by accident...
    result = run_gate(*python("raise SystemExit(3)"))
    # ...then that is the code mise sees. A wrapper that lost it would report every red gate
    # as green, and `check` would pass on a broken tree.
    assert result.returncode == 3


def test_verbose_shows_what_a_passing_gate_printed() -> None:
    """`GATE_VERBOSE=1` replays a passing command's output, so a warning cannot ride the mark."""
    # If a command warns and still exits 0, the default path swallows the warning...
    quiet = run_gate(*python("print('a deprecation that still passes')"), name="lint-check")
    assert "deprecation" not in quiet.stdout
    # ...but under the flag it is replayed ahead of the mark, which is how the audit run finds
    # a tool that warns instead of failing.
    loud = run_gate(
        *python("print('a deprecation that still passes')"), name="lint-check", verbose=True
    )
    assert loud.returncode == 0
    assert loud.stdout.startswith("a deprecation that still passes\n")
    assert re.search(PASSED.format(name="lint-check") + r"\Z", loud.stdout)


def test_a_gate_wrapping_nothing_refuses_to_run() -> None:
    """Wrapping no command at all is a `mise.toml` wiring bug, and the wrapper says so."""
    # If the wrapper is handed nothing to run...
    result = run_gate()
    # ...then it fails and names what was missing, rather than reporting a check it never ran.
    assert result.returncode == 2
    assert "no command given" in result.stderr
    assert not result.stdout


def gated() -> dict[str, dict]:
    """Every task whose `run` goes through the wrapper — the names that reach it as a label."""
    return {
        name: task
        for name, task in tasks().items()
        if any("gate.py" in command for command in commands(task))
    }


def test_every_gate_in_check_routes_through_the_wrapper() -> None:
    """No member of `check` or `check-fast` prints a tool's own output straight at the reader."""
    # If we take the two aggregates a reader runs, and everything they depend on...
    declared = tasks()
    members = {member for name in ("check", "check-fast") for member in declared[name]["depends"]}
    # ...there are members to check, so a parse that went wrong cannot pass as a clean sweep...
    assert members, "`check` and `check-fast` depend on nothing — the `mise.toml` parse is stale"
    # ...and each one runs through the wrapper, which is what holds a green run to a line apiece.
    for member in sorted(members):
        assert member in gated(), (
            f"`{member}` is a gate but does not run through `tools/gate.py`, so a green "
            f"`check` prints whatever it has to say"
        )


def test_a_fixing_task_is_gated_wherever_its_verdict_twin_is() -> None:
    """A task that fixes what its `-check` twin reports on is gated alongside it.

    Four tools here answer the same question twice, once reporting and once rewriting. The fix
    lands in the tree, where `git status` is what shows it, so the tool naming it again is the
    noise the wrapper exists to drop — and a pair split across the two styles would make a
    reader's `check-fast` read half as a table and half as tool output.
    """
    # If we pair each gated verdict task with the fixing task of the same name...
    declared = tasks()
    twins = {
        name.removesuffix("-check"): name
        for name in gated()
        if name.endswith("-check") and name.removesuffix("-check") in declared
    }
    # ...there are pairs to check...
    assert twins, "no gated task has a fixing twin — the `mise.toml` parse is stale"
    # ...and neither half of any pair is loud.
    for fixer, verdict in sorted(twins.items()):
        assert fixer in gated(), (
            f"`{verdict}` runs through `tools/gate.py` and `{fixer}` does not, so the pair a "
            f"reader runs together reports two different ways"
        )


def test_the_name_column_covers_every_gated_task() -> None:
    """The padding column fits the widest gated name, so no green run goes ragged."""
    # If we take every name that reaches the wrapper as a label...
    names = gated()
    assert names, "no task routes through `tools/gate.py` — the `mise.toml` parse is stale"
    # ...then the widest fits the column. The wrapper sees one invocation and cannot work the
    # maximum out for itself, so this is what makes a longer name a deliberate bump.
    widest = max(names, key=len)
    assert len(widest) <= gate.NAME_COLUMN, (
        f"`{widest}` is {len(widest)} characters and NAME_COLUMN is {gate.NAME_COLUMN}; "
        f"raise the constant in tools/gate.py"
    )


def test_lint_shell_cannot_pass_without_shellcheck() -> None:
    """shellcheck is pinned, so `lint-shell` can never report green on a check it skipped."""
    # If the tool is pinned in `mise.toml`, every machine that runs the task has it...
    assert any("shellcheck" in tool for tool in mise_config()["tools"])
    # ...so the task runs it flat, with nothing that would step around a machine without one.
    # Under the wrapper such a branch would be swallowed and the gate would say ✅ having read
    # no script at all — the silent pass the pin exists to make impossible.
    run = "\n".join(commands(tasks()["lint-shell"]))
    assert "shellcheck" in run
    assert "command -v" not in run


def test_a_gated_task_run_through_mise_prints_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the way a reader runs it, a passing gate's whole output is its own success line.

    The three pieces are only worth anything together: `task_output` drops mise's chrome,
    `MISE_TASK_NAME` gives the wrapper the label, and the wrapper swallows the rest. The
    cheapest gate stands for all of them, because the leaf above proves none of the others
    skips the wrapper.
    """
    # If the suite is running under the audit flag — the run that made this leaf red once, by
    # reaching mise through an environment nobody had pinned...
    monkeypatch.setenv("GATE_VERBOSE", "1")
    # ...and the cheapest gate is run through mise on a formatted tree...
    result = subprocess.run(
        ["mise", "run", "format-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=pinned(),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # ...then what lands in the terminal is one line, and it is the wrapper's.
    printed = (result.stdout + result.stderr).splitlines()
    assert printed == [line for line in printed if line.startswith("✅")]
    assert len(printed) == 1, printed
