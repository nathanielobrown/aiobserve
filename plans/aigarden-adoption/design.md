# Design: adopt aigarden — link linting, cogs, never-wrap prose, schema.md from record models, tooling catch-up

## Problem

Doc integrity is enforced by instruction, not tooling: `docs/doc-sync.md` step 4 tells the editor to check links by hand, and it isn't working — `docs/pull-requests.md:15` links `documentation.md#keep-docs-in-step-with-the-change`, an anchor that doesn't exist. Three tables restate code by hand (the route table and two bounds tables in `docs/viewer.md`, the Layout tree in `CLAUDE.md`); `docs/doc-sync.md` names the Layout tree a known manual-sync burden. `docs/schema.md`'s five field tables are a fifth: their meanings and evidence citations live only in the doc, while the parser that reads those fields lives in `extract/claude_code.py` — two artifacts describing one format, tied by nothing. Prose wrapping is unpoliced — we want mycelia's never-wrap convention (one physical line per paragraph, so a reworded sentence is a one-line diff and AI authors mirror the style). And ruff/pyrefly/pytest config lags mycelia's.

The constraint that decides the shape: `.github/workflows/check.yml` runs exactly `mise run check`, so any gate added to `check`'s depends is CI-enforced with no workflow edit. And aigarden is a mise-pinned prebuilt binary, so unlike shellcheck (which CI silently skips) it will exist on the runner.

**Shadow run** (verified: aigarden 0.1.3, default config, this repo, 2026-08-26): 168 findings — 80 `markdown-style` (auto-fixable), 69 `bare-path` (57 in `plans/` + `reports/`, 12 elsewhere), 18 `file-length` (11 code files over the 700-line default, 7 plan docs over the 8000-token markdown default), 1 `anchor-resolves` (the broken link above). Zero `link-target` / `link-case` / `import-target` / `code-doc-ref` findings. Gitignored `handoffs/`, `data/`, `mutants/` were not walked (aigarden honors gitignore), so no excludes are needed for them.

**Never-wrap needs an aigarden change first** (verified in source and by probe, 2026-08-26): aigarden 0.1.3's `[markdown-style] reflow = true` constructs rumdl's MD013 with `MD013Config::default()` — it re-wraps over-length paragraphs to the default column limit, the opposite of never-wrap. mycelia expresses never-wrap through rumdl knobs aigarden doesn't expose: `line-length = 100000`, `reflow-mode = "normalize"`, `code-blocks = false`. Nathaniel owns aigarden and has approved extending it (slice 0).

Two deliberate divergences from mycelia to know about: mycelia does **not** use aigarden's cog engine (it predates it; runs its own `tools/cog_docs.py`) — we use aigarden's native cog; and mycelia runs standalone rumdl for reflow because aigarden couldn't express it — we extend aigarden instead, which also opens mycelia's path off rumdl later.

## Call paths, current → proposed

Current: `mise run check` → `format-check`, `lint-check`, `typecheck`, `lint-shell`, `test`. Links checked by hand; tables hand-edited; wrapping unpoliced.

Proposed:

- `mise run check` additionally depends on `lint-docs-check` (`aigarden check`) and `cogs-check` (`aigarden cog --check`)
- `mise run check-fast` additionally depends on `lint-docs` (`aigarden check --fix`) — fix-mode inner loop, mirroring `format`/`lint`; with never-wrap configured, this is also the prose formatter
- `mise run cogs` (`aigarden cog --write`) regenerates stale blocks in place; `cogs-check` never writes (separate code paths in aigarden by design)
- `mise run mv-doc <src> <dst>` → `aigarden mv`: move a doc and rewrite every reference
- Cog data flow: `aigarden cog` parses `<!-- aigarden:cog sh "uv run python tools/gen_routes.py" -->` … `<!-- aigarden:end -->` markers in tracked markdown, runs the command with cwd = repo root, splices stdout between the markers. Generators import the installed package (`hyphae.view.app`, `hyphae.view.bounds`), so `uv run` from the project venv is the invocation; CI's `mise run sync` precedes `check`, so the venv exists there

## File-tree diff

