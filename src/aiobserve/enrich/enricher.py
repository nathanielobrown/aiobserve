"""One enrichment run: what is stale, what gets sent, what comes back, and what failed.

Rerunning is the retry. A failed item writes no row, so it is still stale next time, and a
succeeded one is not — there is no resume state to keep, and nothing to clean up after a
crash.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from aiobserve.enrich.client import BatchClient, EnrichRequest, Failed, Succeeded
from aiobserve.enrich.prompts import (
    PROMPT_VERSION,
    Item,
    Level,
    input_hash,
    instructions,
    level_of,
    render,
)
from aiobserve.enrich.store import EnrichmentStore, Stamp
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION
from aiobserve.enrich.validation import InvalidOutput, ItemFailure, validate


@dataclass(frozen=True)
class PlannedItem:
    """One item that would be sent: what it renders to, and what its row would be stamped."""

    item: Item
    rendered: str
    stamp: Stamp


@dataclass(frozen=True)
class EnrichReport:
    """What one run did. Returned only when every item succeeded."""

    swept: int
    enriched: int


class EnrichmentFailed(Exception):
    """Some items failed. Names their keys and how they failed — never what they said."""

    def __init__(self, failures: list[ItemFailure]) -> None:
        self.failures = failures
        listed = "\n".join(f"  {failure.kind}: {failure.key}" for failure in failures)
        super().__init__(f"{len(failures)} item(s) failed, wrote nothing:\n{listed}")


# The levels a run describes, in the order it describes them: bottom-up, because every prompt
# embeds its children's descriptions rather than their text. The agent runs are themselves
# split into rounds by parentage.
LEVELS = (Level.agent_run, Level.turn, Level.session)


def plan(
    store: EnrichmentStore, model: str, *, project: str | None, limit: int | None
) -> list[PlannedItem]:
    """Every item a run would send now — an upper bound, for a dry run.

    Hash-stale items plus every ancestor of one: a child's new description restates its
    parents' prompts, and no read can tell in advance whether the new description will differ
    from the old. A child re-described in the same words stops the cascade there and costs
    less than this quotes.
    """
    parents = store.item_parents(project)
    planned: dict[str, PlannedItem] = {}
    reached: set[str] = set()
    for level in LEVELS:
        entries = _plan_level(store, model, level, project=project)
        planned |= entries
        for key in store.stale_keys(level, {key: entry.stamp for key, entry in entries.items()}):
            reached |= {key, *_ancestors(key, parents)}
    return [entry for key, entry in planned.items() if key in reached][:limit]


def enrich(
    store: EnrichmentStore,
    client: BatchClient,
    *,
    project: str | None = None,
    limit: int | None = None,
) -> EnrichReport:
    """Describe every stale item, write what came back, and crash if anything failed.

    Runs go out deepest-first, one round per level of the spawn forest, and main turns last.
    Every round re-reads and re-hashes its level *after* the previous round's upserts: that
    is what carries a new child description up the tree, and planning the rounds up front
    would look identical until the day a description changed.
    """
    swept = store.sweep_zombies()
    parents = store.item_parents(project)
    rounds: list[tuple[Level, set[str] | None]] = [
        (Level.agent_run, keys) for keys in _rounds(parents)
    ]
    # None: every item of the level. Turns and sessions are one round each — no turn embeds
    # another turn, and no session embeds another session.
    rounds += [(Level.turn, None), (Level.session, None)]
    enriched, remaining = 0, limit
    failures: list[ItemFailure] = []
    # Items whose prompts embed something that failed. Writing one bakes a hole into a
    # description that the hash then calls current forever — the one failure a rerun cannot
    # heal, so a blocked item writes nothing and stays stale.
    blocked: set[str] = set()
    for level, keys in rounds:
        if remaining is not None and remaining <= 0:
            break
        entries = _plan_level(store, client.model, level, project=project)
        stale = store.stale_keys(level, {key: entry.stamp for key, entry in entries.items()})
        sending = [
            entries[key] for key in stale if key not in blocked and (keys is None or key in keys)
        ]
        count, round_failures = _round(store, client, sending[:remaining])
        enriched += count
        remaining = remaining - len(sending[:remaining]) if remaining is not None else None
        for failure in round_failures:
            blocked |= _ancestors(failure.key, parents)
        failures += round_failures
    if failures:
        raise EnrichmentFailed(failures)
    return EnrichReport(swept=swept, enriched=enriched)


def _plan_level(
    store: EnrichmentStore, model: str, level: Level, *, project: str | None
) -> dict[str, PlannedItem]:
    """One level's items, rendered and stamped as they stand right now.

    Reads and renders only — a dry run calls this and writes nothing. Call it when the round
    starts, never earlier: a child's new description changes what its parents render to.
    """
    return {
        item.key: PlannedItem(
            item=item,
            rendered=(rendered := render(item)),
            stamp=Stamp(
                input_hash=input_hash(rendered),
                prompt_version=PROMPT_VERSION[level],
                taxonomy_version=TAXONOMY_VERSION,
                model=model,
            ),
        )
        for item in store.items(level, project)
    }


def _rounds(parents: Mapping[str, str | None]) -> list[set[str]]:
    """The agent runs grouped so that every run follows the runs it spawned.

    Grouped by height rather than depth: a leaf goes in the first round whatever tree it
    belongs to, so the whole store's forest is described in as many rounds as its deepest
    branch has levels — four, over the recorded corpus. Only the runs: a run whose parent is
    a turn or a session waits for nothing, because those levels come after every round here.
    """
    waiting: dict[str, set[str]] = {
        key: set() for key in parents if level_of(key) is Level.agent_run
    }
    for key, parent in parents.items():
        if parent in waiting:
            waiting[parent].add(key)
    rounds: list[set[str]] = []
    described: set[str] = set()
    while waiting:
        ready = {key for key, children in waiting.items() if children <= described}
        if not ready:
            # Every remaining run waits on another: a run spawned by its own descendant.
            raise ValueError(f"agent run parentage has a cycle among {len(waiting)} run(s)")
        rounds.append(ready)
        described |= ready
        waiting = {key: children for key, children in waiting.items() if key not in ready}
    return rounds


def _ancestors(key: str, parents: Mapping[str, str | None]) -> set[str]:
    """Every item whose prompt embeds `key`, directly or through another item."""
    found: set[str] = set()
    parent = parents.get(key)
    while parent is not None and parent not in found:
        found.add(parent)
        parent = parents.get(parent)
    return found


def _round(
    store: EnrichmentStore, client: BatchClient, planned: list[PlannedItem]
) -> tuple[int, list[ItemFailure]]:
    """Send one level's stale items and write the answers, one row per success."""
    if not planned:
        return 0, []
    by_key = {entry.item.key: entry for entry in planned}
    results = client.submit(
        [
            EnrichRequest(
                key=entry.item.key,
                instructions=instructions(entry.item.level),
                content=entry.rendered,
            )
            for entry in planned
        ]
    )
    answered: set[str] = set()
    failures: list[ItemFailure] = []
    for result in results:
        # A key we did not send, or one sent back twice, means the client lost track of the
        # batch — the rows it would write belong to some other item.
        if result.key not in by_key or result.key in answered:
            raise ValueError(f"{type(client).__name__} answered {result.key}, which it was not")
        answered.add(result.key)
        entry = by_key[result.key]
        match result:
            case Failed(kind=kind):
                failures.append(ItemFailure(key=result.key, kind=kind))
            case Succeeded(output=output):
                try:
                    enrichment = validate(output)
                except InvalidOutput as invalid:
                    failures.append(ItemFailure(key=result.key, kind=invalid.kind))
                    continue
                store.upsert(entry.item, enrichment, entry.stamp)
    missing = set(by_key) - answered
    if missing:
        raise ValueError(f"{type(client).__name__} left {len(missing)} request(s) unanswered")
    return len(by_key) - len(failures), failures
