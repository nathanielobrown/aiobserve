#!/usr/bin/env python3
"""Run one gate command and say only whether it passed.

Every task in `check` and `check-fast` routes through here (`mise.toml` says which, and why).
mise drops its own per-task headers and footers through `task_output = "quiet"`; this wrapper
owns the rest. It runs the command with both streams captured, and

  - on success prints one line, `✅ <name>  <elapsed>`, swallowing whatever the tool says when
    nothing is wrong — "245 files already formatted", pytest's dots, pyrefly's INFO lines;
  - on failure replays everything it captured, verbatim, then prints `❌ <name>  <elapsed>` and
    exits with the command's own code.

So the noise a run costs is the noise of the gates that failed, and a failure is easy to find
rather than buried. One place holds that contract, instead of a per-tool quiet flag in each
task — half these tools have none, and the ones that do would go quiet on failure too.

`GATE_VERBOSE=1` replays a *passing* command's output as well, before the mark. That is the
audit: the wrapper only ever swallows an exit-0 command, so a warning reaches the green path
only if the tool warns while still passing, and this is how you go looking for one.

The name comes from `$MISE_TASK_NAME`, so a task needs no argument for it:
`run = "tools/gate.py uv run ruff format --check ."`. Outside mise it falls back to the
command's own basename.

Stdlib only, and run by its shebang rather than through `uv run`: this reports on a tree whose
virtualenv may be the broken thing, so it must not need that virtualenv to say so.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# The name is padded to this column so the elapsed times line up down a run. A wrapper sees
# only its own invocation, so it cannot compute the true maximum — the widest gated name today
# is `lint-docs-check` at 15, and `test_the_name_column_covers_every_gated_task` fails when a
# longer one arrives, making the bump deliberate rather than a run that quietly goes ragged.
NAME_COLUMN = 18


def env_flag(name: str) -> bool:
    """Whether environment variable `name` holds a truthy word (`1`, `true`, `yes`, `on`)."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    command = sys.argv[1:]
    if not command:
        # A wrapped task with nothing to run is a wiring bug in `mise.toml`, and reporting
        # success on it would hide a gate that stopped checking anything.
        sys.stderr.write("gate: no command given\n")
        return 2

    name = os.environ.get("MISE_TASK_NAME") or Path(command[0]).name

    start = time.monotonic()
    # stderr merged into stdout so a failure replays in the order the tool wrote it, and both
    # captured rather than streamed so a pass has nothing to say. `check=False`: the exit code
    # is what this reports, so it must not raise on one.
    finished = subprocess.run(  # noqa: S603 — the command is a `mise.toml` task line, not input
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    elapsed = f"{time.monotonic() - start:.2f}s"

    output = finished.stdout or ""
    if output and not output.endswith("\n"):
        output += "\n"
    if finished.returncode == 0:
        report = f"{output if env_flag('GATE_VERBOSE') else ''}✅ {name:{NAME_COLUMN}}  {elapsed}\n"
    else:
        # The mark stays unpadded: it closes a block of replayed output, with no column above
        # it to line up to.
        report = f"{output}❌ {name}  {elapsed}\n"
    # One write, so two gates failing in parallel cannot shred each other's blocks.
    sys.stdout.write(report)
    return finished.returncode


if __name__ == "__main__":
    sys.exit(main())