```
(upstream) ~/repos/aigarden      ~ slice 0: [markdown-style] gains never-wrap knobs; release 0.1.4+
mise.toml                    ~ [tools] aigarden pin (0.1.4+); [settings] minimum_release_age_excludes; tasks lint-docs, lint-docs-check, cogs, cogs-check, mv-doc; check/check-fast wiring
aigarden.toml                + rule config (contract below)
tools/gen_routes.py          + viewer.md route-table generator
tools/gen_bounds.py          + viewer.md bounds + URL-knob table generator
tools/gen_layout.py          + CLAUDE.md Layout-tree generator
tools/gen_schema.py          + schema.md field-table generator (reads the record models)
src/hyphae/extract/records.py  + Pydantic models of Claude Code's raw record shapes: docstrings + Field descriptions + evidence metadata
tests/tools/                 + generator unit tests
docs/viewer.md               ~ three tables become cog blocks
CLAUDE.md                    ~ Layout tree becomes a cog block
docs/*.md, README.md, etc.   ~ one-time never-wrap normalize commit (~61 wrap points in living docs)
docs/documentation.md        ~ "Prefer facts that update themselves" gains the generated form; cog how-to; never-wrap stated as the convention
docs/pull-requests.md        ~ fix the broken anchor
.claude/rules/viewer-ui.md   ~ 10 shorthand paths ("view/nodes.py") → repo-root paths, per the repo's own path rule
docs/schema.md               ~ field tables become cog blocks fed by the record models; epistemology prose stays hand-written; also rephrase one path-shaped string
.claude/README.md            ~ rephrase one path-shaped string that isn't a repo path
pyproject.toml               ~ pydantic joins main dependencies (the record models live in src/)
CONTEXT.md                   ~ new terms (cog, cog block)
pyproject.toml               ~ slice 6: ruff select expansion + pins, pyrefly strict, pytest filterwarnings; pyrefly project-includes += tools; mutmut also_copy += tools
```

## Key contracts

**aigarden upstream (slice 0)**: `[markdown-style]` grows the knobs never-wrap needs, mirroring rumdl's `MD013Config`: normalize reflow-mode, a line-length, and fences/tables left untouched (`code-blocks = false` semantics — mycelia's proven combination). Exact config spelling is an upstream design call (a single `reflow = "never-wrap"` mode vs. raw knobs). Ships as 0.1.4+; this repo pins it.

**`aigarden.toml`** (key names inferred from research — verify against `aigarden explain` at implementation):

- `[markdown-style]` — never-wrap on. Hygiene (trailing spaces, tabs, blank runs, final newline) stays on everywhere walked
- `[per-file-ignores]`:
  - `"plans/**" = ["bare-path", "file-length", "markdown-style"]` — historical documents: citations describe the repo as it was, and reflowing them would churn history for no reader (440 of the repo's 704 wrap points live here)
  - `"reports/**" = ["bare-path"]` — same citation logic; style stays on (zero wrap points today)
  - `"src/hyphae/analyze/templates/**" = ["markdown-style"]` — prompt templates: their bytes are model input, and a reflow would silently change prompts and stale every enrichment stamp
  - the 11 current over-budget code files each mapped to `["file-length"]` (the ratchet: new files held to 700 lines, offenders shrink as touched)
- Config typos fail loudly (`deny_unknown_fields`, exit 2) — aigarden is pre-1.0; the exact mise pin means a break surfaces only on a deliberate bump

**Generator contract**: each `tools/gen_*.py` exposes `generate() -> str` and a `main()` that prints it; emits body content only (aigarden owns the framing newlines); crashes on any surprise rather than emitting a partial table. `gen_routes.py` lifts each reader-facing handler's docstring first line for the description column — the docstring becomes the single source, backfilled where missing. `gen_layout.py` keeps a curated entry list in the script and lifts glosses from package docstrings (code entries) and first sentences (docs entries). Generator output must not hard-wrap (it feeds never-wrapped docs).

**Record models (`extract/records.py`)**: one Pydantic model per registered record type, `extra="allow"` (Claude Code adds fields without notice; only record *types* are closed-world). Each field carries `Field(description=...)` for the Meaning column and evidence metadata (fixture path + CC version, via `json_schema_extra`) for the Evidence column — `gen_schema.py` crashes on a field missing either, turning schema.md's "every claim needs a recording" rule into a code-level requirement. Shared fields (`uuid`, `timestamp`, session context) are defined once on base-model mixins; the generator derives the Records column from which models inherit a field, so nothing is stated twice. Two drift tests tie the models to the parser: every registry enum member (RecordType, SystemSubtype, ContentBlock) has a model or documented value, and every field the models document appears in `claude_code.py`. **As built,** the models are a package, `src/hyphae/extract/records/`: `evidence` carries `Cited`, `blocks` and `shapes` carry the declarations, and `schema` is the walk `gen_schema.py` reads.

**mise pin**: `"github:nathanielobrown/aigarden" = "0.1.4"` (or whatever slice 0 cuts) exact — never `latest` (mise freezes a `latest` resolve permanently) — plus `minimum_release_age_excludes = ["github:nathanielobrown/aigarden"]` so a just-cut release installs.

## Chosen test seam

Unit tests drive `generate()` directly and assert properties against the live code, not golden strings: every GET route in `app.routes` appears in the table or in the generator's explicit exclusion list; every bounds row equals the `bounds.py` constant it cites. Freshness is gated by `cogs-check` inside `check`, so drift needs no test of its own. Link and reflow rules get one-time deliberate-fault probes during implementation (break a link, hard-wrap a paragraph, watch `check` fail), not committed tests. The upstream never-wrap knobs get tests in aigarden's own suite (slice 0), including the fence/table-untouched property.

## Slices

0. **aigarden upstream** — add the never-wrap knobs to `[markdown-style]`, with tests pinning normalize behavior and fence/table safety; release, verified by `cargo test` there and a probe run against this repo. Nothing lands here until the release exists
1. **Gate lands** — mise pin, `aigarden.toml`, `lint-docs`/`lint-docs-check` wired into `check-fast`/`check`; fix the real findings (anchor, viewer-ui.md paths), auto-fix markdown-style, then the one-time never-wrap normalize as its own mechanical commit (~61 wrap points in living docs). Verify: `mise run check` green; a scratch broken link and a scratch hard-wrapped paragraph each fail it
2. **Cog seam + route table** — `tools/gen_routes.py`, the cog block in `docs/viewer.md`, `cogs`/`cogs-check` tasks in `check`, unit test. Verify: hand-edit the generated block → `check` fails; `mise run cogs` heals it
3. **Bounds tables** — `gen_bounds.py`, two blocks, test
4. **Layout tree** — `gen_layout.py`, CLAUDE.md block, docstring backfill, test
5. **`mv-doc`** — verify by moving a scratch doc: references rewritten, `check` green
6. **Tooling catch-up** — expand ruff select toward mycelia's curated list (adapted, not copied — e.g. skip `required-imports = ["from __future__ import annotations"]`), pin ruff exact, pyrefly `>=1.0` + `preset = "strict"` + promoted warn kinds, pytest `filterwarnings = ["error"]` with targeted ignores, pin uv exact in mise; fix the resulting findings. Verify: `mise run check`. Ordered after slice 1 so the doc churn is link-checked; independent of 2–5
7. **schema.md from record models** — `extract/records.py` with meanings + evidence migrated row by row from today's schema.md (each row's fixture citation checked against the fixture as it moves), `gen_schema.py`, cog blocks replacing the five tables, the two drift tests. Verify: generated tables carry every field the hand-written ones did (diff reviewed once, then the cog gate owns it); a field stripped of its evidence metadata crashes the generator. Depends on slice 2's cog seam. **As built,** four of the five tables became cog blocks: the subagent-metadata table counts a corpus rather than describing transcript fields, so it stayed hand-written
8. **Docs** — `documentation.md` gains the generated form as a new rung of "Prefer facts that update themselves" (without it, every cogged table reads as a violation of the written rule), a short cog how-to, and the never-wrap convention stated once; CONTEXT.md terms; doc-sync at PR time

