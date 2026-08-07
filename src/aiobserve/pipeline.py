"""The seams: what an extractor and an exporter owe each other, and the loop that drives them.

`refresh()` is the whole pipeline. It asks an extractor what sessions exist and what each
one currently looks like, asks the exporter what it already holds, and re-extracts only the
difference. Everything agent-specific lives behind `Extractor`; everything sink-specific
behind `Exporter`.
"""

from pathlib import Path
from typing import NamedTuple, Protocol

from aiobserve.model import SessionTrace


class SessionSource(NamedTuple):
    """One session as discovery found it: what to read, and what state it was in."""

    id: str
    # Every file the session's records live in — the transcript, its subagent transcripts
    # and their metas, workflow journals, and offloaded tool results.
    files: tuple[Path, ...]
    # Changes whenever any of those files does. Comparing it against the sink's copy is
    # the only thing that decides whether a session is re-extracted.
    fingerprint: str


class Extractor(Protocol):
    """Turns one agent's recorded sessions into traces. One implementation per agent."""

    def sessions(self, project: Path) -> list[SessionSource]:
        """Every session recorded for `project`. Cheap: it stats files, it does not read them."""
        ...

    def extract(self, source: SessionSource) -> SessionTrace:
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
        """Replace everything the sink holds for this session, atomically."""
        ...


class RefreshResult(NamedTuple):
    """What one pass changed, in session ids — enough for a caller to report or assert on."""

    extracted: list[str]
    skipped: list[str]


def refresh(project: Path, *, extractor: Extractor, exporter: Exporter) -> RefreshResult:
    """Bring the sink up to date with what is on disk for `project`.

    Idempotent by construction: an unchanged session is skipped, and a changed one is
    replaced wholly rather than appended to. A session in the sink whose files are gone
    keeps its rows.
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
