# Testing plan: convert the viewer from Jinja to htpy

Obligations for `plans/htpy-viewer/design.md`. Written 2026-08-29 and revised against the design's
resolution of this plan's six findings; every repo fact below was read this session and carries its
file:line, but treat those as hypotheses to re-check at implementation time. Obligation ids are
stable, not positional — O46–O48 were added later and sit in the level they belong to.

Most of this change is a refactor, so most of the suite is already the harness. The plan's centre
of gravity is therefore **what must pass untouched** (level 4) plus the handful of new contracts
the conversion coins: purity, escaping, component signatures, and the spaces htpy stops emitting.

The counts this plan and the design both use, verified here: `SCENARIOS` holds **39** entries
(`tests/view/scenarios.py:83`), of which **14** are full pages Chromatic archives
(`tests/e2e/routes.json`, `fragment: false`; `specs/scenarios.ts` filters on it). The remaining
**25 are fragments with no visual tier**, today or after. That shapes the fidelity story — see the
accepted gap at the end.

---

## 1. Subprocess — a fresh interpreter, because in-process state has already answered the question

The suite's conftest builds `TestClient`s, so `fastapi` sits in `sys.modules` before any test
runs. These two leaves exist because the thing they check is destroyed by the pytest process.

- **O1. Importing `hyphae.view.components` pulls in no web framework.** *Evidence:* a subprocess that
  walks `pkgutil.walk_packages` over `components/` and imports every module — so one added later
  is covered without anyone remembering — asserting `fastapi`/`starlette` absent from
  `sys.modules`, **plus a negative control in the same leaf**: the identical probe over
  `hyphae.view.app` reports `fastapi` present. Without the control a typo in the module name
  passes silently, and this is the only leaf standing behind the design's central structural claim.
- **O2. The gallery's frozen clock survives a reload worker.** Under `reload=True` uvicorn
  re-imports the factory in a child process, so a freeze done in the parent is lost, and every
  Chromatic snapshot rests on it. *Evidence:* a fresh interpreter with the store env vars set
  imports the gallery app factory and asserts `fmt.utcnow() == corpus_now(store)`, bounded by a
  `timeout=`. The subprocess import is exactly what a reload worker does, so it proves the factory
  owns the freeze without launching uvicorn. The existing in-process leaves
  (`tests/gallery/test_serve.py:165,191`) call `gallery(store)` directly and would pass identically
  if the worker lost it. **Lands at slice 6, beside the factory it tests.**

## 2. Unit — a component called directly: typed view-models in, `Html` out

No app, no HTTP, no store. This is the level the conversion creates; it did not exist under Jinja.

- **O3. A `Markup` child passes through as markup.** *Evidence:* each of the four producers the
  design names (`render.markdown`, `render.link`, `highlight.lit`, `nodes.Node.nav_tree_title`)
  fed to the component that consumes it; the rendered string contains the element, not `&lt;`.
  Real values from the fixture corpus rather than hand-built `Markup`, so the producers' actual
  output is what is proven.
- **O4. An attribute value is escaped even when it is a `Markup`.** htpy's behavior, verified in a
  scratch install at 26.5.1 and load-bearing for the whole escaping contract — pin it so an htpy
  upgrade cannot quietly reverse it. *Evidence:* a component rendered with `Markup("<b>&</b>")` in
  an attribute position; the output carries `&amp;` and `&lt;b&gt;`. This pins the behavior;
  O46 is what keeps a `Markup` from reaching that position in the first place.
- **O5. Every public component clears the machine-checkable signature floor.** *Evidence:* an AST
  scan over `components/**/*.py` — for each public function, every parameter is keyword-only, no
  parameter annotation names `Any`, `Row`, `Request`, `Response` or a bare `dict`, and the return
  annotation is `Html` or `Html | None` — the concrete union the design's Component contract names,
  which is also what keeps `bad-return` checked inside the package `bad-index` is off over. An AST scan rather than `inspect`, so the check reads the source
  the reviewer reads and needs no import. The non-vacuity floor is per-module — every module in
  `components/` defines at least one public component — rather than a package-wide count, which
  would be false until the conversion finishes. "Precise" above the floor — a `Kind` where a `str` would typecheck — is
  review's, written into `viewer-ui.md` by O47.