## Decisions

- **aigarden native cog, not a port of mycelia's engine** — one tool, `cog-fresh` rides `check`, no engine to own; rejected: prettier `cogs.routes()` directives at the cost of a second doc system (user-confirmed)
- **Never-wrap prose, delivered by extending aigarden — no rumdl** — mycelia's rumdl split exists only because rumdl predates aigarden and aigarden lacks the knobs; we own aigarden, so add them upstream (user-confirmed, including the upstream change). Rejected: standalone rumdl (second tool, second config), and aigarden 0.1.3's `reflow = true` as-is (verified to mean "wrap at 80", the opposite convention)
- **Freeze `plans/**` style; normalize living docs once** — historical docs stay byte-stable, the mechanical diff stays small; rejected: repo-wide normalize (churns 440 wrap points of history for no reader)
- **Exclude prompt templates from markdown-style** — their bytes are model input; a formatter must never touch prompts. Rejected: full exclusion (link rules still valuably apply)
- **Tooling catch-up in the same change** as its own slice — rejected: separate follow-up (user-confirmed)
- **Ratchet for file-length** — rejected: budgets above today's max (too loose) and ignoring the rule (user-confirmed)
- **`per-file-ignores` for `plans/**` and `reports/**` citations, not `status-header`** — our plans carry no `**Status:**` headers; adopting that convention is its own change
- **Fix the 10 `viewer-ui.md` shorthand paths rather than suppress** — they violate `documentation.md`'s own repo-root path rule; the finding is correct
- **Generators as `tools/*.py` scripts, not package modules** — repo tooling, not shipped code; rejected: `src/hyphae/` placement
- **Glosses lifted from docstrings/first sentences, not a data file** — a spec file would duplicate prose that already exists
- **schema.md generated from descriptive Pydantic record models, parser unchanged for now** — meanings and evidence move into code as the single source (user-confirmed: DRY); the models are written with raw camelCase field names so the parser can later adopt them wholesale. Rejected for this change: rewriting `claude_code.py` to parse *through* the models — a 1156-line closed-world parser rewrite with corpus-wide re-extraction, its own project; the drift tests hold the two together until then. Also rejected: SQLAlchemy `doc=` params — they'd describe our store tables, not Claude Code's transcript fields, which is what schema.md documents
- **`extra="allow"` on record models** — Claude Code adds fields without notice and the parser today ignores unknown fields; `extra="forbid"` would turn every upstream addition into a crash on shapes we never read. The closed-world crash stays where it is: unregistered record types
- **Task names `lint-docs`/`lint-docs-check`** to match the house `lint`/`lint-check`/`lint-shell` naming; rejected: mycelia's `aigarden-check` (names the tool, not the function)

