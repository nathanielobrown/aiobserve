---
description: Viewer UI conventions
paths:
  - "src/hyphae/view/templates/**/*.html"
  - "src/hyphae/view/static/*.css"
  - "src/hyphae/view/static/*.js"
---

# Viewer UI

The viewer is server-rendered Jinja with three scripts on a shipped page: vendored htmx, `src/hyphae/view/static/nav-tree-width.js` for the one thing a reader sets that no URL carries, and `src/hyphae/view/static/nav-tree.js` for the two things a row's place on the screen decides. A fourth, `src/hyphae/view/static/dev-reload.js`, rides `hp view --dev` alone (below). These are the conventions a template has to hold to; what each page shows is in `docs/viewer.md`, and how to edit one with the page open in front of you — and the two traps in the formatter that owns their layout — is in `docs/ui-development.md`.

# One body, two mounts

A node's body is one macro in `_node_body.html`, mounted twice:

- Its **full view**, `node.html`, wraps the body with the NavTree, the crumbs above it, its enrichment, its previewed values, its children log, and prev/next
- Its **expansion**, `src/hyphae/view/templates/fragments/body.html`, is the body alone — opened in a log row while the reader stays on the parent

Render a node's facts in the body macro and nowhere else. A pane and a NavTree row that disagree tell a reader two stories about one node.

An expansion stops one level down. An api call's lists the tools it called, through the same log macro the page renders; every other kind stands a count and a link to its own page. No row inside an expansion opens another: an accordion of accordions is a page, and the node already has one.

Every node reachable in the pane has a URL that renders the whole page cold. Nothing may render only as a fragment.

# A node's title comes off the node

Print `Node.nav_tree_title`, `crumb_title`, `log_title` or `pane_title`, whichever fits the surface. Never join words in a template and never print a row's own column where a node is being named: the four are one title at four widths (`src/hyphae/view/nodes.py`), and a surface composing its own would be a second answer to what the node is called. What each kind is titled is in `docs/viewer.md`.

# Tooltips are native `title` attributes

`title="…"` on the element, and nothing else — no first-party script, and `app.CSP` allows no inline `style`. It costs no bytes on a page that doesn't hover and no code at all.

A `title` is worth its bytes where the mark on screen is smaller than what it means: the enrichment glyph, and the `*` a cost carries when our price table priced none of some calls under it. Text a reader can already read doesn't get one.

# The glyph is bare in the NavTree, spelled out in the pane

`✨` marks a title a model helped write and leads the whole of it, halves the session wrote included (`docs/viewer.md`). Write it through `parts.glyph(node)`, which reads the `GLYPH` and `GLYPH_CLASS` globals from `src/hyphae/view/enrichment.py`; don't type the character into a template.

Where a title repeats — a NavTree row, a crumb, a log row, a walk control — the glyph goes bare. The pane carries the one that says what the mark means: `parts.summary` hangs `Described.provenance` off it as a `title`, naming the model, when it ran, the prompt and taxonomy versions, and whether the row is stale. A `title` on every repeat would be the same sentence 400 times in one page's markup.

Every mark saying what a thing *is* — the kind of node a surface names, and the kind a children log's column is about — goes through `parts.mark(character)`, whose character comes from `nodes.GLYPHS` or a `Column` — the one place those characters are written, so a mark cannot mean one thing in a table and another in the NavTree. It is `aria-hidden` and carries no `title`: the word it stands for is already in the markup beside it (`docs/viewer.md`). The `<title>` element is the one place a mark goes in bare, because it is text and has no markup to hide it in.

A tool's own glyph is not one of these marks and does not go through `parts.mark`. It stands in for the tool's name inside the title's words (`src/hyphae/view/formatters.py`), so it rides as text wherever the title does — including a children log, which heads the lead in a column of its own and would drop a mark written there.

# A NavTree row is priced, not budgeted

`bounds.NAV_TREE_ROW_BYTES` is measured through the app, pinned with no slack, and spent 3,217 times on the worst page. An attribute added to `_nav_tree.html` is that many bytes of page, so re-measure with `tests/view/test_bounds__node.py` rather than guessing — the pin fails first, which is the point.

Link where you fetch: a row's `href` and its `hx-get` are the same URL, and both carry the page's knob suffix, so a click, a paste, and a bookmark serve the same bytes. The mount an expansion opens through carries it too, so the fragment's own links come back under the preset the reader was in.

# A pane swap says where it lands

