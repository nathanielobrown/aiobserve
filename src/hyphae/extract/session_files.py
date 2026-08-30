"""Which files make up one session, and what the set of them says.

A session is a transcript plus everything written beside it: each subagent's transcript and
meta pair, each workflow journal, and the files Claude Code offloaded a tool result to. This
module sorts that set, reads each file into lines, decides which lines are a replay of another
transcript, and builds the agent runs the pairs describe.

What those lines mean is `extract/transcript.py`; the extractor that drives both is
`extract/claude_code.py`.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from hyphae.extract.layout import (
    AGENT_PREFIX,
    JOURNAL_NAME,
    META_SUFFIX,
    SUBAGENTS_DIR,
    TOOL_RESULTS_DIR,
    TRANSCRIPT_SUFFIX,
    WORKFLOW_PREFIX,
    WORKFLOWS_DIR,
)
from hyphae.extract.records.registry import TranscriptSchemaError
from hyphae.extract.transcript import _fork_context, _Line, _timestamp
from hyphae.model import MAIN_SOURCE, AgentRun, OffloadFile
from hyphae.pipeline import SessionSource

# The `source` a workflow journal records under, after its `wf_<id>/` directory.
JOURNAL_SOURCE = "journal"

# Where a run whose meta names no spawn depth sorts among its siblings: after every run
# that does, since the depths Claude Code writes are small.
_UNKNOWN_DEPTH = 1_000_000

logger = logging.getLogger(__name__)


class _AgentFiles(NamedTuple):
    """One subagent's pair of files, and where the pair sat."""

    # The agentId: the file stem after `agent-`, and the `source` its records take.
    id: str
    # The `wf_<id>` fan-out directory it sat in, for the runs a workflow drove.
    workflow_id: str | None
    transcript: Path
    meta: Path


class _ClassifiedFiles(NamedTuple):
    """One session's files, sorted by what reads them."""

    transcript: Path
    agents: list[_AgentFiles]
    # Each workflow journal, paired with its `wf_<id>/journal` source. Archive only: the
    # runs it logs write their own transcripts.
    journals: list[tuple[str, Path]]
    offloads: list[Path]


