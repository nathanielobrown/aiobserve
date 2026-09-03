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

`GATE_VERBOSE=1` — that spelling and no other — replays a *passing* command's output as well,
before the mark. That is the audit: the wrapper only ever swallows an exit-0 command, so a
warning reaches the green path only if the tool warns while still passing, and this is how you
go looking for one.

Ctrl-C hands back what the tool had printed, marks the gate `interrupted` and exits 130. The
partial output is why a reader interrupts a silent gate, so losing it would be the wrong answer.

The name comes from `$MISE_TASK_NAME`, so a task needs no argument for it:
`run = "tools/gate.py uv run ruff format --check ."`. Outside mise it falls back to the
command's own basename.

Stdlib only, and run by its shebang: under mise `python3` is the project's own virtualenv
interpreter, so the shebang buys no independence from it — what it buys is one exec instead of
`uv run`'s lockfile resolve per gate, and importing nothing means a broken dependency tree is
something this can still report on rather than die of.
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


# What a shell reports for a process that died on SIGINT. The gate returns it in its own right,
# because it outlives the interrupted child in order to replay what that child had printed.
INTERRUPTED = 130


def run(command: list[str]) -> tuple[str, int]:
    """Run `command` with both streams captured, returning what it printed and its exit code.

    Read line by line rather than in one `communicate()`, so that an interrupt keeps what the
    tool had already said — that partial output is the whole reason a reader hit Ctrl-C.
    `errors="replace"`: a tool that echoes a byte no codec accepts costs that byte, not the
    replay, and a red gate is exactly when the output matters.
    """
    lines: list[str] = []
    # `check=False` in effect: the exit code is what this reports, so nothing here may raise on
    # one. The command is a task line out of `mise.toml`, not anything a caller supplies.
    with subprocess.Popen(  # noqa: S603 — the command is a `mise.toml` task line, not input
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    ) as child:
        if child.stdout is None:
            raise RuntimeError("gate: the child was opened without the pipe this reads")
        try:
            lines.extend(child.stdout)
            return "".join(lines), child.wait()
        except KeyboardInterrupt:
            # Ctrl-C in a terminal already reached the child through the foreground process
            # group; kill it anyway for the caller who signalled the gate alone, then take
            # whatever is left in the pipe.
            child.kill()
            lines.extend(child.stdout)
            return "".join(lines), INTERRUPTED


def main() -> int:
    command = sys.argv[1:]
    if not command:
        # A wrapped task with nothing to run is a wiring bug in `mise.toml`, and reporting
        # success on it would hide a gate that stopped checking anything.
        sys.stderr.write("gate: no command given\n")
        return 2

    name = os.environ.get("MISE_TASK_NAME") or Path(command[0]).name

    start = time.monotonic()
    output, code = run(command)
    elapsed = f"{time.monotonic() - start:.2f}s"

    # A tool whose last line has no newline — a progress counter, a prompt — gets one, so the
    # mark below starts a line of its own instead of being glued to what it was saying.
    if output and not output.endswith("\n"):
        output += "\n"
    if code == 0:
        # The one spelling the docs give, so a green run cannot turn loud on a stray value.
        verbose = os.environ.get("GATE_VERBOSE") == "1"
        report = f"{output if verbose else ''}✅ {name:{NAME_COLUMN}}  {elapsed}\n"
    else:
        # The mark stays unpadded: it closes a block of replayed output, with no column above
        # it to line up to. An interrupt says so rather than timing a run nobody let finish.
        closing = "interrupted" if code == INTERRUPTED else elapsed
        report = f"{output}❌ {name}  {closing}\n"
    # One write, so two gates failing in parallel cannot shred each other's blocks.
    sys.stdout.write(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