## Out of scope

- **Parsing through the record models** — the extractor keeps its dict-based reading this change; adopting the models as the parse layer (and the validation-semantics questions that come with it) is the named follow-up
- **A generated store-schema reference** — if wanted later, cog it from `_SCHEMA`/`_VIEWS` in `export/duckdb.py` plus `model.py`'s comments; distinct from schema.md and not requested
- **External URL liveness** — aigarden is offline by design; mycelia covers this with a separate lychee-based task outside `check`. Add later if dead links bite
- **Splitting the 11 over-budget files** (`view/app.py` at 2163 lines is the worst) — the ratchet holds the line
- **Cogging the saved-query list** — docs deliberately point at the directory instead
- **`descriptive-anchor` config** — no stable-ID pattern (ADR-nnnn, Tnn) exists here yet
- **Migrating mycelia off rumdl** onto the new aigarden knobs — enabled by slice 0, but mycelia's change to make

## Open questions

- Upstream config spelling for never-wrap (one named mode vs. raw MD013-shaped knobs) — settled in slice 0's aigarden design
- Do the reader-facing route handlers carry docstrings to lift? (verify at implementation; backfill is in slice 2 if not)
- Is `bounds.py` introspectable with the labels the tables need, or does the generator need a small label map? (verify)
- Exact `aigarden.toml` key for raising a budget without replacing defaults (`extend-budgets` per aigarden's design doc — confirm against `aigarden explain file-length`)
- How many warnings does the suite emit today? `filterwarnings = ["error"]` may need targeted ignores beyond the existing httpx one (verify in slice 6)
- Generated schema.md structure: today's tables group by aspect with hand-chosen row order and some multi-field rows; per-record-type generation will restructure them — acceptable per the clean-breaking-changes rule, but the first generated diff needs a real read
- A few schema.md rows may document fields the parser never reads (documented-but-unused) — decide per row whether they enter the models or a small "observed, unread" list
- Whether Claude Code still strips HTML comments from `CLAUDE.md` before the model sees them (mycelia's engine relies on this; markers in CLAUDE.md are free if so)

## Verified vs inferred

**Verified locally (2026-08-26)**: the shadow-run numbers; the broken anchor; the per-file finding lists; gitignored trees not walked; `reflow = true` in 0.1.3 is `MD013Config::default()` + reflow (source: `src/rumdl_adapter.rs::style_rules`) and reports nothing on short hard-wrapped lines (probe); wrap-point census: 704 total — 440 `plans/`, 180 fixture READMEs (built-in exclude), 23 prompt templates, 61 living docs; `schema.md` documents Claude Code's raw fields with fixture+version evidence; `model.py` is frozen dataclasses with rich comments; the store is raw-SQL DDL in `export/duckdb.py`; the extractor parses records as dicts with closed-world StrEnum registries and no typed raw-record models; pydantic is not currently a dependency. **Inferred from subagent research** (verify at implementation): remaining aigarden config key names, mycelia config excerpts, the `sh`-generator cwd contract.

## Appendix: tooling drift, mycelia → hyphae (feeds slice 6)

| Tool | mycelia | hyphae today |
| --- | --- | --- |
| ruff | `==0.15.17` exact; ~40 curated select groups; `docstring-code-format`; commented ignore list | `>=0.12` floor; 7 groups (E F I UP B SIM TID) |
| pyrefly | `>=1.0`; `preset = "strict"`, `min-severity = "warn"`, every warn-kind promoted to error, sub-config relaxes tests | `>=0.24`; includes + search-path only |
| pytest | `>=9.0.3`; `filterwarnings = ["error"]`; timeout 600 signal-method | `>=8.4`; one targeted warning ignore; timeout 120 |
| uv (mise) | `0.10.11` exact | `latest` |
| coverage | none — parity, both repos skip it deliberately | none |

Shared already: `unfixable = ["F401"]` (same AI-collaboration rationale), no coverage tooling, mise-owned gates.