- **O6. A reader-visible space between two elements is an explicit `" "` child.** htpy emits zero
  inter-element whitespace, so every space Jinja wrote as literal template text must be restored
  by hand or the page reads `0errors`. *Evidence:* per join site the design names — the mark/glyph
  gap in a crumb, a walk button, a NavTree row, and the space before a unit word — a leaf
  asserting the rendered string contains exactly one space at that boundary. The enumeration is
  hand-made and that is its weakness; **the harness at level 6 is what finds the sites nobody
  listed**, and after merge this class of bug is an accepted gap (below).
- **O7. Every `nodes.Kind` renders a body and every `columns.Shape` renders a log.** The runtime
  half of the `match`/`assert_never` claim; the typecheck half is O11. *Evidence:* parametrized
  over `nodes.Kind` and `columns.Shape` themselves rather than a written list, so a kind added
  later is covered; each case asserts a non-empty render carrying the right `data-body` /
  `data-log` value.
- **O8. `assert_never` is a real gate, demonstrated once.** *Evidence:* a recorded demonstration in
  the PR body — delete one `match` arm in a scratch checkout, run `mise run typecheck`, paste the
  pyrefly error. A committed leaf would need a type-error fixture the checker also has to reject.

## 3. Static — source and config read off the tree

The regime checks. Each replaces something the conversion deletes; none may be vacuous.

- **O9. No component constructs a `Markup`.** *Evidence:* `Markup(` matched nowhere under
  `src/hyphae/view/components/**/*.py`, with a companion assertion that the four producer modules
  outside `components/` still match it, so an empty scan cannot pass.
- **O46. No attribute a component writes is handed a `Markup` producer.** htpy escapes a `Markup`
  in attribute position, so the failure is double-escaped visible text no committed reader sees —
  this scan is what stands in for a reader. *Evidence:* over `components/**/*.py`, no attribute
  kwarg names `nav_tree_title`, `crumb_title`, `markdown(`, `lit(` or `link(` — today's zero,
  pinned; the same grep the design audit ran over `templates/`, now committed. The text-bearing
  attributes the scan governs are enumerated in `viewer-ui.md` by O47 (`title=`, `data-*` values,
  `aria-*` labels, the htmx-config `content=`), each taking plain `str`. A companion assertion that at
  least one producer name still matches *somewhere* in `components/` (as children) — some, not
  all five, because a producer arrives with the component that consumes it — so a renamed
  producer cannot empty the scan silently.
- **O47. The rules the tests cannot carry are written down where a reviewer meets them.** Three
  obligations here are review's, not a leaf's, and a review rule nobody wrote down is not a
  control. *Evidence:* the `.claude/rules/viewer-ui.md` diff carries all four in so many words —
  the enumerated text-bearing attributes and the Markup-producer rule (O46's residual: a novel
  producer routed to an attribute), the typing rule above O5's denylist floor, the
  explicit-`" "`-child rule, and the accepted post-merge visible-space gap stated beside it.
- **O10. Every header field a page prints has a label, and every label is asked for.** The
  re-pointed `tests/view/test_app__headers.py:104-115`. *Evidence:* the same
  `(?:fact|label)\(\s*['"]([a-z_]+)` regex over `components/**/*.py`, unioned with `detail_of\(`
  and `COLUMNS`, equal to `set(LABELS)` — **plus a new guard that the components scan found a
  non-zero number of names.** Note the `previewed` half globs `Path(view_app.__file__).parent
  .glob("*.py")`, which is *not* recursive; it must become recursive or a `detail_of` call that
  moves into `components/` drops silently out of the check.
- **O11. The view package needs no per-line escape, and exactly one scoped narrowing.** The
  type-safety goal's own measurement, revised on contact: pyrefly cannot decide htpy's recursive
  `Node` alias, so `bad-index` is off over `components/**` by sub-config (design §Checker scope).
  *Evidence:* `mise run typecheck` green; `git diff` shows no `# pyrefly: ignore` added anywhere
  under `src/hyphae/view/` or `tests/` (the one that exists today, `tests/gallery/serve.py:78`,
  is deleted by slice 6); and the only `[[tool.pyrefly.sub-config]]` in `pyproject.toml` is the
  `components/**` `bad-index` one, carried with its canary (O48).
