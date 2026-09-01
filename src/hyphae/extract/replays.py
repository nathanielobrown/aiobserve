"""Which lines of a session's transcripts are copies of another's.

A fork opens on the conversation it inherited, records and uuids alike, so one uuid can name
lines in several files of one session. This module decides which file owns each record; every
later copy is a replay, kept in the store and left out of any count (`docs/store.md`).

Ordering the transcripts is the whole problem, and `_transcript_order` says how it is settled.
"""

from datetime import UTC, datetime
from typing import Any

from hyphae.extract.agent_runs import is_fork
from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.transcript import Line, timestamp_of
from hyphae.model import MAIN_SOURCE

# Where a run whose meta names no spawn depth sorts among its siblings: after every run
# that does, since the depths Claude Code writes are small.
_UNKNOWN_DEPTH = 1_000_000


def replayed_lines(
    kept: dict[str, list[Line]], metas: dict[str, dict[str, Any]], session_id: str
) -> dict[str, set[int]]:
    """Which lines of each transcript an earlier one already held.

    A fork copies its parent's records verbatim, uuids included, so a uuid inside one
    session can name several files. It belongs to the first transcript in the order below;
    every later copy is a replay. Any other transcript repeating another's records means
    the order put the wrong file first, and stops the run.
    """
    owner: dict[str, str] = {}
    replays: dict[str, set[int]] = {}
    for name in _transcript_order(kept, metas):
        # A line carrying no uuid is a bookkeeping record, which no transcript can have copied.
        copies = {
            line.line_no: uuid
            for line in kept[name]
            if (uuid := line.uuid) is not None and uuid in owner
        }
        if copies and not is_fork(metas.get(name, {})):
            copied = copies[min(copies)]
            raise TranscriptSchemaError(
                f"Session {session_id}: transcript {name} repeats record {copied} from "
                f"{owner[copied]} without being a fork"
            )
        replays[name] = set(copies)
        for line in kept[name]:
            if (uuid := line.uuid) is not None:
                owner.setdefault(uuid, name)
    return replays


def _transcript_order(kept: dict[str, list[Line]], metas: dict[str, dict[str, Any]]) -> list[str]:
    """The session's transcripts, first to record a uuid first.

    Spawn depth leads, because a copied-history fork is spawned *by* the transcript it
    copies and so always sits deeper. Time alone cannot separate them: the fork's opening
    record is its parent's, timestamp and uuid alike, and 46 of the machine's 51 overlapping
    pairs tie on it (scanned 2026-08-07). Ordering those ties by agentId instead hands 335
    records of six real transcripts' own work to a fork.
    """
    agents = [name for name in kept if name != MAIN_SOURCE]

    def key(name: str) -> tuple[int, datetime, str]:
        # A meta that names no depth sorts last. The one such transcript on this machine
        # shares no uuid with any sibling, so where it sits changes nothing (2.1.186).
        depth = metas[name].get("spawnDepth")
        moments = [t for t in (timestamp_of(line.record) for line in kept[name]) if t]
        return (
            _UNKNOWN_DEPTH if depth is None else depth,
            min(moments) if moments else datetime.max.replace(tzinfo=UTC),
            name,
        )

    return [MAIN_SOURCE, *sorted(agents, key=key)]
