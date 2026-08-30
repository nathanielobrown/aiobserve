"""The seams: what an extractor and an exporter owe each other, and the loop that drives them.

`refresh()` is the whole pipeline. It asks an extractor what sessions exist and what each
one currently looks like, asks the exporter what it already holds, and re-extracts only the
difference. Everything agent-specific lives behind `Extractor`; everything sink-specific
behind `Exporter`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

from hyphae.model import SessionTrace


@dataclass(frozen=True)
class SessionSource:
    """One session as discovery found it, in the two things the pipeline itself reads.

    An extractor subclasses this to hand its own `extract()` whatever else it will need —
    where the files are, a row id, a URL. Nothing here interprets that, which is what keeps
    the agent-specific half behind `Extractor`.
    """

    id: str
    # Changes whenever any of the session's files does. Comparing it against the sink's
    # copy is the only thing that decides whether a session is re-extracted.
    fingerprint: str


class Extractor[SourceT: SessionSource](Protocol):
    """Turns one agent's recorded sessions into traces. One implementation per agent.

    `SourceT` is the extractor's own `SessionSource` subclass: `sessions()` mints them and
    `extract()` is the only thing that reads what it added.
    """

    def sessions(self, project: Path) -> list[SourceT]:
        """Every session recorded for `project`. Cheap: it stats files, it does not read them."""
        ...

    def extract(self, source: SourceT) -> SessionTrace:
        """Read a session's files and build its trace."""
        ...


class Exporter(Protocol):
    """Writes traces into a sink. One implementation per sink."""

    def fingerprints(self) -> dict[str, str]:
        """Session id to the fingerprint the sink holds, for every session it holds.

        Sessions whose files are gone from disk stay in here: the sink is the archive.
        """
        ...

    def export(self, trace: SessionTrace, fingerprint: str) -> None:
        """Make the sink hold this session at this fingerprint, replacing what it held.

        All or nothing per session: returning means `fingerprints()` now reports this
        fingerprint, and raising means it still reports no new one, so the next run sends
        the session again. What a failure leaves in the sink is the sink's own business —
        the store rolls back to the old copy, an append-only backend keeps what landed.
        """
        ...


class RefreshResult(NamedTuple):
    """What one pass changed, in session ids — enough for a caller to report or assert on."""

    extracted: list[str]
    skipped: list[str]


def refresh[SourceT: SessionSource](
    project: Path, *, extractor: Extractor[SourceT], exporter: Exporter
) -> RefreshResult:
    """Bring the sink up to date with what is on disk for `project`.

    Idempotent by construction: an unchanged session is skipped, and a changed one is sent
    whole — nothing here diffs a session against what the sink already holds. A session in
    the sink whose files are gone keeps its rows.
    """
    held = exporter.fingerprints()
    extracted: list[str] = []
    skipped: list[str] = []
    for source in extractor.sessions(project):
        if held.get(source.id) == source.fingerprint:
            skipped.append(source.id)
            continue
        exporter.export(extractor.extract(source), source.fingerprint)
        extracted.append(source.id)
    return RefreshResult(extracted=extracted, skipped=skipped)