- **O48. The sub-config outlives its reason by at most one red.** *Evidence:* a leaf that runs
  `pyrefly check` over a minimal two-element htpy nesting under a config with no sub-config,
  asserting `bad-index` is reported (0.1 s, verified 1.2.0). The day a release decides recursive
  aliases this reds, and its message says to delete the sub-config and re-run `typecheck`. Until
  then the rule's bug class stays red-able at runtime: htpy raises `TypeError` at render for an
  invalid child, and O7 plus the scenario sweep render every component.
- **O12. Each swap vocabulary is written once.** The composability goal's checkable artifact:
  `hx-select-oob` today appears verbatim in three places. *Evidence:* a count of each swap
  attribute's literal occurrences in `components/**` — one per named dict (`PANE_SWAP`, the
  tail-row unsets, the popover overrides) and no more.
- **O13. The djLint regime leaves no residue.** *Evidence:* `git ls-files '*.html'` returns
  nothing; `jinja2`, `djlint` and `[tool.djlint]` are absent from `pyproject.toml`; `format-html`
  and `format-html-check` are absent from `mise.toml` including the `check`/`check-fast` edges;
  the djLint block is gone from `.vscode/settings.json`. `tests/tools/test_mise_tasks.py`'s
  `HTML_TASKS` block is deleted rather than adapted — its own `assert len(tracked) > 10` guard
  makes survival impossible.
- **O14. The data-attribute suite is untouched where the design claims parity.** The headline
  regression obligation, and the cheapest to audit. *Evidence:* at slice 7,
  `git diff --stat tests/view/` touches only the files this plan names as amended —
  `test_dev.py`, `test_app.py`, `test_app__headers.py`, `test_app__filters.py` (deleted),
  `budgets.py`, `test_bounds*.py` — and no other file under `tests/view/` gains a line.

  As built, seven more were amended, every one of them for the space obligation: a `data-*`
  reader strips the gap between two inline elements, so a leaf that could not see a lost space
  gained a `reads` assertion beside the one it already made. `test_app__list.py` (the pager's
  three phrases), `test_enrichment.py` (the pills), `test_node.py` (an expansion's heading:
  mark, space, title), `test_records.py`, and `conftest.py`, which is where `headings` moved
  once a second file read it. `test_node__logs.py` lost that helper. `test_lifecycle.py` is the
  one amendment that is not about spaces: it gained the leaf for a component that raises
  halfway down a page, which is why `Viewer.html` renders whole before the response exists.
  No leaf keyed to a `data-*` attribute changed what it asserts.

## 4. Integration — served HTML through `TestClient`, over the recorded fixture store

Where the existing suite lives (`tests/view/conftest.py` readers, all keyed to `data-*`). These
obligations are mostly "passes unchanged"; that is the point.

- **O15. Transcript text arrives inert on every page and fragment.** *Evidence:*
  `test_app__safety.py:test_planted_markup_arrives_inert` green with no assertion changed, across
  all fifteen responses it plants into. Its docstring's `|safe` sentence is reworded to the
  Markup-construction rule; the sentinels are invented, as its own comment already says, because
  no redacted fixture carries markup.
- **O16. The pane swap resolves to the same six attributes on the same elements.** *Evidence:*
  `tests/view/test_nav_tree__rows.py:64-70` green unchanged, read through `conftest.wired()`,
  which resolves inheritance the way htmx does.
- **O17. The swap stays on `#nav-tree-rows` and a row carries only `hx-get`.** A dict spread makes
  it easy to write the six attributes onto every row: behaviourally identical, and 3,217 times the
  cost. *Evidence:* the row element's own attribute set asserted to be `{href, hx-get, data-*}`,
  and the `NAV_TREE_ROW_BYTES` equality pin (O24) which such a spread would blow.
