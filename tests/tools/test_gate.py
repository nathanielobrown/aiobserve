"""What `tools/gate.py` promises the reader of a green run, and the reader of a red one.

Every gate in `check` and `check-fast` runs through the wrapper, so two of its guarantees hold
the whole quality suite up: a passing command collapses to one line, and a failing one replays
everything it captured *and* exits with the command's own code. A wrapper that flattened that
code would turn every red gate green and nothing else in the repo would notice.

The wrapper is driven here the way mise drives it — a subprocess with `MISE_TASK_NAME` in its
environment — rather than by calling `main()`, because the exit code and the single stream
write are the contract, and neither is observable in-process.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "gate.py"

# What a success line looks like once the name has been padded out to its column: the mark, the
# task's name, a run of padding, and the elapsed time. The padding is matched loosely because
# its width is a tunable constant, and a test that pinned it would fail on every bump.
PASSED = r"✅ {name} +\d+\.\d\ds\n"


def run_gate(
    *command: str, name: str = "demo", verbose: bool = False
) -> subprocess.CompletedProcess[str]:
    """The wrapper over `command`, labelled `name` the way mise labels it.

    `GATE_VERBOSE` is pinned rather than inherited: the audit run is `GATE_VERBOSE=1 mise run
    check`, which runs this suite, and an inherited flag would flip the default-path leaves.
    """
    environment = {**os.environ, "MISE_TASK_NAME": name, "GATE_VERBOSE": "1" if verbose else ""}
    return subprocess.run(
        [sys.executable, str(GATE), *command],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )


def python(source: str) -> tuple[str, str, str]:
    """A `python -c <source>` command, as a tuple to splat into `run_gate`."""
    return (sys.executable, "-c", source)


def test_a_passing_gate_says_only_that_it_passed() -> None:
    """A green command's own chatter is swallowed — one line is left, and it names the task."""
    # If the wrapped command prints what a tool prints and then exits 0...
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
