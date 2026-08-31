"""`rust/metadata/enrichment.json`: the stamps and vocabulary of enrichment, as plain data.

Run by hand — `uv run python -m tools.gen_enrichment` — and read back by
`tests/tools/test_gen_enrichment.py`, which regenerates into a scratch directory and compares
bytes. Not a cog block like most of `tools/`: what it writes is a file another runtime reads,
not a table spliced into a document.

`hyphae/enrich/` stays the one owner. What crosses is what a reader of the rows needs and
cannot derive: the stamps that decide whether a row is current, the table each level lives in,
and the two closed vocabularies a row is written in (`plans/rust-prototype/full-port.md`).
What each member *means* does not cross — the definitions are prompt material, written for the
classifier, and a second copy of that prose would drift with no reader to catch it.
"""

import json
from pathlib import Path

from hyphae.enrich.prompts import PROMPT_VERSION, Level
from hyphae.enrich.store import LEVELS
from hyphae.enrich.taxonomy import TAXONOMY_VERSION, Category, Outcome

ROOT = Path(__file__).resolve().parent.parent
ENRICHMENT = ROOT / "rust" / "metadata" / "enrichment.json"
# What a failing staleness check tells the reader to run.
COMMAND = "uv run python -m tools.gen_enrichment"


def level(named: Level) -> dict[str, object]:
    """One level: what its rows are stamped with, and where they and their subjects live.

    A level missing from either table crashes rather than emitting a partial entry — both are
    closed sets over the same enum, and a level with no prompt version has no staleness.
    """
    if named not in PROMPT_VERSION:
        raise ValueError(f"level `{named.value}` has no prompt version to stamp its rows with")
    if named not in LEVELS:
        raise ValueError(f"level `{named.value}` has no table for its rows to live in")
    spec = LEVELS[named]
    return {
        "prompt_version": PROMPT_VERSION[named],
        "table": spec.table,
        "keys": list(spec.keys),
        "base": spec.base,
        "base_keys": list(spec.base_keys),
    }


def generate() -> str:
    """The whole file: the levels in enum order, then the vocabularies and their version."""
    written = {
        "levels": {named.value: level(named) for named in Level},
        "taxonomy_version": TAXONOMY_VERSION,
        "categories": [category.value for category in Category],
        "outcomes": [outcome.value for outcome in Outcome],
    }
    return json.dumps(written, indent=2) + "\n"


def write(path: Path) -> None:
    """Write the file, creating its directory if the tree does not hold one yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate())


def main() -> None:
    write(ENRICHMENT)
    print(f"wrote {ENRICHMENT.relative_to(ROOT)}: {len(Level)} levels, taxonomy {TAXONOMY_VERSION}")


if __name__ == "__main__":
    main()
