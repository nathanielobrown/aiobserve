---
description: Viewer UI conventions
paths:
  - "src/aiobserve/view/templates/**/*.html"
  - "src/aiobserve/view/static/*.css"
  - "src/aiobserve/view/static/*.js"
---

# Viewer UI

The viewer is server-rendered Jinja with two scripts: vendored htmx, and `view/static/tree-width.js` for the one thing a reader sets that no URL carries. These are the conventions a template has to hold to; what each page shows is in `docs/viewer.md`.

# One body, two mounts

A node's body is one macro in `_node_body.html`, mounted twice:

- Its **full view**, `node.html`, wraps the body with the tree, the crumbs above it, its enrichment, its previewed values, its children log, and prev/next
- Its **expansion**, `fragments/body.html`, is the body alone — opened in a log row while the reader stays on the parent

Render a node's facts in the body macro and nowhere else. A pane and a tree row that disagree tell a reader two stories about one node.

An expansion stops one level down. An api call's lists the tools it called, through the same log macro the page renders; every other kind stands a count and a link to its own page. No row inside an expansion opens another: an accordion of accordions is a page, and the node already has one.

Every node reachable in the pane has a URL that renders the whole page cold. Nothing may render only as a fragment.

# A node's title comes off the node

Print `Node.tree_title`, `log_title` or `pane_title`, whichever fits the surface. Never join words in a template and never print a row's own column where a node is being named: the three are one title at three widths (`view/nodes.py`), and a surface composing its own would be a second answer to what the node is called. What each kind is titled is in `docs/viewer.md`.

# Tooltips are native `title` attributes

`title="…"` on the element, and nothing else — no first-party script, and `app.CSP` allows no inline `style`. It costs no bytes on a page that doesn't hover and no code at all.

A `title` is worth its bytes where the mark on screen is smaller than what it means: the enrichment glyph, and the `*` a cost carries when our price table priced none of some calls under it. Text a reader can already read doesn't get one.

# The glyph is bare in the tree, spelled out in the pane

`✨` marks a title a model helped write and leads the whole of it, halves the session wrote included (`docs/viewer.md`). Write it through `parts.glyph(node)`, which reads the `GLYPH` and `GLYPH_CLASS` globals from `view/enrichment.py`; don't type the character into a template.

Where a title repeats — a tree row, a crumb, a log row, a walk control — the glyph goes bare. The pane carries the one that says what the mark means: `parts.summary` hangs `Described.provenance` off it as a `title`, naming the model, when it ran, the prompt and taxonomy versions, and whether the row is stale. A `title` on every repeat would be the same sentence 400 times in one page's markup.

Every mark saying what a thing *is* — the kind of node a surface names, and the kind a children log's column is about — goes through `parts.mark(character)`, whose character comes from `nodes.GLYPHS` or a `Column` — the one place those characters are written, so a mark cannot mean one thing in a table and another in the tree. It is `aria-hidden` and carries no `title`: the word it stands for is already in the markup beside it (`docs/viewer.md`). The `<title>` element is the one place a mark goes in bare, because it is text and has no markup to hide it in.

# A tree row is priced, not budgeted

`bounds.TREE_ROW_BYTES` is measured through the app, pinned with no slack, and spent 3,217 times on the worst page. An attribute added to `_tree.html` is that many bytes of page, so re-measure with `tests/view/test_bounds.py` rather than guessing — the pin fails first, which is the point.

Link where you fetch: a row's `href` and its `hx-get` are the same URL, and both carry the page's knob suffix, so a click, a paste, and a bookmark serve the same bytes. The mount an expansion opens through carries it too, so the fragment's own links come back under the fold the reader was in.

# A pane swap says where it lands

A link that moves the reader without leaving the page carries six attributes, and all six have to be in effect on it: `hx-get` the node's URL, `hx-select="#pane"`, `hx-target="#pane"`, `hx-swap="outerHTML"`, `hx-select-oob="#tree-rows"`, and `hx-push-url="true"`. The two that are easy to leave off are the two with defaults that look harmless: htmx aims at the clicked element, so a link without `hx-target` swaps the whole pane inside the `<a>` and leaves the pane showing the node the reader came from — the URL changes and the page does not. `hx-select` hands back the `#pane` element itself rather than its contents, which is why the swap is `outerHTML` and not the default.

htmx reads all but `hx-get` off the closest ancestor carrying one, so the tree writes the five shared ones on `#tree-rows` and its rows carry only the URL. A children log writes them out per row instead: the body toggle beside each link is an `hx-get` that must not swap the pane, so a hoisted attribute would have to be undone on it — a line per row either way. `test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane` reads both mounts the way htmx resolves them, inheritance and all.

# A control beside the tree lives inside the swapped element

A tree row swaps `#tree-rows` out of band and takes `#pane` out of the response, so those two
elements are the whole of what a click refreshes. Anything that names the selected node — the
preset switcher does, three links to the node under each fold — has to render inside one of
them or it goes stale the moment a reader clicks a row, pointing back at the node they left.

Put it inside `#tree-rows` rather than adding a second out-of-band target: a target costs bytes
on every tree row, and the row is the one thing on the page multiplied 3,217 times.

# The scroller stays outside the swapped element

The tree keeps a reader's place across a click for one reason: `#tree` carries the scrollbar and the swap replaces `#tree-rows` inside it. An untouched scroller keeps its `scrollTop`, so nothing in the markup has to ask for it and `hx-preserve` is not needed.

Move `overflow` down onto the rows and every click drops the reader back at the top of the session. No assertion on served HTML would see it, so the structure is pinned instead by `test_the_tree_keeps_its_place_because_the_scroller_is_not_what_swaps`, which reads the served stylesheet.

Witnessed in a real Chromium on 2026-08-20 at a viewport where the tree overflows. Clicking a row that is scrolled *out* of view does move the tree — the browser scrolls the link into view before focusing it, which is the browser being right. A test script that clicks through a driver's "scroll into view if needed" measures that and not the swap; click a visible row by coordinates.

# A rendered value goes through one macro

Prose a person or a model wrote — a prompt, a run's brief, what a call said — shows as the Markdown it was written in, through `parts.prose`. `view/render.py` owns the escaping, and no template may hand `|safe` to a value that did not come through it.

Both mounts of one value use that macro: the head a pane previews, and the whole of it the fetch swaps into the same block. A value rendered one way in the preview and another in the fetch is a value a reader cannot tell has a head.

# A cut value goes through the filter that marks it

A string its query cut arrives one character past the width it is printed at, and the filter that prints it cuts it back and marks where the rest was left behind (`view/format.py:cut`): `line` for a children log's row, `head` and `member` for a header, `said` for the line an enrichment pass wrote, `short` for that line on a row of the session list. Print such a value bare and a reader cannot tell a name that ended from one that was stopped. A title arrives marked already, at whichever of the three widths `view/nodes.py` cut it to.

The session list's other strings are the exception: a row there is multiplied by the size of the page, so its title, path and lists are cut where they stand and only the pass's line pays for a mark. Marking them is a ceiling to re-measure, not a byte to spend (`tests/view/test_bounds.py`).

# Label every value a test reads

A rendered value goes in `<span data-field="{{ name }}">`, and a repeated thing gets a `data-` key naming it (`data-tree`, `data-child`, `data-crumb`, `data-walk`). Tests read the viewer through those attributes, so prose is free to change and the units and marks stay outside the labelled span — a `data-field` carries a value and nothing around it: the value the store holds, or — where the field is a title — the one derivation that composes it. A kind mark is the exception that proves it: it carries no key, and `tests/view/conftest.py:icons` reads it by class.
