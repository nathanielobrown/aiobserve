# Domain language

The canonical name and one-line meaning of every hyphae concept. One term per physical line so a definition greps: `rg -i '^- \*\*(turn|pane)' CONTEXT.md`. Vocabulary only — the code and docs each section points at hold the detail. When a change coins or bends a term, update this file in the same change.

## The system in one sentence

An **extractor** reads each recorded **session** into the model, the **store** archives it, an **enrichment** pass describes it, and the viewer serves every **node** as a page.

## Telemetry

What one session recorded. Entities: `src/hyphae/model.py`; relationships: `docs/store.md`; Claude Code's own field names: `docs/schema.md`.

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
- **Record model** — the pydantic class the parser reads one record kind through, in `src/hyphae/extract/records/`; every field it declares is one the parser may read, and every field it does not is unknown
- **Offload file** — tool output Claude Code wrote to a file instead of the transcript
- **Replay** — rows a fork or resume copied from another transcript; kept in the store, excluded from the corpus

## Pipeline

The extract → store → export seam: `src/hyphae/pipeline.py`; the store: `docs/store.md`; OTLP: `src/hyphae/export/otlp_delivery.py`, `docs/otlp-export.md`.

- **Extractor** — reads one agent's sessions into the model
- **Exporter** — writes the model to a sink; the store and OTLP are sinks
- **Store** — the trace store: one DuckDB file, one table per entity, the durable archive rather than a cache
- **Fingerprint** — changes when any of a session's files do; the only thing deciding re-extraction
- **Price table** — what each model charges per million tokens and the window it answers in; one table, `src/hyphae/extract/pricing.py:MODELS`, read by the extract, the viewer, the analyze macros and the `hp enrich` quote
- **Corpus** — the rows minus every replayed copy: the basis for any cross-session count
- **Live** — a row no fork copied, plus every agent run: what a sink counts or ships; the trace's `live()` and the store's `live_*` views name the same rows
- **Rollup** — one row per session: counts, tokens, cost
- **Timeline** — one thread in outline, a row per turn in the order they ran: `session_timeline` for `main`, `run_timeline` for an agent run
- **Span** — a store row's OTLP shadow; one OTLP trace per session
- **Delivery ledger** — `otlp_delivery`: what one backend acknowledged of each session, and the fingerprints an OTLP send or census diffs against
- **Census** — the sessions and spans a send to one backend would ship now: the dry run

## Enrichment

Model-written descriptions beside the telemetry: `docs/enrichment.md`; the vocabularies: `src/hyphae/enrich/taxonomy.py`.

- **Enrichment** — one accepted model answer about one item: description, category, outcome, friction
- **Level** — the three kinds a pass describes: turn, agent run, session
- **Category / Outcome** — the closed vocabularies for what kind of work it was and how it ended
- **Stamp** — the input hash, versions and model a row was written under; a mismatch with today's is what `stale` means
- **Pass** — one person-started, bottom-up enrichment or analysis iteration; never call it a run

## Viewer pages

What each page shows and cites: `docs/viewer.md`; the code: one package per page under `src/hyphae/view/pages/`, whose routes `src/hyphae/view/app.py` extends onto the app.

- **Component** — one typed function building part of a page's markup with htpy; a page is Python, not a template (a page's own `markup`, over the shared `src/hyphae/view/components/`)
- **Projects page** — `/`, the landing page: every project and its recent sessions
- **Session list** — `/sessions`: the filter form above one page of sessions
- **Node page** — the one page shape every node kind shares: NavTree beside reading pane
- **Errors page** — every failed tool call of a session, on every thread, in order
- **Records page** — one thread's raw records, verbatim
- **Query page** — the SQL behind a page; every footer cites one
- **Offload page** — one chunk of an offload file, at the URL a tool call's body links to
- **Scenario** — one page of the fixture corpus by name: a URL, a title and a group, pinned in `tests/view/scenarios.py`
- **Gallery** — the scenarios served as pages for UI work (`docs/ui-development.md`)

## Node-page anatomy

