"""The staleness rule: what a row is written under, and what makes it stale.

Plain maps and strings in, verdicts out — no store and no client, because the rule is about
four values and nothing else. The stamps here are invented, as every stamp in the suite is:
a stamp is ours, so no recorded session holds one. The oracles read `levels.py` and
`taxonomy.py` directly; one that asked `Versions` what the versions are would test the code
against itself.
"""

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from hyphae.enrich.items import Level
from hyphae.enrich.levels import LEVELS
from hyphae.enrich.stamp import Stamp, Versions, input_hash, stale
from hyphae.enrich.taxonomy import TAXONOMY_VERSION

# What a row this file plants was written under. The versions are today's, so a leaf that
# wants drift asks for it by name.
PLANNED = Stamp(
    input_hash="hash-1",
    prompt_version=LEVELS[Level.turn].prompt_version,
    taxonomy_version=TAXONOMY_VERSION,
    model="claude-haiku-4-5-20251001",
)

# A build whose three levels are on three different prompt versions. Today's declarations
# happen to agree on one number, so a `Versions` that read one level's version for another
# would be invisible against them — every leaf about *which* version is read uses this.
SPREAD = Versions(prompt={Level.agent_run: 3, Level.turn: 4, Level.session: 5}, taxonomy=7)


@pytest.mark.parametrize(
    "mutation",
    [
        # Each of the four fields of the staleness key, moved one at a time on the held row:
        # the re-render that produced a different prompt...
        {"input_hash": "a-different-hash"},
        # ...an instruction or output-schema change...
        {"prompt_version": 99},
        # ...a taxonomy revision...
        {"taxonomy_version": 99},
        # ...and a `--model` switch.
        {"model": "claude-sonnet-4-5"},
    ],
)
def test_stale_names_the_key_whose_held_stamp_moved(mutation: dict[str, Any]) -> None:
    """A key is stale when any one of the four fields differs from what would be written."""
    # If three keys are planned under one stamp, and the middle one is held under a stamp
    # differing in a single field...
    planned = {"a": PLANNED, "b": PLANNED, "c": PLANNED}
    held = {"a": PLANNED, "b": replace(PLANNED, **mutation), "c": PLANNED}
    # ...then that key, and only that key, comes back stale.
    assert stale(planned, held) == ["b"]


def test_a_planned_key_nothing_holds_is_stale() -> None:
    """An item nothing has enriched yet is stale, which is how a first pass finds work."""
    planned = {"a": PLANNED, "b": PLANNED}
    assert stale(planned, {}) == ["a", "b"]


def test_an_identical_stamp_is_fresh_and_a_held_key_nothing_planned_is_not_reported() -> None:
    """Only planned keys are reported, and only where the held stamp is not the planned one.

    The second half is what keeps a pass from treating the store's own leftovers as work:
    a row for an item this project no longer plans is the sweep's business, not staleness's.
    """
    planned = {"a": PLANNED, "b": PLANNED}
    held = dict(planned) | {"leftover": replace(PLANNED, model="claude-sonnet-4-5")}
    assert stale(planned, held) == []


def test_current_reads_the_two_declarations() -> None:
    """`Versions.current()` is exactly what `levels.py` and `taxonomy.py` declare today.

    The one equality behind the whole module: the viewer and the enricher can no longer
    disagree about a row's versions because both ask this, and this is the declarations.
    """
    current = Versions.current()
    assert current.prompt == {level: LEVELS[level].prompt_version for level in Level}
    assert current.taxonomy == TAXONOMY_VERSION


def test_current_reads_each_levels_own_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every level's prompt version is that level's own declaration, not one level's for all.

    Today's three levels declare the same number, so the leaf above builds its oracle from
    coincident values and cannot tell a per-level read from a constant. Patching the
    declaration is the only falsifier — and it is a different animal from the monkeypatches
    this module exists to delete: those faked a bump to drive behaviour through the seam,
    this moves the one input of a function whose whole job is to read `levels.py`.
    """
    # If the turn level alone moves to a version no other level is on — the state
    # `docs/enrichment.md` tells a maintainer to create...
    monkeypatch.setitem(LEVELS, Level.turn, replace(LEVELS[Level.turn], prompt_version=99))
    current = Versions.current()
    # ...then that is the version `current()` reports for turns...
    assert current.prompt[Level.turn] == 99
    # ...and the other two are still their own.
    assert current.prompt == {level: LEVELS[level].prompt_version for level in Level}


@pytest.mark.parametrize(
    ("level", "prompt_version"),
    # Every level, against the version `SPREAD` puts it on and no other.
    [(Level.agent_run, 3), (Level.turn, 4), (Level.session, 5)],
)
def test_stamp_puts_the_four_together(level: Level, prompt_version: int) -> None:
    """A minted stamp carries the hash of what it was handed, its own level's prompt
    version, the taxonomy version, and the model on the call."""
    minted = SPREAD.stamp(level, "some rendered content", "claude-sonnet-4-5")
    assert minted == Stamp(
        input_hash=input_hash("some rendered content"),
        prompt_version=prompt_version,
        taxonomy_version=7,
        model="claude-sonnet-4-5",
    )


def test_input_hash_is_sha256_of_the_rendered_content() -> None:
    """The staleness hash is the plain sha256 of what it was handed.

    Pinned to the digest rather than to itself: a hash that changed would call every row in
    the canonical store stale and re-enrich the lot at full price.
    """
    assert input_hash("a rendered turn") == hashlib.sha256(b"a rendered turn").hexdigest()


@pytest.mark.parametrize(
    ("prompt_version", "taxonomy_version", "expected"),
    [
        # A row written under today's declarations is current...
        (LEVELS[Level.turn].prompt_version, TAXONOMY_VERSION, False),
        # ...one written under an older prompt version has been left behind...
        (LEVELS[Level.turn].prompt_version - 1, TAXONOMY_VERSION, True),
        # ...and so has one written under an older taxonomy.
        (LEVELS[Level.turn].prompt_version, TAXONOMY_VERSION - 1, True),
    ],
)
def test_moved_past_judges_a_rows_two_versions(
    prompt_version: int, taxonomy_version: int, expected: bool
) -> None:
    """Today's build has moved past a row when either of its versions is not today's."""
    assert (
        Versions.current().moved_past(
            Level.turn, prompt_version=prompt_version, taxonomy_version=taxonomy_version
        )
        is expected
    )


def test_moved_past_reads_the_named_levels_version() -> None:
    """A row is judged against its own level's prompt version, not another level's.

    Separate from the leaves above because today's three levels declare one number: only a
    build that spreads them can tell a lookup by level from a lookup by luck.
    """
    # If a row was written under prompt version 3 — the agent-run level's, on this build...
    written = 3
    # ...then it is current as an agent run...
    assert SPREAD.moved_past(Level.agent_run, prompt_version=written, taxonomy_version=7) is False
    # ...and behind as a turn, whose level is on 4.
    assert SPREAD.moved_past(Level.turn, prompt_version=written, taxonomy_version=7) is True


def test_moved_past_judges_neither_the_hash_nor_the_model() -> None:
    """Two rows differing only in the model that wrote them get the same verdict.

    The partial rule the viewer relies on: a reader holding a stored row cannot know today's
    hash without a render, or the model without a pass, so `moved_past` judges the two axes
    a reader can see and says so.
    """
    current = Versions.current()
    written = replace(PLANNED, model="claude-sonnet-4-5")
    verdicts = {
        current.moved_past(
            Level.turn,
            prompt_version=row.prompt_version,
            taxonomy_version=row.taxonomy_version,
        )
        for row in (PLANNED, written)
    }
    assert verdicts == {False}