A link that moves the reader without leaving the page carries six attributes, and all six have to be in effect on it: `hx-get` the node's URL, `hx-select="#reading-pane"`, `hx-target="#reading-pane"`, `hx-swap="outerHTML"`, `hx-select-oob="#nav-tree-rows"`, and `hx-push-url="true"`. The two that are easy to leave off are the two with defaults that look harmless: htmx aims at the clicked element, so a link without `hx-target` swaps the whole pane inside the `<a>` and leaves the pane showing the node the reader came from — the URL changes and the page does not. `hx-select` hands back the `#reading-pane` element itself rather than its contents, which is why the swap is `outerHTML` and not the default.

htmx reads all but `hx-get` off the closest ancestor carrying one, so the NavTree writes the five shared ones on `#nav-tree-rows` and a row's link carries only the URL. A children log writes them out per row instead: the body toggle beside each link is an `hx-get` that must not swap the pane, so a hoisted attribute would have to be undone on it — a line per row either way. `test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane` reads both mounts the way htmx resolves them, inheritance and all.

A fetch that is not a pane swap rides an element of its own beside the link. The popover a NavTree row fetches overrides every attribute `#nav-tree-rows` writes, and htmx walks up from whatever fetched: written on the `<li>`, those overrides would reach the link inside it and the click would stop swapping the pane. `hx-disinherit` is not the way out, because it stops the walk rather than skipping a level of it. So the trigger is a span of its own next to the link, and `hx-trigger`'s `from:closest li` keeps the row as the thing a reader points at (`_nav_tree.html`).

A served-HTML test reads the attributes; only a browser reads what they do. `tests/e2e/specs/htmx.spec.ts` clicks a row and reads where the pane landed, and points and tabs at a row for its popover. Witnessed by hand in a real Chromium on 2026-08-26, against `mise run gallery --port 9061` — never 8477, which is a live viewer — in both colour schemes:

- Pointing at a row fetched its popover after the delay and drew it on screen at the reading pane's left edge. Pointing at the same row again fetched nothing, so `once` holds
- The pointer moving into the popover left it open: `:hover` follows the DOM and not the layout, so it stays true inside a `position: fixed` descendant of the row
- Clicking inside the popover held it open after the pointer left, and did not navigate. `tabindex="-1"` makes the click focus it and `li.node:focus-within` keeps it up while a reader drags across a number, which is the affordance a pin would have added a second state for
- `Tab` onto a row's link fetched the popover the same way, so the keyboard reaches what the pointer reaches
- The row's link still swapped the pane, and the console stayed empty

# A colour on a bar is judged on the gallery

The context bar paints a track and three bands over it, and the outermost band takes its colour from the row's kind (`src/hyphae/view/static/style.css`). None of it is text, so no contrast ratio settles it. What a test holds is that the edges nest, that a run, a compaction and a maxed row each take a different tip, and that every token the bar spends is defined in both schemes. Which purple, which green, and how far the base band reads from the track are eyeballed.

Witnessed in a real Chromium on 2026-08-28 against `mise run gallery --port 9063` — never 8477, which is a live viewer — in both colour schemes at 1400×900:

- A turn's base band read as a grey stub before its accent tip, and a run's tip read as purple beside it, so a thread is told apart from a turn at a glance
- A compaction drew a short dim head and a long green band: the window it kept, then the window it gave back
- The run whose own thread compacted drew the full width in the alarm, red in light and salmon in dark, and no other row on the page did
- The base band is the quietest thing on the row in both schemes, which is what it is for, and it clears the track it sits on
- The console stayed empty

# A control beside the NavTree lives inside the swapped element

A NavTree row swaps `#nav-tree-rows` out of band and takes `#reading-pane` out of the response, so those two elements are the whole of what a click refreshes. Anything that names the selected node — the preset control does, three links to the node under each preset — has to render inside one of them or it goes stale the moment a reader clicks a row, pointing back at the node they left.

Put it inside `#nav-tree-rows` rather than adding a second out-of-band target: a target costs bytes on every NavTree row, and the row is the one thing on the page multiplied 3,217 times.

# The document does not scroll

The frame is a column exactly the viewport's height: the masthead takes what it needs, and what sits under it takes the rest and scrolls inside itself. Every page but the node browser scrolls `main`. The node page gives its whole content box to `#browser` and hands the scroll down to the two columns, so the NavTree and the reading pane each carry a scrollbar and neither the window nor `main` carries one.

That is why the citation footer of a node page renders inside `#reading-pane`, last, rather than under the document (`node.html`): a footer outside both columns would sit below a fold nobody can reach. It also means a click brings the new node's citations with it, since the swap takes `#reading-pane` out of the response. `test_the_citation_footer_scrolls_with_the_pane_it_cites` pins the containment.

Size a pane against the room its parent gave it — `height: 100%` under a grid row of `minmax(0, 1fr)` — and not against `100vh`, which measures the window and forgets the masthead.

Witnessed in a real Chromium on 2026-08-27 against `mise run gallery --port 9062` — never 8477, which is a live viewer:

