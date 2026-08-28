# Viewer polish: one naming system, richer popovers, a readable reading pane

Decisions were settled in [questions.md](questions.md); this design says what to build. File:line references were verified 2026-08-28 — re-verify at implementation.

## Problem

Three problems, one iteration:

1. **Tool-call naming is split across two systems.** The Python formatter registry (`src/hyphae/view/formatters.py`) holds the emoji + argument extraction, but api-call titles, the calls-log `tool_titles` column, and the tool popover's sibling list name tool calls through the SQL macro `tool_title` (`src/hyphae/analyze/macros.py:107`) and never see the formatters. Per-tool improvements silently diverge — the emoji regression on api-call titles is the symptom.
2. **The popover and NavTree hide structure.** Run/turn popovers show own-thread spend while the session popover includes subagents; nothing breaks subagent spend out. Compacted runs show no count, and compaction nodes have no popover at all.
3. **The reading pane misformats payloads and prose.** The Arguments preview always takes the bare `<pre>` path (`templates/_parts.html:164-170`); Result is highlighted only for `Read`. Agent-authored prose has no visual "quote" identity, and titles print markdown syntax as raw asterisks.

The constraint that decides the shape: **naming and formatting are display concerns and live in Python; SQL ships fields.** Record this convention in the AI guidance (see Slices).

## Call paths, current → proposed

**Tool naming, current:** `view_nav_tree_calls.sql:26` / `view_call_header.sql:42` compute `min_by(tool_title(t.input, …))` → `builders.call_node` prints it verbatim; `view_turn_calls.sql:50` `string_agg(tool_title(…))` → calls-log column; `view_numbers_tool.sql:12` → popover siblings. Only `tool_node` (`builders.py:188`) consults `formatters.FORMATTERS`.

**Proposed:** those queries drop `tool_title` and ship the same fields the `tool_fields` macro (`macros.py:147`) already defines (per-row, not aggregated). One Python entry point — `formatters.name_tool(fields) -> Formatted` — runs the registry and, when no formatter matches, the shape-driven fallback ported from the `tool_title` macro (`file_path` → `description` → head of raw JSON, read from a new bounded `input_head` member of `tool_fields`). Every surface (tool node, api-call title, calls log, popover siblings, errors list) calls it. `tool_title` and `tool_about` are deleted once no query selects them; `tool_path` and `tool_asked` stay as `tool_fields` internals.

**Popover numbers, current:** `_nav_tree.html:65` `hx-get` → `fragments.py:33 counted` → `view_numbers.sql`, whose `$kind` CASE selects own-thread calls for runs/turns but *everything* for the session.

**Proposed:** the session case becomes own-thread (`source = 'main'`) like every other kind; the query gains a subtree-spend aggregate (cost of runs hanging under the node, from the same run data `view_runs.sql` exposes). `view/numbers.py` adds the two lines when subtree spend > 0; `fragments/numbers.html` renders them per the mockup in questions.md Q3. Compaction rows join the popover mechanism: `Kind.COMPACTION` enters `NUMBERED` (`nodes.py:337`), a small fragment shows context before → after, freed, trigger (fields already in `view_compactions.sql`).

**Detail rendering, current:** `node_pages.py:361-377` builds Arguments with no syntax (bare `<pre>`), Result with suffix syntax for `Read` only; the pretty JSON path exists only in `fragments/raw.html`.

