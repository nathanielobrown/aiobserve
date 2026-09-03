"""Scaffolding for the tooling tier: read a generated markdown table, or `mise.toml`, as data.

Every leaf over a generator asserts a property of generated text against the live code it was
generated from, so the one thing that tier shares is the parser that turns a table back into
cells. No test compares a generator's output to a golden string: a golden would pin today's
wording and say nothing about whether the numbers in it are still the code's. Beside it, the
one reader of `mise.toml`, which two files ask different questions of.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cells(table: str) -> list[tuple[str, ...]]:
    """Every body row of a generated markdown table, cell by cell, header and rule dropped."""
    rows = []
    for line in table.splitlines():
        if not line.startswith("|"):
            continue
        row = tuple(cell.strip() for cell in line.strip("|").split("|"))
        # The `| --- | --- |` rule under the header, which carries no data.
        if all(set(cell) <= {"-", ":"} for cell in row):
            continue
        rows.append(row)
    # The header row is the first one left; a table without one is a generator bug.
    assert len(rows) > 1, f"no rows in:\n{table}"
    return rows[1:]


def numbers(text: str) -> list[int]:
    """Every integer written in `text`, thousands separators undone."""
    return [int(match.replace(",", "")) for match in re.findall(r"\d[\d,]*", text)]


def mise_config() -> dict:
    """`mise.toml` as data — the tasks, the pinned tools and the settings."""
    return tomllib.loads((ROOT / "mise.toml").read_text())


def tasks() -> dict[str, dict]:
    """Every task `mise.toml` declares, as data."""
    return mise_config()["tasks"]


def commands(task: dict) -> list[str]:
    """What a task runs, as a list — mise takes one command or several, and both read alike."""
    run = task.get("run", "")
    return run if isinstance(run, list) else [run]