- At 1400×500 on a turn page the document overflowed by 0 px, `#browser` ran from the masthead's lower edge to the bottom of the window, and the pane scrolled 255 px to its end, where the footer came into view
- At 1400×260, small enough that both columns overflow: scrolling the NavTree to its end left the pane and the document at 0, and scrolling the pane afterwards left the NavTree where it stood. The preset control stayed pinned directly under the masthead
- At 800×500 the browser fell back to block flow and `main` took the scroll — 439 px of it, which is the page scroll the ≤900px layout keeps, ending on the footer
- The console stayed empty throughout

# The scroller stays outside the swapped element

The NavTree keeps a reader's place across a click for one reason: `#nav-tree` carries the scrollbar and the swap replaces `#nav-tree-rows` inside it. An untouched scroller keeps its `scrollTop`, so nothing in the markup has to ask for it and `hx-preserve` is not needed.

Move `overflow` down onto the rows and every click drops the reader back at the top of the session. No assertion on served HTML would see it, so the structure is pinned instead by `test_the_nav_tree_keeps_its_place_because_the_scroller_is_not_what_swaps`, which reads the served stylesheet, and by the browser leaf in `tests/e2e/specs/htmx.spec.ts` that clicks a row at a viewport where the tree overflows and reads `scrollTop` across the swap.

Witnessed in a real Chromium on 2026-08-20 at a viewport where the NavTree overflows. Clicking a row that is scrolled *out* of view does move the NavTree — the browser scrolls the link into view before focusing it, which is the browser being right. A test script that clicks through a driver's "scroll into view if needed" measures that and not the swap; click a visible row by coordinates.

The driver is the trap and not the language: in the TypeScript runner on 2026-08-28, at 1400×220 on a tool page, `locator.click()` on a row 24 px below the tree's foot scrolled `#nav-tree` 43 px before the click landed.

# The open path clamps at the top of the NavTree

A step of the open path above the selection carries `ancestor` on its `<li>`, and the stylesheet stands each depth one row further down than the depth above it, the first under the preset control. One ancestor per depth, so no two steps clamp at the same place. It is pure CSS and a ladder written out per depth, for the reason the indent ladder is: no rule reads `data-depth` as a number, and `app.CSP` forbids the inline `style` a computed offset would arrive as. `test_the_open_path_clamps_at_the_top_while_the_rows_under_it_scroll` reads the class off the served rows and the ladder off the served stylesheet; what a browser does with the two is a browser's to say.

Witnessed in a real Chromium on 2026-08-28 against `mise run gallery --port 9065` — never 8477, which is a live viewer — in both colour schemes:

- At 1400x170 on a tool call five levels down, where the tree overflows: the session, the bucket, the run, the turn and the api call stood at 67, 87, 106, 125 and 144 px, one row apart under the preset control, while a wheel moved every tool row under them 20 px up and behind them
- They held those places at the end of the scroll, which is the limit the stylesheet accepts: one flat list releases nothing, so a step stays clamped past the end of its own subtree
- Each clamped row painted over the rows passing beneath it, and none reached over the preset control, which carries the `z-index` they are left without
- The console stayed empty

# A row's place on the screen is the script's, not the stylesheet's

`src/hyphae/view/static/nav-tree.js` does the two things a rule cannot: it tops each shown popover at the row it belongs to, and it centres the selected row in the NavTree on load. The popover's left edge stays the stylesheet's — a fixed offset past the tree's width — so a popover never moves sideways, and the script writes `top` alone. Near the foot of the window it clamps, standing the numbers inside the viewport rather than under it.

Place on `mouseover`, `focusin`, `htmx:afterSwap` — htmx fetches the numbers a moment after the pointer lands — and on the tree's own `scroll`, so a popover that is up rides the rows under it.

Witnessed in a real Chromium on 2026-08-28 against `mise run gallery --port 9064` — never 8477, which is a live viewer — in both colour schemes:

- At 1400x220 on a tool page, where the tree overflows: it opened at `scrollTop` 43 with the selected row's centre 5 px off the tree's own. Hovering a row stood the popover 0.1 px under its top; wheeling the tree 43 px up carried the popover along with the row, to the lowest place the window left it
- At 1400x300 a row at 244 px took a popover standing at 129, its foot 7.7 px off the bottom of the window — the clamp, not the row
- At 1000x560 the ⚒ row that spawned a run carried the attribution line under its counts, and the ◎ rows drew model, context, the three charges and their washes, with `over 2 api calls` where the node was more than one call
- The console stayed empty throughout

# A rendered value goes through one macro

Prose a person or a model wrote — a prompt, a run's brief, what a call said — shows as the Markdown it was written in, through `parts.prose`. `src/hyphae/view/render.py` owns the escaping, and no template may hand `|safe` to a value that did not come through it.

