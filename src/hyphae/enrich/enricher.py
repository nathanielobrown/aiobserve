"""One enrichment run: what is stale, what gets sent, what comes back, and what failed.

Rerunning is the retry. A failed item writes no row, so it is still stale next time, and a
succeeded one is not — there is no resume state to keep, and nothing to clean up after a
crash.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hyphae.enrich.client import BatchClient, EnrichRequest, Failed, Succeeded
from hyphae.enrich.items import Item, Level, level_of
from hyphae.enrich.levels import ROUND_ORDER, instructions, render
from hyphae.enrich.stamp import Stamp, Versions, stale
from hyphae.enrich.store import EnrichmentStore
from hyphae.enrich.validation import InvalidOutput, ItemFailure, validate


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
        # Each distinct diagnostic once: a run that failed the same way three hundred times
        # has one cause to read, and reading it is the only way to fix the next run.
        causes = dict.fromkeys(failure.diagnostic for failure in failures if failure.diagnostic)
        said = "".join(f"\n  {cause}" for cause in causes)
        super().__init__(
            f"{len(failures)} item(s) failed, wrote nothing:\n{listed}"
            + (f"\nwhat the transport said:{said}" if said else "")
        )


def plan(
    store: EnrichmentStore,
    model: str,
    *,
    versions: Versions,
    project: str | None,
    limit: int | None,
) -> list[PlannedItem]:
    """Every item a run would send now, in the order it would send them — for a dry run.

    The rounds a real pass makes, against a model that answers everything and fails nothing:
    what comes back is the list `enrich` sends under the same limit, so the operator approves
    the pass they pay for.

    An upper bound in one respect: a described item is counted as restating every prompt above
    it, because nothing writes and there is no answer to compare. A child re-described in the
    same words stops the cascade there and costs less than this quotes.
    """
    parents = store.item_parents(project)
    quoted: list[PlannedItem] = []

    def describe(sending: list[PlannedItem]) -> _Outcome:
        quoted.extend(sending)
        return _Outcome(
            failures=[],
            restated={key for entry in sending for key in _ancestors(entry.item.key, parents)},
        )

    _pass(store, model, versions=versions, project=project, limit=limit, describe=describe)
    return quoted


def enrich(
    store: EnrichmentStore,
    client: BatchClient,
    *,
    versions: Versions,
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
    enriched = 0
    failures: list[ItemFailure] = []

    def describe(sending: list[PlannedItem]) -> _Outcome:
        nonlocal enriched
        count, round_failures = _round(store, client, sending)
        enriched += count
        failures.extend(round_failures)
        # Nothing is declared restated here: the upserts are on disk, so the next round
        # re-reads and re-hashes its items and sees for itself which prompts moved.
        return _Outcome(failures=round_failures, restated=set())

    _pass(store, client.model, versions=versions, project=project, limit=limit, describe=describe)
    if failures:
        raise EnrichmentFailed(failures)
    return EnrichReport(swept=swept, enriched=enriched)


@dataclass(frozen=True)
class _Outcome:
    """What one round did with its items: what failed, and what it leaves stale above."""

    failures: list[ItemFailure]
    # Items whose prompts now restate a description this round wrote. Empty for a real pass,
    # which reads the new descriptions back out of the store instead of predicting them.
    restated: set[str]


def _pass(
    store: EnrichmentStore,
    model: str,
    *,
    versions: Versions,
    project: str | None,
    limit: int | None,
    describe: Callable[[list[PlannedItem]], _Outcome],
) -> None:
    """The rounds one pass makes, and what each of them is about to be asked.

    Shared by `enrich` and `plan` so a dry run cannot walk staleness by rules of its own:
    `describe` is the only difference between quoting a round and paying for it.
    """
    parents = store.item_parents(project)
    rounds: list[tuple[Level, set[str] | None]] = [
        (Level.agent_run, keys) for keys in _rounds(parents)
    ]
    # None: every item of the level. Every level above the runs is one round — no turn embeds
    # another turn, and no session embeds another session.
    rounds += [(level, None) for level in ROUND_ORDER if level is not Level.agent_run]
    remaining = limit
    # Items whose prompts embed something that failed. Writing one bakes a hole into a
    # description that the hash then calls current forever — the one failure a rerun cannot
    # heal, so a blocked item writes nothing and stays stale.
    blocked: set[str] = set()
    restated: set[str] = set()
    for level, keys in rounds:
        if remaining is not None and remaining <= 0:
            break
        entries = _plan_level(store, model, level, versions=versions, project=project)
        moved = set(
            stale({key: entry.stamp for key, entry in entries.items()}, store.stamps(level))
        )
        sending = [
            entry
            for key, entry in entries.items()
            if key in moved | restated and key not in blocked and (keys is None or key in keys)
        ][:remaining]
        outcome = describe(sending)
        for failure in outcome.failures:
            blocked |= _ancestors(failure.key, parents)
        restated |= outcome.restated
        remaining = remaining - len(sending) if remaining is not None else None


def _plan_level(
    store: EnrichmentStore, model: str, level: Level, *, versions: Versions, project: str | None
) -> dict[str, PlannedItem]:
    """One level's items, rendered and stamped as they stand right now.

    Reads and renders only — a dry run calls this and writes nothing. Call it when the round
    starts, never earlier: a child's new description changes what its parents render to.
    """
    return {
        item.key: PlannedItem(
            item=item,
            rendered=(rendered := render(item)),
            stamp=versions.stamp(level, rendered, model),
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
            case Failed(kind=kind, diagnostic=diagnostic):
                failures.append(ItemFailure(key=result.key, kind=kind, diagnostic=diagnostic))
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
