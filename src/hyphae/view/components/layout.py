"""The frame every page is served in, and the three slots a page fills."""

import htpy

from hyphae.view.components import Html

# The dev reload client, which `hp view --dev` alone puts on a page: it listens on
# `/dev/reload` and reloads when a stylesheet or a component is saved (`view/dev.py`). Built
# once because a prod page is this page with that one element taken out, which is how
# `tests/view/test_dev.py` reads both at once.
_DEV_SCRIPT = htpy.script(src="/static/dev-reload.js", defer=True)


def page(
    *,
    tab_title: str,
    scripts: Html | None,
    main: Html,
    footer: Html | None,
    dev: bool,
) -> Html:
    """One whole document: `tab_title` in the tab, `main` under the masthead, `footer` last.

    `scripts` is what a page needs beyond htmx — only the node page has any. `footer` is the
    citation frame, which the node page leaves `None` and stands inside its reading pane
    instead, because that page's scrollers are its two columns and a document footer under them
    would never come into view.
    """
    return htpy.html(lang="en")[
        [
            htpy.head[
                [
                    htpy.meta(charset="utf-8"),
                    htpy.meta(name="viewport", content="width=device-width, initial-scale=1"),
                    htpy.title[tab_title],
                    htpy.link(rel="stylesheet", href="/static/style.css"),
                    # What paints the classes `view/text/highlight.py` writes. Its own file rather
                    # than a block of `style.css`, because the classes are Pygments' vocabulary
                    # and not this viewer's.
                    htpy.link(rel="stylesheet", href="/static/pygments.css"),
                    # htmx writes a style element for its indicator class as it loads, which
                    # `app.CSP` blocks and the browser reports as an error on every page.
                    # Nothing here wears that class, so the styles are turned off rather than
                    # allowed: a hash in the policy would pin this htmx build, and a nonce would
                    # open the door for the transcript text every page renders.
                    htpy.meta(name="htmx-config", content='{"includeIndicatorStyles": false}'),
                    # htmx, vendored — the version is in the filename because that is where an
                    # upgrade has to be seen. Fetched from
                    # https://unpkg.com/htmx.org@2.0.6/dist/htmx.min.js, sha256
                    # b6768eed4f3af85b73a75054701bd60e17cac718aef2b7f6b254e5e0e2045616. No CDN:
                    # the viewer reads private transcripts on a laptop that may be offline, and
                    # the CSP that keeps a transcript from calling out would refuse a remote
                    # script anyway.
                    htpy.script(src="/static/htmx-2.0.6.min.js", defer=True),
                    scripts,
                    _DEV_SCRIPT if dev else None,
                ]
            ],
            htpy.body[
                [
                    htpy.nav(id="masthead")[htpy.a(href="/")["hyphae"]],
                    htpy.main[main],
                    footer,
                ]
            ],
        ]
    ]
