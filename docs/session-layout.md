# Session data layout

Where a Claude Code session's files sit on disk, and how the extractor joins them: a subagent transcript to the call that spawned it, a fan-out agent to its launcher, a copied record to the transcript that ran it first.

[The schema reference](schema.md) says what the fields inside those files mean. This document says which files there are.

- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` stores one session transcript as one JSON object per line
- `~/.claude/projects/<encoded-cwd>/<session-id>/` stores the session directory described below; `hyphae.extract.layout` walks this tree
- Claude Code's OpenTelemetry export provides a thinner live schema and is enabled per machine, not per repository

Claude Code forms `<encoded-cwd>` by replacing each `/` in the working directory with `-`: `~/repos/mycelia` becomes `-Users-nob-repos-mycelia`. This tree is shared across Claude accounts because `~/.claude-black/projects` is a symlink to `~/.claude/projects`. A transcript path therefore does not identify the account that wrote it.

## A session directory holds transcripts, metadata, and offloaded output

Of 575 mycelia transcripts, 104 have a session directory beside them (scanned 2026-08-07). Those directories contain only the path shapes below. The extractor crashes on an unknown path because Claude Code prunes these directories within weeks; silently skipping a file could erase the only evidence of a schema change.

| Path below `<session-id>/` | Count | Contents | Archive destination |
| --- | ---: | --- | --- |
| `subagents/agent-<id>.jsonl` | 2,275 | A subagent transcript | source `<id>` |
| `subagents/agent-<id>.meta.json` | 2,275 | The spawn metadata: `toolUseId`, `agentType`, and `spawnDepth` | `agent_runs` |
| `subagents/workflows/wf_<id>/agent-<id>.jsonl` | 180 | A parallel fan-out agent transcript, one level deeper | source `<id>` |
| `subagents/workflows/wf_<id>/agent-<id>.meta.json` | 180 | Only the workflow agent's `agentType` and `spawnDepth`; it has no spawning tool call | `agent_runs` |
| `subagents/workflows/wf_<id>/journal.jsonl` | 6 | The fan-out log, with `started` and `result` records keyed by agent | source `wf_<id>/journal` |
| `tool-results/<name>` | 567 | Tool output named by `persistedOutputPath` because it was too large for the transcript | `offload_files` |
| `workflows/wf_<id>.json` | 6 | The workflow definition | not read |
| `workflows/scripts/<name>.js` | 6 | The script that drove the workflow | not read |

A subagent id is often hexadecimal, but sessions can assign names such as `agent-audit-pr291-79ea2c606313e623.jsonl`. Use the complete stem after `agent-` as the source.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains a subagent; `tests/fixtures/workflow/`, CC 2.1.207, contains a fan-out and journal; `tests/fixtures/offload/`, CC 2.1.220, contains a persisted result.

## Subagent metadata records why the agent ran

Each observed subagent transcript has a neighboring `meta.json`, and each meta has a transcript: 2,764 pairs on the recording machine, with no unpaired files (scanned 2026-08-07). Because no recording establishes how half a pair should behave, the extractor crashes if it finds one.

| Key | Metas | Meaning |
| --- | ---: | --- |
| `agentType` | 2,764 | The agent definition, such as `general-purpose`, `auditor`, `workflow-subagent`, or a session-defined name. This is not a closed set |
| `spawnDepth` | 2,763 | `1` for an agent spawned by the session, higher for nested agents, and `0` for a teammate. Its absence in one CC 2.1.186 session is a recorded state, not a parse error |
| `description` | 2,584 | The one-line task summary from the spawning call |
| `toolUseId` | 2,510 | The `Agent` call that requested the run |
| `model` | 753 | The model alias chosen by the caller, such as `opus` |
| `parentAgentId` | 389 | The agent that spawned this run |
| `isFork` | 52 | The run replays another transcript's history or continues it by reference |
| `taskKind`, `teamName`, `color`, `planModeRequired`, `permissionMode` | 71 | Teammate fields; `taskKind` is `in_process_teammate` |
| `name`, `worktreePath`, `worktreeBranch`, `customAgentType`, `stoppedByUser` | 94, 86, 86, 39, 3 | Recorded but not yet read |

Of the 254 metas without `toolUseId`, 180 belong to workflow agents, 71 to teammates, and three to forks. The team mechanism starts a teammate without a tool call. Preserve that orphaned run with a warning; dropping it would recreate the prior importer's false claim that all agent runs came from direct tool calls.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains a spawned and nested run; `tests/fixtures/teammate/`, CC 2.1.211, contains an orphaned teammate.

## Read a run's ask and answer off the call that spawned it

What a run was asked and what it answered are not in the meta. Both are on the spawning call: its `prompt` and its `result`. Read the field rather than the tool name, because the tool is not always `Agent` and a fan-out shares one call among many runs:

```sql
-- data/traces.duckdb, every agent run, no time window. Scanned 2026-08-25.
SELECT tc.name,
       count(*) AS runs,
       count(DISTINCT (tc.session_id, tc.id)) AS spawning_calls,
       count(json_extract_string(tc.input, '$.prompt')) AS with_prompt,
       count(tc.result) AS with_result