**Proposed:** one rule in `detail.py`/`node_pages.py`: Arguments always get `Syntax.JSON`; Result gets suffix syntax when known, else JSON-parse-then-plain (the highlighter's `_readable` already falls back for non-JSON). The pane preview and the fetch fragment share the same `parts.code` path.

## File-tree diff

```
src/hyphae/view/
  formatters.py        changed: name_tool() entry point; ported fallback; ToolSearch 🧰 (query), PushNotification 🔔 (message)
  inline_markdown.py   added: render (inline subset, no block elements) + strip; both autoescape-safe
  builders.py          changed: call_node / tool_node / bucket titles via name_tool; run rows carry compaction count
  nodes.py             changed: COMPACTION into NUMBERED; nav_tree_title/crumb/pane cuts operate on plain text, render at print
  numbers.py           changed: subagent-spend lines
  node_pages.py        changed: Arguments/Result syntax rule; drop session Title/Project fact rows
src/hyphae/analyze/
  macros.py            changed: tool_fields gains `message` and a bounded `input_head`; tool_title/tool_about deleted after migration
  queries/view_*.sql   changed: nav_tree_calls, call_header, turn_calls, numbers_tool ship fields; view_numbers subtree spend + session own-thread; compaction numbers fragment query added
templates/
  fragments/numbers.html            changed: breakout lines
  fragments/numbers_compaction.html added
  _nav_tree.html / _parts.html      changed: compaction badge (red pill, "N compaction(s)"); title spans render inline markdown; quote-border class on prose details
static/                             changed: badge + quote-border CSS
```

## Key contracts

- `formatters.name_tool(name, fields) -> Formatted(mark, words)` — the only place a tool call is named; `fields` is the `tool_fields` column set, `name` the dispatch key (not a `tool_fields` member). An empty `mark` means the caller leads with the tool's name
- `inline_markdown.render(text, *, links) -> Markup`, `cut(text, size, *, links, source_cap) -> Markup` and `strip(text) -> str` — bold/italic/code/links only, no block elements; every surface answers `links` explicitly (no default); links become `<a>` only where the surface is not already inside a link (reading-pane `<h1>`; NavTree rows, crumbs, walk, stepper render link text styled but not clickable); `<title>` and attributes use `strip`
- `view_numbers.sql` output gains `subtree_usd`; base lines mean own-thread for every kind including session
- Width cuts (`Node.nav_tree_title` etc.) measure the *plain* text so markdown syntax never eats the budget

## Chosen test seam

Python-tier page tests over recorded fixtures (`tests/view/`, gallery scenarios in `tests/view/scenarios.py`), asserting `data-field` contents — the existing seam. Bounds tests (`tests/view/test_bounds__node.py`) re-measure `NAV_TREE_ROW_BYTES` after the badge and markdown spans. The browser tier (`mise run e2e`) covers popover fetch behavior.

## Slices

1. **Naming core** — `name_tool()` with ported fallback; `view_nav_tree_calls` / `view_call_header` ship fields; api-call titles get emoji. Verify: existing title tests + a new assertion that an api-call NavTree row shows the tool glyph
2. **Naming everywhere** — calls log and popover siblings through `name_tool`; delete `tool_title` macros; add ToolSearch + PushNotification (add `message` to `tool_fields`). Verify: calls-log and sibling assertions on a fixture containing both tools
3. **Inline markdown** — module + wiring into title surfaces and `<title>` strip; links clickable in `<h1>` only. Verify: fixture with `**bold**` and a link in an enrichment description
4. **Popover breakout** — session own-thread semantics + subtree spend lines. Verify: numbers-fragment test on a fixture with subagents; zero-subagent node shows no breakout
5. **Compactions** — run-row red badge (count already in `view_runs.sql:32`) + compaction popover. Verify: fragment test + bounds re-measure
6. **Reading pane** — Arguments/Result JSON rule, quote borders on prompt/brief/said/thought/run result, drop session Title/Project facts, keep Task brief. Verify: tool-page and run-page template tests
7. **Docs** — the display-vs-retrieval convention in the AI guidance (see the answered question below); CONTEXT.md terms for any coined name (e.g. *compaction badge*); `docs/viewer.md` popover paragraph; `doc-sync` before the PR

## Decisions

- **The calls-log sub-line is composed in Python, not dropped** — `tool_about` printed a `Bash` row's description whenever a command was present; `builders.tool_about` prints a description the row's own title does not already say. The rule generalizes to every tool and keeps the old macro's promise that a row never prints one value twice
- **`view_turn_calls` ships every tool's fields, not a head slice** — a struct per tool costs little (absent members are NULL) and slicing to `$head_items` would silently drop tools the aggregated column used to name
- **Python owns naming; SQL ships fields** — rejected porting emoji into the SQL macro (string-building misery) and patching only the two call queries (leaves three naming systems)
- **Inline markdown everywhere visible, links clickable only outside existing links** — rejected reading-pane-only (NavTree is where labels are read) and strip-everywhere (loses the formatting asked for)
- **Popover base lines are own-thread for every kind, session included** — rejected keeping the session inclusive (perpetuates the inconsistency); the total-spend line is what matches the session-list cost column
- **Compaction badge on run rows only** — rejected turn/session badges: main-thread compactions are already visible as interleaved ⊟ nodes
- **Full compaction popover, not a `title` attribute** — consistency with every other row
- **Quote border on prose only** — tool payloads keep code styling; rejected bordering all details (border would mean "any detail"). **As built,** prose takes the same rail a payload `<pre>` already carries, so the pane holds one column of walled values and prose and payload are told apart by the face they are set in; rejected a rail in a second token or weight, which would have made the border itself a distinction to learn
- **Cut marks are source-aware** — a query ships `width + 1` raw characters (the existing cut protocol), so `inline_markdown.cut` takes that cap: it marks when it spends its visible budget *or* the raw string exceeds `source_cap`, and drops a trailing markdown run the source cut broke rather than printing its delimiters as text. Rejected marking on raw length alone (false mark on a complete title whose syntax outruns a narrow crumb) and cutting on rendered length in SQL (costly; moves every width)
- **Keep Task brief** (reverses the original ask) — enriched runs title themselves from the enrichment description, so the brief is not a duplicate

## Out of scope

- Per-turn compaction counts and badges (no query exists; the tree already shows main-thread compactions)
- Markdown in detail *values* beyond what already renders as prose
- Any change to the cost badge or context bar
- Redacting or changing what the store keeps

## Open questions

- ~~Verify `ToolSearch.query` and `PushNotification.message` against a recorded session~~ Answered 2026-08-28: both names confirmed in the store, session `4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b` (Claude Code 2.1.221), `tests/fixtures/spine`'s own source recording — see testing_plan.md slice 0. No record model gains a field
- ~~Exact home for the display-vs-retrieval convention note~~ Answered 2026-08-28: `.claude/rules/viewer-ui.md`, whose `paths` now cover the view's Python and its `view_*.sql` queries — `docs/documentation.md` puts a convention for a set of files under `.claude/rules/`, and the front matter is what puts it in front of the author about to break it
