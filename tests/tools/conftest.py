"""Scaffolding for the generator tier: read a generated markdown table back as data.

Every leaf here asserts a property of generated text against the live code it was generated
from, so the one thing the tier shares is the parser that turns a table back into cells. No
test compares a generator's output to a golden string: a golden would pin today's wording and
say nothing about whether the numbers in it are still the code's.
"""

import re


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