FROM agent_runs a
JOIN tool_calls tc ON tc.session_id = a.session_id
                  AND tc.id = a.tool_use_id AND tc.source <> a.id
GROUP BY ALL;
```

| `name` | Runs | Spawning calls | With `prompt` | With `result` |
| --- | ---: | ---: | ---: | ---: |
| `Agent` | 2,555 | 2,555 | 2,555 | 2,554 |
| `Workflow` | 180 | 6 | 0 | 180 |

So 180 of the 2,735 runs with a spawning call — 6.6% — have no ask to read, because a fan-out is launched once and the launcher is asked in other words. The one `Agent` run without a result is a run whose parent received nothing. No result in the store is JSON, so what comes back is prose.

Count runs, not calls. `tool_calls.id` is unique within a session, not across the store: the same query keyed on `id` alone counts 2,629 `Agent` rows, 74 of which belong to a session whose runs point at something else.

*Evidence:* `tests/fixtures/spine/`, CC 2.1.221, contains an `Agent` call carrying a `prompt` and the result it returned; `tests/fixtures/workflow/`, CC 2.1.207, contains the `Workflow` call, whose input is a name and its arguments.

## Join a workflow agent through its launcher's run id

A fan-out does not spawn agents one by one, so its agent metas name no call. The launching `Workflow` call returns `toolUseResult.runId`, which matches the `wf_<id>` directory containing the agent transcripts. This is the only link from those transcripts to their launching tool call. All six workflow runs on the recording machine contain it (scanned 2026-08-07).

*Evidence:* `tests/fixtures/workflow/`, CC 2.1.207, contains the `Workflow` call, its result, and the named `wf_c30cc877-997` directory.

## Attribute copied history to the transcript that ran it first

All 52 observed fork metas pair `isFork: true` with `agentType: "fork"` (scanned 2026-08-07). The first transcript record identifies one of two fork shapes:

| First record | Forks | Meaning |
| --- | ---: | --- |
| `fork-context-ref` | 26 | The file copies no records. The opening record names `parentSessionId`, `parentLastUuid`, and `contextLength`; work begins mid-conversation |
| `user` or `system` | 26 | The file copies the parent's records verbatim, including uuids and timestamps, then appends the fork's work |

A copy is the original but for `agentId`, which each file rewrites to its own: of the 2,006 pairs of records that share a uuid across two transcripts of one session, on this machine's twelve such sessions, every pair differs there and no pair differs only elsewhere (scanned 2026-08-30). A copied record then appears in two files. The corpus contains 51 overlapping transcript pairs, each with a fork on one side; 25 are fork-to-fork, where one fork copies another's work. Attribute each record to the transcript that ran it first. Keep later copies but mark them `replayed`, so the archive retains what each file recorded without double-counting the work. This rule marks 1,617 records across nine sessions as replays. None appears in a non-fork transcript; such a replay would show that the ordering chose the wrong origin.

Order transcripts by `(spawnDepth, first timestamp, agentId)`, with the main transcript first. Depth must lead because a copied-history fork begins with its parent's timestamp. Of 51 overlapping pairs, 46 tie on the first timestamp; breaking those ties by agent id would wrongly assign 335 records from six original transcripts to their forks. A fork is spawned by the transcript it copies and is therefore deeper.

The one meta without `spawnDepth` sorts last. Its transcript, the subagent file `agent-a20276f6d8a4e5309.jsonl` under the `mac_settings` project, from CC 2.1.186, shares no uuid with a sibling, so its position does not affect attribution.

*Evidence:* `tests/fixtures/fork_origin/`, CC 2.1.215, contains a copied-history fork and the auditor it copied; `tests/fixtures/fork_byref/`, CC 2.1.202, begins with `fork-context-ref`.
