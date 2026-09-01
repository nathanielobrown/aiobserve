# Context bar colours: a navy ramp for growth, gray for threads, no track

Implement the palette chosen from `prototypes-2.html#none-darker` in this directory (round 3, option 4), and regroup the context bar's CSS so its values sit in one block. Open the prototype beside the gallery while working: it is the target, rendered over the same fixtures.

## Problem

The context bar (`CONTEXT.md`: *Context bar*, *Band*) colours its tip by the row's kind: `--mark` blue on a turn or call, `--agent` purple on a run, `--free` green on a compaction, `--bad` red full-width on a maxed run, with `--dim` and `--faint` under them (`src/hyphae/view/static/style.css`, "The context bar" block). Read down a NavTree, the purple run bars and the red maxed bars pull the eye as hard as the growth the bar exists to show, and the six tokens are scattered across `:root`, the dark block and the paint rule.

The change: every thread bar (session, run, maxed run) is one muted gray; the three bands a turn or call draws are one blue ramp, dark base → medium past → bright added; a compaction's freed span stays green; the track under every bar goes. The red compaction pill on the run's row is what flags a maxed run now.

Constraints that decide the shape: the policy (`app.CSP`) forbids inline styles, so widths keep riding the `f`/`p`/`b` class ladder; a run's prior edge is always 0 (`view_runs.sql:35`), so a thread bar is one band by construction and needs no second gray.

## Call paths, current → proposed

Nothing outside the stylesheet moves. `nodes.py:Node.bar` keeps minting `f{n} p{n} b{n} maxed`; `components/nav_tree.py:_row` keeps writing them on the `<li>`. What changes is how the stylesheet spends them:

- **Current:** the ladder sets widths in `--ctx-fill` / `--ctx-prior` / `--ctx-base`; one rule paints four layers (`--faint`, `--dim`, `--ctx-tip` defaulting to `--mark`, `--line`); three rules override `--ctx-tip` by kind and `.maxed` forces the widths full
- **Proposed:** the ladder sets widths in `--edge-fill` / `--edge-prior` / `--edge-base`; one rule paints three layers through role properties `--band-base` / `--band-past` / `--band-added`, each defaulting to a palette token; `:is(.session, .run)` maps both `--band-past` and `--band-added` to `--ctx-thread`; `.compaction` maps `--band-added` to `--ctx-freed`; `.maxed` forces the widths full and nothing else

## File-tree diff

```
src/hyphae/view/static/style.css      changed: tokens, paint rule, kind overrides, ladder property names
tests/view/test_nav_tree__bars.py     changed: the two stylesheet leaves near line 295 and 348
tests/view/test_app.py                changed: the token roster and ramp test near line 170
docs/viewer.md                        changed: the two bar paragraphs (lines 87–89)
.claude/rules/viewer-ui.md            changed: the witnessed section on the bar (lines 78–85)
CONTEXT.md                            changed: *Band* gains the thread band; *Context bar* unchanged
plans/context-bar-colors/             kept: this plan and the two prototype pages are the record
```

**As built,** `style.css` had already split into eight per-concern sheets (`b899c7b`): the palette went to `tokens.css`, the paint rule and the kind overrides to `nav-tree.css`, and the bar's tests to `tests/view/pages/node/test_nav_tree__bars.py`. The seam held — `tests/view/conftest.py:viewer_css` joins every sheet, so a rule that moves between files moves nowhere a test can see.

## Key contracts

The palette, one block in `:root` and one in the dark media block. These are the values the prototype was chosen on; the comment on each is the fact the token stands for:

```css
/* The context bar's palette (the paint rule is below, under "The context bar"). Three blues
   run dark → bright along a bar so growth reads left to right; a thread's whole window is one
   gray; a compaction's freed span is the one green. */
--ctx-base:   #1c3f8f;  /* the context the session opened on, under a turn's bands */
--ctx-past:   #3a6fd8;  /* what stood before the node */
--ctx-added:  #4aa3ff;  /* what the node added: the tip */
--ctx-freed:  #1f8a4c;  /* what a compaction gave back */
--ctx-thread: #b0b0bb;  /* a session's or a run's whole window, maxed or not */
--ctx-height: 3px;
```

Dark: `--ctx-base: #2b4c8c; --ctx-past: #4f86e6; --ctx-added: #8ecbff; --ctx-freed: #5fd18a; --ctx-thread: #55556a;`

