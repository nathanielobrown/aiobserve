"""What the other tier reads instead of importing the enrichment package: versions and vocabulary.

`rust/metadata/enrichment.json` is the generation bridge for the facts a second implementation
needs about enrichment rows it does not write: the version stamps that decide staleness, the
table each level lives in, and the two closed vocabularies a row is written in
(`plans/rust-prototype/full-port.md`). A drifted copy is a cache that never rebuilds and a
viewer calling stale rows current, so the file is regenerated here and compared byte for byte.
"""

import json
from pathlib import Path

from hyphae.enrich.prompts import PROMPT_VERSION, Level
from hyphae.enrich.store import LEVELS
from hyphae.enrich.taxonomy import TAXONOMY_VERSION, Category, Outcome
from tools import gen_enrichment


def test_the_checked_in_versions_file_is_what_the_generator_writes(tmp_path: Path) -> None:
    """The tracked JSON is byte for byte what the enrichment package generates today."""
    # If the generator writes the file fresh into a scratch directory...
    fresh = tmp_path / "enrichment.json"
    gen_enrichment.write(fresh)
    # ...then the tracked copy the other tier reads is the same bytes, or it has drifted.
    assert fresh.read_bytes() == gen_enrichment.ENRICHMENT.read_bytes(), (
        f"`{gen_enrichment.ENRICHMENT.name}` has drifted from `hyphae/enrich/` —"
        f" regenerate it with `{gen_enrichment.COMMAND}`"
    )


def test_every_level_reaches_the_file_with_its_prompt_version_and_its_tables() -> None:
    """One entry per level, carrying what decides re-enrichment and where the rows live.

    A level with no prompt version would leave the other side unable to tell a current row
    from one written under older instructions — which is the whole of what a stamp is for.
    """
    # If the file is read back as data...
    written = json.loads(gen_enrichment.ENRICHMENT.read_text())
    # ...then it names the three levels, in the order the enum declares them...
    assert list(written["levels"]) == [level.value for level in Level]
    # ...each with the version its prompt is at and the tables `enrich/store.py` gives it.
    assert written["levels"] == {
        level.value: {
            "prompt_version": PROMPT_VERSION[level],
            "table": LEVELS[level].table,
            "keys": list(LEVELS[level].keys),
            "base": LEVELS[level].base,
            "base_keys": list(LEVELS[level].base_keys),
        }
        for level in Level
    }


def test_both_closed_vocabularies_reach_the_file_whole_beside_their_version() -> None:
    """Every member of both taxonomies, and the version they were written under.

    Closed is the point — `GROUP BY category` only means something over a fixed set — so a
    bridge carrying a subset would let the other side reject a row this one wrote.
    """
    # If the file is read back as data...
    written = json.loads(gen_enrichment.ENRICHMENT.read_text())
    # ...then both vocabularies are in it whole, in declaration order...
    assert written["categories"] == [category.value for category in Category]
    assert written["outcomes"] == [outcome.value for outcome in Outcome]
    # ...and the version that says which vocabulary a stored row was written against.
    assert written["taxonomy_version"] == TAXONOMY_VERSION
