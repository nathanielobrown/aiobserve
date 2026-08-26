# Domain language

The canonical name and one-line meaning of every aiobserve concept. One term per physical line so a definition greps: `rg -i '^- \*\*(turn|pane)' CONTEXT.md`. Vocabulary only — the code and docs each section points at hold the detail. When a change coins or bends a term, update this file in the same change.

## The system in one sentence

An **extractor** reads each recorded **session** into the model, the **store** archives it, an **enrichment** pass describes it, and the viewer serves every **node** as a page.

## Telemetry

What one session recorded. Entities: `src/aiobserve/model.py`; relationships: `docs/store.md`; Claude Code's own field names: `docs/schema.md`.

- **Session** — one recorded Claude Code session: the main transcript plus everything its subagents wrote
- **Project** — the absolute, symlink-free working directory a session ran in
- **Thread** — one stream of records within a session: `main` or an agent run's id; the store column is `source`
- **Transcript** — the JSONL file Claude Code wrote for one thread
- **Turn** — one prompt and all the work it drove, until the next prompt
- **API call** — one model response, reassembled from the records that share its message id
- **Tool call** — one tool the model asked for, plus its result; `tool_use` is Claude Code's spelling, not ours
- **Agent run** — one subagent execution; its own turns, calls and tools form the thread keyed by the run id
- **Compaction** — where Claude Code summarized the conversation to free context; the transcript after one is lossy
- **Record** — one verbatim transcript line; the flat archive every normalized row derives from
- **Offload file** — tool output Claude Code wrote to a file instead of the transcript
- **Replay** — rows a fork or resume copied from another transcript; kept in the store, excluded from the corpus

## Pipeline

The extract → store → export seam: `src/aiobserve/pipeline.py`; the store: `docs/store.md`; OTLP: `docs/otlp-export.md`.

- **Extractor** — reads one agent's sessions into the model
- **Exporter** — writes the model to a sink; the store and OTLP are sinks
- **Store** — the trace store: one DuckDB file, one table per entity, the durable archive rather than a cache
- **Fingerprint** — changes when any of a session's files do; the only thing deciding re-extraction
- **Corpus** — the rows minus every replayed copy: the basis for any cross-session count
- **Rollup** — one row per session: counts, tokens, cost
- **Span** — a store row's OTLP shadow; one OTLP trace per session

## Enrichment

Model-written descriptions beside the telemetry: `docs/enrichment.md`; the vocabularies: `src/aiobserve/enrich/taxonomy.py`.

- **Enrichment** — one accepted model answer about one item: description, category, outcome, friction
- **Level** — the three kinds a pass describes: turn, agent run, session
- **Category / Outcome** — the closed vocabularies for what kind of work it was and how it ended
- **Stamp** — the input hash and versions that decide re-enrichment; a mismatch is what `stale` means
- **Pass** — one person-started, bottom-up enrichment or analysis iteration; never call it a run

## Viewer pages

What each page shows and cites: `docs/viewer.md`; the routes: `src/aiobserve/view/app.py`.

- **Projects page** — `/`, the landing page: every project and its recent sessions
- **Session list** — `/sessions`: the filter form above one page of sessions
- **Node page** — the one page shape every node kind shares: tree beside reading pane
- **Errors page** — every failed tool call of a session, on every thread, in order
- **Records page** — one thread's raw records, verbatim
- **Query page** — the SQL behind a page; every footer cites one
- **Gallery** — recorded fixtures served as pages for UI work (`docs/ui-development.md`)

## Node-page anatomy

- **Node** — anything that gets a page: session, turn, agent run, api call, tool call, compaction, or bucket
- **Bucket** — a synthetic node gathering rows the transcript attached to nothing, kept visible rather than dropped or given a fake parent; it stands for no store row, so its title names what is missing
- **Unattributed** — a thread's bucket of api calls that answer no turn, such as calls before the first prompt
- **Unattached** — the session's one bucket of agent runs no tool call spawned; it spans every thread
- **Tree** — the left column: the one open path through the session (never "sidebar")
- **Presets** — the tree's depth choices and the control above it that picks one: full, no api calls, agents only (`?nav=`)
- **Cost badge** — the warm ground behind a tree row's dollar value, deepening with the row's share of what the session spent
- **Context bar** — the line under a tree row: how full the model's context window was when the node ended, with what the node added left bright at the tip
- **Pane** — any major region of a page; the node page splits into two: the tree and the reading pane
- **Reading pane** — the right column, reading one node whole
- **Body** — one node's rendered content: title, facts, enrichment, details; the reading pane holds one, an expansion another
- **Crumb chain** — the ancestors leading the reading pane, down to the node
- **Facts** — the labelled store fields under the title; the label registry is `src/aiobserve/view/labels.py`
- **Enrichment block** — what a pass wrote about the node: description, tags, friction, behind the `✨` glyph
- **Detail** — a fat value the reading pane previews, cut at 4,000 characters with the rest a fetch away (`?detail=`)
- **Children log** — the paged table of one kind of child under the details (`?log=`)
- **Expansion** — a child's body opened in place from a log row's View button
- **Walk** — the prev / next controls stepping along the node's own level
- **Error stepper** — the previous / all / next failure controls under the walk
- **Title** — the one derived name every surface prints for a node; a session's own is its *recorded title*

## Qualify these words

Each means several things until qualified.

- **Trace** — say *session trace* (one extraction result), the *trace store* (the archive), or an *OTLP trace* (one session's span tree)
- **Call** — say *api call* or *tool call*; bare `call` is only the viewer's kind name for the former
- **Run** — an *agent run*; a person-started iteration is a *pass*
- **Description** — an enrichment's summary; what the spawner typed for an agent run is its *brief* (the reading pane's label: "Task brief")
- **Model** — say which: the model that *answered* a call, the alias a run *asked for*, or the model that *wrote* an enrichment