Tokens deleted: `--faint`, `--agent` (each has one use, in the bar), `--free` (renamed to `--ctx-freed`, its one use), `--ctx-tip`. Width properties renamed: `--ctx-fill/prior/base` → `--edge-fill/prior/base`, freeing `--ctx-*` for colours. No track layer: the paint rule has three `linear-gradient`s, not four, and a row's bar is only what it holds.

The paint rule and the kind overrides are in `prototypes-2.html` under `/* ═══ The context bar, restructured`; lift them, dropping `--ctx-track` and `--ctx-track-height` (the chosen option has no track). Every kind override stays keyed on the class the row already carries — no new class, no markup change.

## Chosen test seam

The served stylesheet, read by `TestClient` in the Python tier, as the two existing leaves do: regex over `/static/style.css` with comments stripped. Colours themselves are eyeballed on the gallery (`.claude/rules/viewer-ui.md`); what the tier holds is structure and scheme coverage.

## Slices

1. **Rename and restructure, colours unchanged.** Rename the width properties to `--edge-*`, introduce `--band-*` roles with today's tokens as defaults, keep the track. Update `test_nav_tree__bars.py::test_a_context_bar_is_drawn_by_three_families_of_class_one_rule_spends` for the new names and layer order. The gallery must look identical before and after. Verify: `mise run check`; a screenshot diff of `mise run gallery --port 8492` on the agent-run scenario is stronger. Commit alone — it is the refactor the feature rests on
2. **The palette.** Add the tokens, drop the track layer, map `:is(.session, .run)` to the thread gray and `.compaction` to freed, delete the old tokens. Rewrite `test_a_run_a_compaction_and_a_maxed_thread_each_take_the_tip_in_a_colour_of_their_own` to hold the new claims: session and run share one band token, compaction's added band is a token of its own, `.maxed` forces `--edge-fill: 100%`, and every `--ctx-*` colour is set in both schemes. In `test_app.py`, widen the token regex to `[a-z-]+`, replace the roster, and replace the `line → faint → dim` ramp with `base < past < added` by luminance in **both** schemes (the ramp runs dark to bright on light and dark paper alike), plus a check that `--ctx-thread` is not the paper. Verify: `mise run check`, then eyeball the run, compaction and unattached scenarios on the gallery against `prototypes-2.html#none-darker` in light and dark. Commit
3. **Docs.** `docs/viewer.md:87–89`: the bar has no track, a run takes the thread gray rather than "a colour of its own", a maxed run is drawn full in that gray and the pill says why. `.claude/rules/viewer-ui.md:78–85`: rewrite the witnessed bullets and re-witness them in a real Chromium, dating the entry. `CONTEXT.md` *Band*: add the thread band. Run `doc-sync`. Verify: `mise run check` (aigarden checks the links). Commit

Then `mise run e2e`; the Chromatic baseline will change on every node page, and accepting it is part of landing this.

## Decisions

- **Roles (`--band-*`) between the palette and the paint rule, not per-kind paint rules** — one paint rule, three one-line overrides; the alternative of a rule per kind repeats four gradients per kind
- **Drop the track rather than paint it transparent** — a layer nothing sees is a layer a reader of the rule has to explain; the alternative kept `--ctx-track` for a future that may not come
- **Rename widths to `--edge-*` rather than name colours `--ctx-color-*`** — the colours are what a person tweaks, so they get the short names; 63 ladder lines rename mechanically
- **Maxed run in the thread gray, full width** — the alternative kept the alarm red; rejected because the compaction pill on the same row already says it and a red bar was the loudest thing in the column
- **`--free` renamed, not aliased** — its one use is this bar; an alias is a second name for one fact
- **Thread bar one gray, no prior/added split** — not a choice: a run's prior is 0 by definition (`view_runs.sql:35`) and a session has no `added`

## Out of scope

- **Calls drawing the base band.** Only turns carry `b` (`view_nav_tree_calls.sql` has no base), so under a turn its calls show past-from-zero while the turn shows base-then-past. Changing that is a query change, and the decision has not been made
- **The popover** (`view_numbers.sql`, the `.popover` rules): it prints numbers, names no colour, and is untouched
- **Bar thickness**: 3px stays, as a token so it is one place to change

## Open questions

- Whether the base band should reach calls (above). Settled by looking at the gallery after slice 2 and deciding whether the column reads as one graph without it. **As built,** the column reads as one graph — every bar shares an origin and a scale — but the base band carries a meaning only on a turn: a call's past band runs from zero over the same span its parent turn draws as base, so the two name the same context differently
- `.claude/rules/viewer-ui.md` records the port the witnessing used and the date; use a port that is not 8477 (a live viewer) and say which