- **O18. The popover trigger's `unset` overrides do not reach the link beside it.** The sibling-span
  contract; `hx-disinherit` is rejected by the rules file. *Evidence:*
  `test_numbers.py:562-572` green unchanged — the trigger's `hx-select`/`hx-select-oob` are
  `unset` and `hx-push-url` is `false`, while the same row's link keeps the inherited pane swap.
- **O19. The `+N more` tail row keeps its own unset overrides.** *Evidence:*
  `test_nav_tree.py:422-433` and `:485` green unchanged.
- **O20. No page reaches off the machine, writes an inline style, or wears `htmx-indicator`.**
  Scraping test 1's replacement, re-asserted over served HTML. *Evidence:* the assertion sweeps
  all 39 scenario responses rather than 23 template files, which is strictly more than the source
  scan saw, and O21 is what proves 39 is everything.
- **O21. The htmx-config meta parses under htpy's quoting.** htpy emits a double-quoted,
  `&#34;`-escaped attribute; the current regex assumes single quotes and its `(config,) =`
  unpack raises. *Evidence:* the amended regex plus `html.unescape` before `json.loads`, asserting
  `includeIndicatorStyles is False`. **Lands at slice 3, when `/` converts — not slice 7.**
- **O22. The sweep covers the routes the app has.** *Evidence:*
  `test_bounds.py:465-474` — `{route.path for route in client.app.routes} == set(SCENARIOS)` —
  green at every slice, which is what proves no route was dropped, renamed or duplicated by the
  rewrite.
- **O23. A dev page is a prod page plus one line, on all 39 scenarios.** *Evidence:*
  `test_dev.py:166-181`. Its `TAG` constant pins Jinja's newline and indent; slice 1 amends it to
  accept either spelling, slice 7 re-pins it to htpy's bare tag and deletes the dual branch.
- **O24. The dev watcher stops classifying `.html`.** *Evidence:* the `event_for` parametrization
  amended so `.html` is no longer in `RENDERED`, and `reload_router`'s `watch_paths` default
  asserted to be `(STATIC,)`. The import of `TEMPLATES` going away is enforced by slice 7 deleting
  `templating.py`.
- **O25. A mid-render failure is a 500, never a truncated 200.** The stated reason for choosing
  `HTMLResponse(str(element))` over streaming; nothing tests it today because Jinja never offered
  the choice. *Evidence:* a component monkeypatched to raise mid-tree; the response is 500 and
  carries no partial markup.
