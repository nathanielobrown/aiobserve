"""The Claude Code extractor: which sessions a project has, and what each one holds.

Assembly. It finds a project's sessions, sorts each one's files (`extract/layout.py`),
reads each transcript into lines (`extract/transcript.py`) and those lines into entities
(`extract/parse.py`), and stamps the result with a fingerprint that decides re-extraction.

The reader below it is closed-world on purpose: every record type, every `system` subtype and
every tag a prompt can lead with is registered in `extract/records/registry.py`, and anything else
stops the run. What each field means, and the session that proves it, is in `docs/schema.md`.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hyphae import settings
from hyphae.extract.agent_runs import agent_runs
from hyphae.extract.layout import (
    DEFAULT_PROJECTS_ROOT,
    SessionFiles,
    classify,
    find_sessions,
    read_offload_file,
)
from hyphae.extract.parse import parse
from hyphae.extract.records.unknown import UnknownFields
from hyphae.extract.replays import replayed_lines
from hyphae.extract.transcript import (
    pr_links,
    raw_record,
    read_lines,
    resolve_duplicates,
    session_of,
    workflow_launches,
)
from hyphae.model import MAIN_SOURCE, SessionTrace
from hyphae.pipeline import SessionSource
from hyphae.projects import encode_project_path

EXTRACTOR_NAME = "claude_code"

# Bump on any change to what this parser produces: the version is folded into every
# fingerprint, so bumping it re-extracts the whole corpus on the next refresh.
EXTRACTOR_VERSION = "7"


@dataclass(frozen=True)
class ClaudeCodeSource(SessionSource):
    """One discovered session, still as the files that hold it.

    Discovery already knows where the transcript is and what sits beside it, so `extract()`
    is handed the structure rather than a flat list to guess its way back through.
    """

    files: SessionFiles


class ClaudeCodeExtractor:
    """Discovers and parses Claude Code sessions for one project."""

    def __init__(self, *, projects_root: Path = DEFAULT_PROJECTS_ROOT) -> None:
        self.projects_root = projects_root
        # One tally per extractor, so the session count beside a field means "sessions this
        # run refreshed" rather than "sessions in this file". Strict where a person is looking
        # and a tally in an extract, which is what `settings.UNIT_TESTING` decides.
        self.unknown_fields = UnknownFields(strict=settings.UNIT_TESTING)

    def sessions(self, project: Path) -> list[ClaudeCodeSource]:
        """Every session recorded for `project`, with the fingerprint of its files."""
        project_dir = self.projects_root / encode_project_path(project)
        return [
            ClaudeCodeSource(
                id=session.id,
                fingerprint=fingerprint(session.files(), project_dir),
                files=session,
            )
            for session in find_sessions(project, projects_root=self.projects_root)
        ]

    def extract(self, source: ClaudeCodeSource) -> SessionTrace:
        """Parse every file of one session into a trace.

        Every transcript the session wrote — its own and each subagent's — runs through the
        same parser, distinguished only by the `source` its rows carry.
        """
        files = classify(source.files)
        transcripts = [
            (MAIN_SOURCE, files.transcript),
            *((agent.id, agent.transcript) for agent in files.agents),
        ]
        lines = {
            name: read_lines(path, source.id, self.unknown_fields) for name, path in transcripts
        }
        journals = {
            name: read_lines(path, source.id, self.unknown_fields) for name, path in files.journals
        }
        metas = {agent.id: json.loads(agent.meta.read_text()) for agent in files.agents}
        # The archive keeps every line of every file, duplicates included; the normalized
        # tables below read each transcript's deduplicated view.
        raw_records = [
            raw_record(source.id, name, line)
            for name, rows in (*lines.items(), *journals.items())
            for line in rows
        ]
        kept = {name: resolve_duplicates(rows, source.id) for name, rows in lines.items()}
        replays = replayed_lines(kept, metas, source.id)
        parsed = [parse(rows, source.id, name, replays[name]) for name, rows in kept.items()]
        return SessionTrace(
            extractor=EXTRACTOR_NAME,
            extractor_version=EXTRACTOR_VERSION,
            session=session_of(kept[MAIN_SOURCE], source.id, files.transcript),
            turns=[turn for one in parsed for turn in one.turns],
            api_calls=[call for one in parsed for call in one.api_calls],
            tool_calls=[call for one in parsed for call in one.tool_calls],
            agent_runs=agent_runs(
                files.agents,
                kept,
                metas,
                replays,
                workflow_launches(kept[MAIN_SOURCE]),
                source.id,
            ),
            compactions=[one for parsed_one in parsed for one in parsed_one.compactions],
            # Main-transcript only: no subagent in the corpus records one (2026-08-07).
            pr_links=pr_links(kept[MAIN_SOURCE], source.id),
            offload_files=[read_offload_file(path, source.id) for path in files.offloads],
            raw_records=raw_records,
        )


def fingerprint(files: Iterable[Path], relative_to: Path) -> str:
    """A session's state, as one digest over the files that hold it.

    Covers every file, not just the main transcript: a subagent transcript or an offloaded
    tool result changes without the transcript changing. Folds in the extractor version so
    a parser upgrade re-extracts the corpus rather than leaving old rows parsed by old
    logic. Uses mtime, so copying the tree re-extracts everything — idempotent, just slow.
    """
    digest = hashlib.sha256(EXTRACTOR_VERSION.encode())
    for path in sorted(files):
        stat = path.stat()
        entry = f"{path.relative_to(relative_to)}\0{stat.st_size}\0{stat.st_mtime_ns}\0"
        digest.update(entry.encode())
    return digest.hexdigest()
