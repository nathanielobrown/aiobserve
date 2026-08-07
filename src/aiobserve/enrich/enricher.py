"""One enrichment run: what is stale, what gets sent, what comes back, and what failed.

Rerunning is the retry. A failed item writes no row, so it is still stale next time, and a
succeeded one is not — there is no resume state to keep, and nothing to clean up after a
crash.
"""

from dataclasses import dataclass

from aiobserve.enrich.batches import BatchClient, EnrichRequest, Failed, Succeeded
from aiobserve.enrich.prompts import (
    PROMPT_VERSION,
    Item,
    Level,
    input_hash,
    instructions,
    render_turn,
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


def plan_turns(
    store: EnrichmentStore, model: str, *, project: str | None, limit: int | None
) -> list[PlannedItem]:
    """The main turns a run would send now, rendered and stamped.

    Reads and renders only — a dry run calls this and writes nothing. Call it when the round
    starts, never earlier: a child's new description changes what its parents render to.
    """
    planned = [
        PlannedItem(
            item=item,
            rendered=(rendered := render_turn(item)),
            stamp=Stamp(
                input_hash=input_hash(rendered),
                prompt_version=PROMPT_VERSION[Level.turn],
                taxonomy_version=TAXONOMY_VERSION,
                model=model,
            ),
        )
        for item in store.turn_items(project)
    ]
    by_key = {entry.item.key: entry for entry in planned}
    stale = store.stale_keys(Level.turn, {key: entry.stamp for key, entry in by_key.items()})
    return [by_key[key] for key in stale[:limit]]


def enrich(
    store: EnrichmentStore,
    client: BatchClient,
    *,
    project: str | None = None,
    limit: int | None = None,
) -> EnrichReport:
    """Describe every stale item, write what came back, and crash if anything failed."""
    swept = store.sweep_zombies()
    planned = plan_turns(store, client.model, project=project, limit=limit)
    enriched, failures = _round(store, client, planned)
    if failures:
        raise EnrichmentFailed(failures)
    return EnrichReport(swept=swept, enriched=enriched)


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