- **O26. One body, two mounts.** *Evidence:* the existing expansion tests
  (`/fragment/body/...` versus the same node's reading pane) green unchanged, asserting the
  fragment's body block equals the page's. Slice 4 is where this must hold, before the node page
  moves.
- **O27. One citations function, two mounts.** *Evidence:* the citation block served by a node page
  and by `fragments/body` compare equal — the duplication between `_citation.html:16` and
  `fragments/body.html:29-31` is the composability win, and equality is what shows it landed.
- **O28. The gallery still indexes every scenario and is still a dev viewer.** *Evidence:*
  `tests/gallery/test_serve.py:90,129,140` green unchanged after `index.html` becomes a component
  calling `layout.page`.

## 5. Byte pins — measured through the app, then arithmetic over the measurement

`tests/view/budgets.py` + `test_bounds*.py`. Almost every pin is one-sided `<=`, so shrinking
bytes keeps slices green — which is what makes mid-branch coexistence work and what makes the
slice-7 re-pin a discipline rather than a test.

- **O29. `NAV_TREE_ROW_BYTES` is re-pinned inside slice 5, and it falls.** The one equality pin
  (`test_bounds__node.py:227`, `assert widest_row == budget`), currently 1929. *Evidence:* the
  leaf green with the new constant, and the new constant strictly less than 1929 — htpy emitting
  *more* than djLint-indented Jinja would mean a space was added, not restored.
- **O30. `bounds.SESSIONS` rises when it is re-derived.** It is pinned from both sides
  (`test_bounds.py:335-343`: the ceiling fits under `PAGE_BYTES`, ceiling+1 does not), so smaller
  rows force the ceiling up from 97. *Evidence:* both halves green at slice 7; the cog block at
  `docs/viewer-bounds.md:52` re-runs and prints the new number, so that one number self-updates.
- **O31. `HYPHAE_PIN_EXACT=1` turns every one-sided measured pin into an equality pin, and slice 7
  runs the suite under it until green.** This is what converts the re-pin from a checklist item
  nothing reds on into evidence. *Evidence:* two halves. The mode itself — with the variable set,
  each `measured <= MEASURED_*` leaf in `test_bounds*.py` also asserts equality, demonstrated by a
  leaf that inflates one constant and shows the mode reds where the default run passes. And its
  use — the slice-7 commit message or PR body records `HYPHAE_PIN_EXACT=1 mise run test` green,
  with the tightened constants in that commit's diff. Everyday runs keep the one-sidedness
  `bounds.py`'s docstring chose on purpose.
- **O32. The reader-facing budgets are unmoved and the measured ceilings shrink to measurement.**
  *Evidence:* the diff shows `PAGE_BYTES = 500_000` untouched, and `NODE_BYTES` /
  `EXPANSION_BYTES` set to the new `worst_*_bytes()` output with the same rounding slack their
  comments describe.
- **O33. `docs/viewer-bounds.md`'s derived figures move inside a cog block, so freshness polices
  them.** Paragraph `:66` carries a dozen derived byte figures (1,929; 6,205,593; 49; 157,633;
  362; 1,164,554; 7,245,973; the 14,027 spare) and `:72` carries "10.5 KB of page chrome plus 97
  rows at 5 KB" — all prose today, outside the two existing cog blocks at `:11` and `:52`, so the
  freshness check cannot see them. *Evidence:* a generator under `tools/` deriving each figure from
  `bounds.py` and `tests/view/budgets.py`; the figures inside a cog block; and
  `mise run cogs-check` reddening on a deliberately stale block. The prose around the numbers —
  which explains what each ceiling bought — stays outside the block.

## 6. Branch-local scratch — the both-engines text diff harness

Gitignored, never committed, deleted before the PR. It is the only oracle that covers all 39
scenarios for the class of bug the conversion actually risks, and it covers the one moment of mass
spacing risk — the conversion itself.

- **O34. The harness renders every scenario under both engines and their visible text agrees.**
  *Evidence:* for each of the 39 `SCENARIOS` URLs, at defaults and again at `WORST_KNOBS`, both
  responses' text content extracted, every whitespace run normalized to a single space and the
  ends stripped, compared equal. "Clean" means zero differing URLs; a difference is a lost or
  gained reader-visible space, which is exactly what levels 2–5 cannot see.
- **O35. The reference engine is a pinned worktree, so the comparison outlives slice 7.** *Evidence:*
  `git worktree add` at the commit *before* slice 1 serving the Jinja gallery on a second port,
  which is what keeps the reference alive after slice 7 deletes the in-branch templates. The window
  is slice 5 through the branch's last commit; a run on the last commit is the one that matters,
  because it compares the finished conversion against untouched Jinja.
- **O36. The harness leaves evidence behind after it is deleted.** A throwaway script proves
  nothing once it is gone. *Evidence:* its final run log — the commit sha, the URL count, and
  "0 differing" — pasted into the PR body, and `git status` clean of it at PR time.

## 7. Browser tier — real Chromium over the gallery (`mise run e2e`, out of `check`)

- **O37. The Chromatic baseline is reset once, by a human, over 14 full pages.** Every snapshot
  diffs on whitespace collapse. *Evidence:* the Chromatic build link with all 14 accepted,
  recorded in the PR body. Nathaniel's call on when.
- **O38. A second Chromatic run after the reset shows zero changes.** This is what proves the new
  render is deterministic rather than merely different — and it is cheap. *Evidence:* the second
  build reporting no changed snapshots.
- **O39. `specs/htmx.spec.ts` passes with no spec edited.** It drives real swaps and asserts where
  the pane landed and that `scrollTop` survived; the design's out-of-scope section promises
  identical routes and behavior. *Evidence:* `mise run e2e` green with
  `git diff tests/e2e/specs/` empty.

## 8. Per-slice gate — `mise run check` at each of the seven commits

Each slice's own commit is the artifact. The audit established which existing tests red at which
slice; those amendments must land *in* that slice, not after it.

- **O40. Slice 1 leaves `check` green, with `test_dev.py`'s `TAG` accepting both spellings.**
  *Evidence:* `check` on the slice-1 commit; the amended constant visible in that commit's diff.
  Converting `query.html` converts a scenario, so the sweep at `:166-181` sees a mixed corpus from
  this slice forward.
- **O41. Slice 3 leaves `check` green, with the htmx-config regex amended in the same commit.**
  *Evidence:* `check` on the slice-3 commit; O21's amendment in that diff.
- **O42. Slice 5 leaves `check` green, with `NAV_TREE_ROW_BYTES` re-pinned in the same commit.**
  *Evidence:* `check` on the slice-5 commit; O29 in that diff.
- **O43. Slice 6 leaves `check` green, with `mise.toml`'s djLint paths trimmed in the same commit
  that deletes `tests/gallery/index.html`.** djLint exits 2 on a missing path, so a task still
  naming the deleted file reds `check` outright. *Evidence:* `check` on the slice-6 commit, and
  the subprocess clock leaf (O2) in that commit's diff beside the factory it tests.
- **O44. Slices 2, 4 and 7 leave `check` green.** *Evidence:* `check` on each commit; slice 7's
  diff carries the re-pins (O29–O33) — including the `HYPHAE_PIN_EXACT=1` run and the regenerated
  `viewer-bounds.md` cog block — the scraping-test deletions (O13), and the bare-tag re-pin (O23).
- **O45. Mutation testing over the new components kills what the leaves claim.** The type-safety
  goal is that a wrong field name fails loudly; a survivor over a field is a claim no leaf makes.
  *Evidence:* `mise run mutate 'hyphae.view.components.*'` on the branch, run cold and serial, with
  the survivor list read and each survivor either killed or explained in the PR body.

---

## Accepted gap: visible-space durability after merge

One thing this plan cannot cover with a committed leaf, accepted in writing by the design's
Decisions rather than left as a finding.

**After merge, a deleted `" "` child is caught by nothing committed.** The `data-*` readers see
`0errors` and `0 errors` the same; every byte pin but `NAV_TREE_ROW_BYTES` is one-sided, so a
missing space *shrinks* the page and stays green; Chromatic watches 14 of 39 scenarios. Both fixes
were costed and rejected: a committed normalized-text digest per scenario fails on every wording
change — the exact brittleness `tests/view/conftest.py` names as its reason for reading `data-*`
and never prose — and per-fragment Chromatic means 25 unstyled partial baselines re-reviewed on
every copy edit.

What makes the gap acceptable is that **the space-deleting actor is gone**. A Jinja space was
literal template whitespace a formatter could reflow, which is why `{{ " " }}` had to exist; an
htpy space is an explicit `" "` child — code Ruff never reflows, removable only by an edit a
reviewer sees. That is the same protection every chrome *word* already has: nothing catches a
deleted "compactions" either. The conversion's own moment of mass spacing risk is covered by the
level-6 harness. O47 puts the rule and the gap in `viewer-ui.md` in so many words.

## Deliberately not covered

- **That every function in `cuts.py` has a caller.** `test_app__filters.py:175-195` is deleted with
  the Jinja registry it policed. Naming the loss honestly: Ruff does not flag an unused
  module-level function, so nothing replaces it, and an orphaned cut will sit in the tree until
  someone reads the file. Accepted — a dead formatter costs a reader a page, where a dead Jinja
  filter cost every render a name.
- **Render performance.** Settled by the audit's scratch bench (7.5 MB, 3,217 rows, ~220 ms via
  `str()`); slice 1's synthetic bench is a sanity check that produces a number for the PR body, not
  a gate. No committed timing assertion — a wall-clock threshold in CI is a flake generator.
- **Colours, popover placement, clamping, and the reload loop's feel.** Recorded as dated Chromium
  witness notes in `.claude/rules/viewer-ui.md`, as they are today. The reload story changes, so
  the witness note is rewritten with a new date rather than tested.
- **Store schema modelling for the listing and records rows.** The design scopes their typing to
  what their components print; no leaf here asks for more.
