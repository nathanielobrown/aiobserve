"""The staleness rule: what an enrichment row was written under, and what makes it stale.

Four values decide it — the hash of the rendered content, the level's prompt version, the
taxonomy version, and the model that answered. They are minted, compared and judged here, so
a pass and a reader cannot hold two versions of the rule. The two versions are declared
elsewhere and read here alone: `LevelSpec.prompt_version` in `levels.py`, `TAXONOMY_VERSION`
in `taxonomy.py`.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from hyphae.enrich.items import Level
from hyphae.enrich.levels import LEVELS
from hyphae.enrich.taxonomy import TAXONOMY_VERSION


@dataclass(frozen=True)
class Stamp:
    """What a row was written under. A row is current when its stamp equals today's."""

    # sha256 of the rendered prompt content — not of the instructions, which
    # `prompt_version` covers.
    input_hash: str
    prompt_version: int
    taxonomy_version: int
    model: str


def input_hash(rendered: str) -> str:
    """The staleness hash: the rendered content and nothing else.

    Not the instructions and not the output schema — a level's `prompt_version` covers those,
    so an instruction edit does not have to pretend the content changed.
    """
    return hashlib.sha256(rendered.encode()).hexdigest()


@dataclass(frozen=True)
class Versions:
    """The half of the stamp the code decides.

    A pass adds the hash and the model; a reader with no pass in hand can still judge a row
    against this half. Passed rather than read, so a test bumps a version by handing over a
    different value instead of patching the declaration.
    """

    prompt: Mapping[Level, int]
    taxonomy: int

    @classmethod
    def current(cls) -> "Versions":
        """What the declarations say today — the whole of what `hp enrich` runs under."""
        return cls(
            prompt={level: spec.prompt_version for level, spec in LEVELS.items()},
            taxonomy=TAXONOMY_VERSION,
        )

    def stamp(self, level: Level, rendered: str, model: str) -> Stamp:
        """What a row for `level` would be stamped now, given its render and the answering
        model."""
        return Stamp(
            input_hash=input_hash(rendered),
            prompt_version=self.prompt[level],
            taxonomy_version=self.taxonomy,
            model=model,
        )

    def moved_past(self, level: Level, *, prompt_version: int, taxonomy_version: int) -> bool:
        """Whether this build has moved past a row's versions.

        Two of the four axes: the hash needs a render and the model needs a pass, so a reader
        holding only a stored row gets the verdict those two can support and no more.
        """
        return prompt_version != self.prompt[level] or taxonomy_version != self.taxonomy


def stale(planned: Mapping[str, Stamp], held: Mapping[str, Stamp]) -> list[str]:
    """The planned keys whose held stamp is not the planned one, in planned order.

    No row counts as not the one, which is how a first pass finds work. Held keys nothing
    planned are not reported: a leftover row is the sweep's business.
    """
    return [key for key, stamp in planned.items() if held.get(key) != stamp]