- **Node** — anything that gets a page: session, turn, agent run, api call, tool call, compaction, or bucket
- **Bucket** — a synthetic node gathering rows the transcript attached to nothing, kept visible rather than dropped or given a fake parent; it stands for no store row, so its title names what is missing
- **Unattributed** — a thread's bucket of api calls that answer no turn, such as calls before the first prompt
- **Unattached** — the session's one bucket of agent runs no tool call spawned; it spans every thread
- **NavTree** — the left column: the one open path through the session (never "sidebar")
- **Presets** — the NavTree's depth choices and the control above it that picks one: full, no api calls, agents only (`?nav=`)
- **Knob** — one of the four things a node-page URL may name: the preset and the three sizes; every link a page mints carries the ones that are not defaults (`docs/viewer-bounds.md`)
- **Cost badge** — the warm ground behind a NavTree row's dollar value, deepening with the row's share of what the session spent; a row with agent runs under it draws two, `$own/$total`
- **Compaction badge** — the count on an agent run's NavTree row of how often its own thread compacted, in the alarm colour rather than a wash
- **Context bar** — the line under a NavTree row: how full the model's context window was when the node ended, in nested bands whose colours the row's kind decides — a turn runs dark to bright and ends on what it added, a session or a run is one flat gray
- **Band** — one span of a context bar: the context the session opened on, what stood before the node, what the node added, a thread's whole window, or what a compaction freed
- **Popover** — the numbers behind one NavTree row, fetched when a reader points at it or tabs to it: what the badge and the bar draw, written out for the node's own thread, with what the agent runs under it spent broken out below
- **Pane** — any major region of a page; the node page splits into two: the NavTree and the reading pane
- **Reading pane** — the right column, reading one node whole
- **Body** — one node's rendered content: title, facts, enrichment, details; the reading pane holds one, an expansion another
- **Crumb chain** — the line above the reading pane: the way out of the session — home, then the project — then every ancestor down to the node
- **Facts** — the labelled store fields under the title; the label registry is `src/hyphae/view/text/labels.py`
- **Enrichment block** — what a pass wrote about the node: description, tags, friction, behind the `✨` glyph
- **Detail** — a fat value the reading pane previews, cut at 4,000 characters with the rest a fetch away (`?detail=`)
- **Children log** — the paged table of one kind of child under the details (`?log=`)
- **Expansion** — a child's body opened in place from a log row's View button
- **Walk** — the prev / next controls stepping along the node's own level
- **Error stepper** — the previous / all / next failure controls under the walk
- **Title** — the one derived name every surface prints for a node; a session's own is its *recorded title*

## Repo tooling

The generators and the gate wrapper: `tools/`; how to write a generator and where a generated fact belongs: `docs/documentation.md`; which tasks a gate wraps and which stay loud: `mise.toml`; the browser tier: `docs/ui-development.md`.

- **Cog** — the splice `mise run cogs` performs: it runs the command a document names and pastes the output back into it
- **Cog block** — one splice in one document: the two markers and the generated text between them
- **Gate** — one task wrapped in `tools/gate.py`: a line when it passes, everything the tool said when it fails
- **Browser tier** — the Playwright specs under `tests/e2e/` that drive the gallery in a real Chromium; every other test the suite runs is the Python tier

## Qualify these words

Each means several things until qualified.

- **Trace** — say *session trace* (one extraction result), the *trace store* (the archive), or an *OTLP trace* (one session's span tree)
- **Tree** — say *NavTree* for the viewer's left column; a data structure, a spawn tree, or the Layout tree is just a tree
- **Call** — say *api call* or *tool call*; bare `call` is only the viewer's kind name for the former
- **Run** — an *agent run*; a person-started iteration is a *pass*
- **Description** — an enrichment's summary; what the spawner typed for an agent run is its *brief* (the store column `brief`, the reading pane's label: "Task brief")
- **Model** — say which: the model that *answered* a call, the alias a run *asked for*, or the model that *wrote* an enrichment
- **Cut** — a string shortened to a width, carrying one character past it so the page can mark it (the `cut` SQL macro, `view/text/format.py:cut`); the count a limit left off is *dropped*, printed as "+N more"