A title is the other half of that: one line rather than a block, escaped by `src/hyphae/view/inline_markdown.py` and rendered by `nodes.Node`'s own cuts, never by a template. Those two modules are the only escaping a page has.

Both mounts of one value use that macro: the head a pane previews, and the whole of it the fetch swaps into the same block. A value rendered one way in the preview and another in the fetch is a value a reader cannot tell has a head.

# A cut value goes through the filter that marks it

A string its query cut arrives one character past the width it is printed at, and the filter that prints it cuts it back and marks where the rest was left behind (`src/hyphae/view/format.py:cut`): `line` for a children log's row, `head` and `member` for a header, `short` and `item` for a row of the session list. Print such a value bare and a reader cannot tell a name that ended from one that was stopped. A title arrives marked already, at whichever of the four widths `src/hyphae/view/nodes.py` cut it to.

A closed vocabulary is the one thing cut without a mark: a taxonomy value is bound at `queries.TAG_CHARS` because a page whose size is arithmetic needs every width named, not because any member reaches it (`src/hyphae/enrich/taxonomy.py`). `_parts.html:counted` takes `mark=false` for those, and a mark there would claim a name went on when nothing was left behind.

A mark is three bytes on every row of the page that carries it, so adding one to a column of the session list moves `bounds.SESSIONS` — the ceiling is derived from the dearest row, and the pin in `tests/view/test_bounds.py` holds it from both sides against what `tests/view/budgets.py` measured that row at.

# Label every value a test reads

A rendered value goes in `<span data-field="{{ name }}">`, and a repeated thing gets a `data-` key naming it (`data-nav-tree`, `data-child`, `data-crumb`, `data-walk`). Tests read the viewer through those attributes, so prose is free to change and the units and marks stay outside the labelled span — a `data-field` carries a value and nothing around it: the value the store holds, or — where the field is a title — the one derivation that composes it. A kind mark is the exception that proves it: it carries no key, and `tests/view/conftest.py:icons` reads it by class.

# What a dev page does when the files change under it

`hp view --dev` puts `src/hyphae/view/static/dev-reload.js` on every page and serves `/dev/reload`, which sends one message per debounced save under the templates and the static files (`src/hyphae/view/dev.py`). A stylesheet-only save swaps the sheets in place; anything else reloads the page; and the client reloads on a reconnect too, so a restarted server is what the open page is reading. Nothing else on the page triggers either.

Witnessed in a real Chromium on 2026-08-25, over a store built from the `resume_pair` and `spine` fixtures on port 8491, and again on 2026-08-26 against `mise run gallery` on 8478 — never 8477, which is a live viewer. In the second run, on an open node page scrolled down the pane:

- Saving `style.css` re-fetched both stylesheets at a fresh URL inside 0.2s, with no document request: the colour changed and the scroll position held
- Saving `base.html` reloaded the page whole inside 0.2s with the new markup on it, and reverting the file reloaded it back
- Stopping the gallery and starting it again reloaded the page once, 3.1s after the new server answered, at the URL it was on. The exit logged `Cancel 1 running task(s)`, which is `DEV_SHUTDOWN_SECONDS` hanging up on the open stream rather than waiting on it forever
- The console carried nothing but the dropped stream the restart caused: the loop runs under the same `default-src 'self'` as the pages it reloads

# The gallery is the scenario list, opened

`mise run gallery` (`tests/gallery/serve.py`) builds a store from the redacted fixtures, serves it under `--dev`, and lists `tests/view/scenarios.py:SCENARIOS` at `/gallery`. One link per entry and no others, so the page a person walks is the list the tier sweeps.

Witnessed in a real Chromium on 2026-08-25 on the gallery's own port 8478 — never 8477, which is a live viewer. The index came up with 35 rows, one per `SCENARIOS` entry; clicking the turn-node link landed on that node's page with its NavTree (17 rows) and its pane rendered, the reload script on it, and no console error.

A browser check of any page here cannot use Playwright's `wait_for_function`: it evaluates a string as script, and the CSP refuses that. Wait on a selector instead. That is the Python harness's rule and not the header's: on 2026-08-28 the TypeScript runner's `waitForFunction` resolved against the same `default-src 'self'`, in both its function and string forms, with the console empty. `tests/e2e` still waits on selectors, because a selector says what it is waiting for.

Two more traps in that harness. Playwright's sync API delivers page events only while the main thread is inside a Playwright call, so a wait loop built out of `time.sleep` sees nothing and reads as "the page never reloaded" — poll `page.title()` in the loop. And never `git checkout` a file you are editing to strip debug lines from it: the checkout took a whole uncommitted client rewrite with it, and the next hour measured the old file.