def _replays(
    kept: dict[str, list[_Line]], metas: dict[str, dict[str, Any]], session_id: str
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
        copies = {line.line_no for line in kept[name] if line.record.get("uuid") in owner}
        if copies and not _is_fork(metas.get(name, {})):
            first = min(copies)
            uuid = next(line.record["uuid"] for line in kept[name] if line.line_no == first)
            raise TranscriptSchemaError(
                f"Session {session_id}: transcript {name} repeats record {uuid} from "
                f"{owner[uuid]} without being a fork"
            )
        replays[name] = copies
        for line in kept[name]:
            uuid = line.record.get("uuid")
            if uuid is not None:
                owner.setdefault(uuid, name)
    return replays


def _transcript_order(kept: dict[str, list[_Line]], metas: dict[str, dict[str, Any]]) -> list[str]:
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
        moments = [t for t in (_timestamp(line.record) for line in kept[name]) if t]
        return (
            _UNKNOWN_DEPTH if depth is None else depth,
            min(moments) if moments else datetime.max.replace(tzinfo=UTC),
            name,
        )

    return [MAIN_SOURCE, *sorted(agents, key=key)]


def _is_fork(meta: dict[str, Any]) -> bool:
    """Whether a run continues another transcript's conversation.

    `agentType: "fork"` agrees with the flag on all 52 fork metas on this machine
    (scanned 2026-08-07), so the flag alone answers it.
    """
    return bool(meta.get("isFork"))


def _classify(source: SessionSource) -> _ClassifiedFiles:
    """Sort a session's files by what reads them.

    The layout is closed-world like the record types: a file whose place we cannot name is
    a Claude Code change we need to see, not a file to skip.
    """
    transcript = _transcript_of(source)
    directory = transcript.with_suffix("")
    # Each agent's two files arrive independently; they are paired once both are seen.
    transcripts: dict[str, Path] = {}
    metas: dict[str, Path] = {}
    workflows: dict[str, str | None] = {}
    journals: list[tuple[str, Path]] = []
    offloads: list[Path] = []
    for path in source.files:
        if path == transcript:
            continue
        parts = path.relative_to(directory).parts
        if parts[:1] == (TOOL_RESULTS_DIR,) and len(parts) == 2:
            offloads.append(path)
            continue
        # A workflow's definition and the script that ran it, beside the runs they drove.
        if parts[:1] == (WORKFLOWS_DIR,):
            continue
        place = _companion(parts, source.id)
        if place.agent_id is None:
            journals.append((f"{place.workflow_id}/{JOURNAL_SOURCE}", path))
            continue
        (metas if place.meta else transcripts)[place.agent_id] = path
        workflows[place.agent_id] = place.workflow_id
    if transcripts.keys() != metas.keys():
        odd = transcripts.keys() ^ metas.keys()
        raise TranscriptSchemaError(
            f"Session {source.id}: agent runs {sorted(odd)} have a transcript or a meta, not both"
        )
    agents = [
        _AgentFiles(
            id=agent, workflow_id=workflows[agent], transcript=transcripts[agent], meta=metas[agent]
        )
        for agent in transcripts
    ]
    return _ClassifiedFiles(
        transcript=transcript, agents=agents, journals=journals, offloads=offloads
    )


class _Companion(NamedTuple):
    """Where one file under the session directory sits, and what it is."""

    # The `wf_<id>` directory it sat in, when a fan-out wrote it.
    workflow_id: str | None
    # The agentId its name carries, or None for a workflow's journal.
    agent_id: str | None
    # The `.meta.json` beside a subagent's transcript rather than the transcript.
    meta: bool


def _companion(parts: tuple[str, ...], session_id: str) -> _Companion:
    """Place one file under `subagents/`. A file we cannot place stops the run.

    The layout is closed-world like the record types: an unplaceable file is a Claude Code
    change we need to see, not a file to skip.
    """
    unknown = TranscriptSchemaError(
        f"Session {session_id}: unknown file {'/'.join(parts)} in its directory"
    )
    workflow = None
    if parts[:2] == (SUBAGENTS_DIR, WORKFLOWS_DIR):
        if len(parts) != 4 or not parts[2].startswith(WORKFLOW_PREFIX):
            raise unknown
        workflow = parts[2]
    elif parts[:1] != (SUBAGENTS_DIR,) or len(parts) != 2:
        raise unknown
    name = parts[-1]
    if workflow and name == JOURNAL_NAME:
        return _Companion(workflow_id=workflow, agent_id=None, meta=False)
    if not name.startswith(AGENT_PREFIX):
        raise unknown
    stem = name[len(AGENT_PREFIX) :]
    # `.meta.json` first: it is the longer suffix, and both end in "json".
    if stem.endswith(META_SUFFIX):
        return _Companion(workflow, stem[: -len(META_SUFFIX)], meta=True)
    if stem.endswith(TRANSCRIPT_SUFFIX):
        return _Companion(workflow, stem[: -len(TRANSCRIPT_SUFFIX)], meta=False)
    raise unknown


def _agent_runs(
    agents: list[_AgentFiles],
    kept: dict[str, list[_Line]],
    metas: dict[str, dict[str, Any]],
    replays: dict[str, set[int]],
    launches: dict[str, str],
    session_id: str,
) -> list[AgentRun]:
    """One row per subagent the session ran, from the meta Claude Code wrote beside it."""
    runs = []
    for agent in agents:
        meta = metas[agent.id]
        # A fan-out's agents are not spawned one by one, so their metas name no call. The
        # call that launched the whole fan-out stands in — it is what asked for the work.
        tool_use_id = meta.get("toolUseId") or launches.get(agent.workflow_id or "")
        if tool_use_id is None:
            # Real and expected: a teammate is started by the team mechanism, not by a
            # tool call. Said out loud because a silently dropped run hides a whole
            # delegated workload.
            logger.warning(
                "Session %s: agent run %s has no spawning tool call", session_id, agent.id
            )
        lines = kept[agent.id]
        moments = [t for t in (_timestamp(line.record) for line in lines) if t]
        # A fork's file opens with the conversation it inherited, so its own work starts
        # where the copying stops.
        own = [
            t
            for t in (
                _timestamp(line.record) for line in lines if line.line_no not in replays[agent.id]
            )
            if t
        ]
        runs.append(
            AgentRun(
                id=agent.id,
                session_id=session_id,
                # Absent for a run the session itself spawned.
                parent_agent_id=meta.get("parentAgentId"),
                tool_use_id=tool_use_id,
                agent_type=meta["agentType"],
                # Both absent when the caller named none.
                brief=meta.get("description"),
                model=meta.get("model"),
                workflow_id=agent.workflow_id,
                # Absent on one meta of the 2764 on this machine, a 2.1.186 session.
                spawn_depth=meta.get("spawnDepth"),
                is_fork=_is_fork(meta),
                fork_context_uuid=_fork_context(lines),
                started_at=min(own) if own else None,
                ended_at=max(moments) if moments else None,
            )
        )
    return runs


def _offload_file(path: Path, session_id: str) -> OffloadFile:
    """One `tool-results/` file, read whole — it is the only copy once Claude Code prunes."""
    data = path.read_bytes()
    try:
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(),
            lossy_decode=False,
            size_bytes=len(data),
        )
    except UnicodeDecodeError:
        # Not text at all — a fetched PDF — or text cut mid-character. Archived anyway:
        # the file is gone in a few weeks, and its size and name still say what ran.
        return OffloadFile(
            session_id=session_id,
            name=path.name,
            content=data.decode(errors="replace"),
            lossy_decode=True,
            size_bytes=len(data),
        )


def _transcript_of(source: SessionSource) -> Path:
    """The session's own transcript, among the files discovery collected."""
    name = f"{source.id}{TRANSCRIPT_SUFFIX}"
    for path in source.files:
        if path.name == name:
            return path
    raise TranscriptSchemaError(f"Session {source.id}: no {name} among its files")
