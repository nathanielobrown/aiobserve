# The UI development loop

Edit a viewer template or stylesheet and see it in the browser without touching the browser. Two things make that a loop: `mise run gallery`, which serves every scenario the viewer tier pins, and `hp view --dev`, which reloads the open page when you save. What each page shows is in [the viewer guide](viewer.md); the conventions a template has to hold to are in `.claude/rules/viewer-ui.md`.

## Open the scenario you are about to change

```bash
mise run gallery
```

The gallery builds a store from the redacted fixtures, serves it in dev mode, and prints its index — `/gallery` on port 8478, one past the viewer's own default, so a gallery and a viewer over your own store can be open side by side. The index is a row per entry of `tests/view/scenarios.py:ROUTES`: the route a page stands for, and one real URL that renders it. Click the one you are working on.

`mise run gallery --port 9001` moves it, which is how a second branch's gallery opens beside the first. That flag is the only argument it takes, and neither a path nor an environment variable can reach it: session data is private, and what keeps this tool from serving the canonical store is that the process can only build its own corpus (`tests/gallery/serve.py`). That build costs well under a second, so it happens on every launch and nothing is cached.

## Save the file and watch the page

Jinja re-renders an edited template on the next request, so all the loop adds is the request. Save a template and the open page reloads; save a stylesheet and the page swaps its sheets in place, keeping the scroll and everything else a reload would cost; restart the server and the page reloads once, on the reconnect, onto whatever the new server serves. `.claude/rules/viewer-ui.md` records what a real Chromium did with each of the three.

A reload costs a reader nothing here because every state but tree width rides the URL: the page comes back at the node, the view and the knobs it was on. That is why the loop needs no hot module replacement and no DOM morphing.

Python edits are the exception. Nothing watches them — restart the gallery or the viewer by hand, and the open page will follow.

## A formatter owns the layout

```bash
mise run format-html
```

djLint writes every template's indentation and attribute layout, and `[tool.djlint]` in `pyproject.toml` decides how. `mise run check-fast` formats them for you and `mise run check` fails on a file that is not formatted. VS Code formats on save through that same binary and that same block, so an editor's output is the check's output; `.vscode/settings.json` carries only what that parity needs.

Two things to know before you edit a template:

- **The whitespace it writes is bytes on the page.** Jinja renders the newline and the indent the formatter puts between a row's cells, and a tree row is spent 3,217 times on the worst page (`.claude/rules/viewer-ui.md`). A space a reader has to see is written `{{ " " }}`, because a literal one sits where djLint reflows (`src/hyphae/view/templates/_parts.html`)
- **Never write a raw tag inside a `{# … #}` comment.** djLint reads the opening tag as the real thing and leaves the rest of the file unindented. Name the element in words instead (`src/hyphae/view/templates/base.html`)

## Run the same loop over your own store

```bash
uv run hp view --dev
```

`--dev` mounts the reload stream and puts its client on every page, and changes nothing else: a shipped page is a dev page minus one script tag. The watcher's dependency lives in the dev group, so an installed viewer never carries it and `--dev` in a checkout without it fails at startup rather than serving a loop that never fires. Run `mise run sync` if it does.

## Add a route and the gallery gains the page

`ROUTES` has two readers: the viewer tier, which sweeps every URL in it and checks the keys against the routes the app declares, and the gallery, which lists it. A route added with no entry fails `tests/view/test_bounds.py`, and the entry that clears it is the page you can then open in the gallery. Neither side keeps a list of its own to drift.
