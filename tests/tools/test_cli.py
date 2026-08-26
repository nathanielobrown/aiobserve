"""What a cog block actually runs: `uv run python -m tools.gen_<name>`, out of the repository root.

Every other leaf in this tier calls `generate()` in-process, which says nothing about the seam
the document depends on — a module that cannot be run, or a `main()` that prints something
other than what it generated, would splice a broken block with the whole suite green. So these
run the command for real and compare its stdout to the text the generator returns.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tools import gen_bounds, gen_layout, gen_routes, gen_schema

ROOT = Path(__file__).resolve().parents[2]

# One invocation per generator, each with the text it should have printed. The two generators
# that take an argument are asked for one table: the argument handling is what is under test
# here, not every table, which the generators' own tiers cover.
COMMANDS: list[tuple[str, tuple[str, ...], Callable[[], str]]] = [
    ("gen_routes", (), gen_routes.generate),
    ("gen_bounds", ("knobs",), lambda: gen_bounds.generate(gen_bounds.Table.KNOBS)),
    ("gen_layout", (), gen_layout.generate),
    ("gen_schema", ("identity",), lambda: gen_schema.generate(gen_schema.Section.IDENTITY)),
]


def run(module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """One generator, run the way a cog block runs it."""
    return subprocess.run(
        ["uv", "run", "python", "-m", f"tools.{module}", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("module", "arguments", "generated"), COMMANDS, ids=[name for name, _, _ in COMMANDS]
)
def test_the_command_a_cog_block_runs_prints_what_the_generator_returns(
    module: str, arguments: tuple[str, ...], generated: Callable[[], str]
) -> None:
    # The whole contract of the CLI seam: exit 0, and the generated body on stdout with the one
    # newline `print` adds. A block spliced from anything else would drift from these tests.
    done = run(module, *arguments)
    assert done.returncode == 0, done.stderr
    assert done.stdout == generated() + "\n"


def test_a_generator_asked_for_no_table_says_which_ones_it_has() -> None:
    # The other half of the seam. A cog block whose argument is missing or misspelled has to
    # fail loudly, because the alternative is a document splicing an empty block.
    done = run("gen_bounds")
    assert done.returncode != 0
    assert "knobs" in done.stderr and "bounds" in done.stderr
