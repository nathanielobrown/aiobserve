"""One row per subagent a session ran, from the meta Claude Code wrote beside its transcript.

A run's meta is the only place its type, its brief and the call that spawned it are recorded;
its transcript holds the work. Both are read here — the meta for what the run was, the lines
for when it started and ended.
"""

import logging
from typing import Any

from hyphae.extract.layout import _AgentFiles
from hyphae.extract.transcript import _fork_context, _Line, _timestamp
from hyphae.model import AgentRun

logger = logging.getLogger(__name__)


def _is_fork(meta: dict[str, Any]) -> bool:
    """Whether a run continues another transcript's conversation.

    `agentType: "fork"` agrees with the flag on all 52 fork metas on this machine
    (scanned 2026-08-07), so the flag alone answers it.
    """
    return bool(meta.get("isFork"))


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
