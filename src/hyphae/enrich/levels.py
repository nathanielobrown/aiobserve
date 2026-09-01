"""Everything one enrichment level is, declared once per level.

A level is a prompt, a set of rows, and a reader that finds them. `LEVELS` holds all three
together, so adding one is one entry here beside the renderer it names — rather than an edit
to a version map, a subject map, a budget constant, a render dispatch, a table map, a reader
map and a round order, none of which would complain about being forgotten.

The entries are written in round order: bottom-up, because every prompt embeds its children's
descriptions rather than their text.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hyphae.enrich import prompts
from hyphae.enrich.items import Budgets, Item, Level


@dataclass(frozen=True)
class LevelSpec:
    """One level, whole: what it sends the model, where its rows live, and what reads them."""

    # Covers what `input_hash` cannot see: the instructions and the output schema. Bump it and
    # this level re-enriches; its parents follow through the hash.
    prompt_version: int
    # What this level is looking at. The rest of the instructions is the same everywhere, so a
    # level reads differently only where it should.
    subject: str
    budgets: Budgets
    # This level's render, taking this level's item. The registry is keyed by level and an
    # item names its own level, so `render` cannot hand a renderer the wrong item.
    renderer: Callable[[Any, Budgets], str]
    # The `EnrichmentStore` method that reads this level's items. Named rather than bound: the
    # store imports this module, so a spec cannot hold one of its methods.
    reader: str
    table: str
    # The enrichment table's primary key columns, in order.
    keys: tuple[str, ...]
    # The view holding the rows enrichment describes, and the columns matching `keys`.
    base: str
    base_keys: tuple[str, ...]
    # Instruction paragraphs this level alone carries, past the shared guidance.
    riders: tuple[str, ...] = ()


# Closed set: a level here with no table in `enrich/store.py`'s DDL cannot be written, a table
# there with no level here would never be swept, and a level missing here gets no round at all.
LEVELS: dict[Level, LevelSpec] = {
    Level.agent_run: LevelSpec(
        prompt_version=4,
        subject=(
            "You are reading one run of a subagent: the task it was given, any later "
            "instructions, and what it did about them. Describe that run."
        ),
        # The same cap a main turn gets: a run holds the same kind of work, and 209 of 2,458
        # recorded runs reach it.
        budgets=Budgets(total=30_000),
        renderer=prompts.render_run,
        reader="run_items",
        table="agent_run_enrichments",
        keys=("session_id", "agent_run_id"),
        base="live_agent_runs",
        base_keys=("session_id", "id"),
    ),
    Level.turn: LevelSpec(
        prompt_version=4,
        subject=(
            "You are reading one turn of a coding session: what the person asked for, and "
            "what the agent did about it. Describe that turn."
        ),
        budgets=Budgets(total=30_000),
        renderer=prompts.render_turn,
        reader="turn_items",
        table="turn_enrichments",
        keys=("session_id", "source", "turn_id"),
        # `live_turns`, not `turns`: a fork's replay of another transcript's turn is a copy,
        # and the turn it copied is enriched under the transcript that ran it.
        base="live_turns",
        base_keys=("session_id", "source", "id"),
    ),
    Level.session: LevelSpec(
        prompt_version=4,
        subject=(
            "You are reading a summary of one coding session: what it cost, and a description "
            "of each thing it did, in order. Describe the session as a whole."
        ),
        # Smaller: a session carries one line per child rather than a transcript. Sessions
        # average 3.1 children and the longest recorded one has 92.
        budgets=Budgets(total=24_000),
        renderer=prompts.render_session,
        reader="session_items",
        table="session_enrichments",
        keys=("session_id",),
        # `describable_sessions`, not `sessions`: a row for a session the pass will never
        # refresh again is a zombie by the same definition as one whose session is gone, and
        # 45 such rows are already on disk from before the gate existed.
        base="describable_sessions",
        base_keys=("session_id",),
        riders=(prompts.RELAYING,),
    ),
}

# The levels a run describes, in the order it describes them — the order they are written in
# above. The agent runs are themselves split into rounds by parentage.
ROUND_ORDER = tuple(LEVELS)


def render(item: Item) -> str:
    """One item as its level's prompt, at that level's budgets.

    The enricher's one door into the renders. Take a `prompts.render_*` function directly to
    pass budgets, as the tests do.
    """
    spec = LEVELS[item.level]
    return spec.renderer(item, spec.budgets)


def instructions(level: Level) -> str:
    """The system prompt for one level. Versioned by its `prompt_version`, not by the hash."""
    spec = LEVELS[level]
    return prompts.instructions(spec.subject, *spec.riders)
