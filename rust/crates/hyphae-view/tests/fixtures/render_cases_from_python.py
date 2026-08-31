"""Write the escaping cases the Rust renderers are checked against.

The viewer's two escape paths are the one place a mistake is live on every page at once, so
the expected side comes from the Python that already serves them rather than from what a
reader of `render.py` believes it does. Regenerate after either module changes:

    mise x -- python rust/crates/hyphae-view/tests/fixtures/render_cases_from_python.py \
        > rust/crates/hyphae-view/tests/fixtures/render_cases.json

The inputs are written rather than lifted from the corpus on purpose: what has to hold is
what a *hostile* title does, and no recorded session wrote one.
"""

import html
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "src"))

from hyphae.view import highlight, inline_markdown, render  # noqa: E402

TITLES = [
    "plain title",
    "**bold** and *em* and `code`",
    "<script>alert(1)</script>",
    "a \"quoted\" & <tagged> 'title'",
    "[link](https://example.com/x)",
    "[bad](javascript:alert(1))",
    "![alt text](https://host/px?d=1)",
    "![](https://host/px)",
    "# not a heading\n- not a list",
    "a\nb",
    "line one\n\nline two",
    "<https://example.com>",
    "*.tmp and handoff_2 stay",
    "trailing **open",
    "trailing `open",
    "trailing [open](http://ex",
    "emoji 🌱 title",
    "&amp; entity",
    "back\\*slash escape",
    "  leading and trailing spaces  ",
    "tabs\tinside",
    # An inline HTML attribute is a rule of its own: block passthrough being off does not
    # settle it, and an `onerror` handler is what rides in if it is on.
    "a paragraph with <img src=x onerror=alert(1)> in it",
    # A fence is a block, and no block element belongs in a line a NavTree row prints.
    "```\nfenced\n```",
]
cases = [
    {
        "text": text,
        "render_links": str(inline_markdown.render(text, links=True)),
        "render_plain": str(inline_markdown.render(text, links=False)),
        "cut_20": str(inline_markdown.cut(text, 20, links=False, source_cap=10_000)),
        "cut_capped": str(inline_markdown.cut(text, 200, links=False, source_cap=8)),
        "strip": inline_markdown.strip(text),
    }
    for text in TITLES
]

BLOCKS = [
    "# Heading\n\nA paragraph with **bold**.",
    "<script>alert(1)</script>\n\nafter",
    "![alt](https://host/px?d=1)",
    "[js](javascript:alert(1)) and [ok](https://example.com)",
    "```python\nprint('hi')\n```",
    "```\nplain <fence>\n```",
    "- one\n- two",
    'quote "and" ampersand & <tag>',
    "a paragraph with <img src=x onerror=alert(1)> in it",
    # Linkify off: text that looks like a URL is text.
    "see https://evil.test/path for details",
    # Both fence arms escape in different places — a lexed block by the highlighter, an
    # unlexed one by the renderer — so the markup inside each is pinned separately.
    '```json\n{"a": "<img src=x onerror=y>"}\n```',
    '```html\n{"a": "<img src=x onerror=y>"}\n```',
    # A fence naming a language with no lexer here is still a block.
    "```rust\nx = 1\n```",
    # One fence per syntax the viewer marks up, so what the Rust port excuses is bounded by
    # syntax rather than by a count: a highlighter that lost a language would still pass a
    # count. The short spellings, because those are what a model types.
    "```sql\nSELECT a FROM t WHERE b = '<x>' -- note\n```",
    "```sh\necho '<x>' && ls -1\n```",
    "```md\n# Heading\n\n- an item with `code`\n```",
]


def _shown(markup: str) -> str:
    """What a browser shows of a run of markup: the tags dropped, the escapes undone."""
    return html.unescape(re.sub(r"<[^>]*>", "", markup))


def _block(text: str) -> dict[str, str | None]:
    """One block case, with the highlighter's arm named where it took one.

    A fence whose language this viewer has a lexer for is the one place the two ports part. Both
    mark the block up; Pygments and syntect draw the token boundaries differently, so the markup
    inside the `<pre>` is not comparable byte for byte and `syntax` is what excuses it. What still
    is comparable travels with the case: the wall the `<pre>` opens with, which says which
    highlighter arm ran, and the characters a reader sees inside it — which for JSON is the
    re-laid-out value rather than the stored one, so the check is not free.
    """
    rendered = str(render.markdown(text))
    syntax = highlight.by_fence(text.split("\n", 1)[0].removeprefix("```"))
    if syntax is None:
        return {"text": text, "html": rendered, "syntax": None, "wall": None, "shown": None}
    wall, _, rest = rendered.partition(">")
    return {
        "text": text,
        "html": rendered,
        "syntax": str(syntax),
        "wall": wall + ">",
        "shown": _shown(rest.removesuffix("</pre>\n")),
    }


blocks = [_block(t) for t in BLOCKS]

LINKS = [
    "https://github.com/x/y/pull/1",
    "http://example.com",
    "javascript:alert(1)",
    "https://example.com/very/long…",
    "",
    # The scheme is read case-insensitively, so a shouted one still links...
    "HTTP://example.test/",
    # ...and every scheme that runs or reads something local never reaches an `href`.
    "data:text/html,<script>alert(1)</script>",
    "file:///etc",
    # A quote in a URL cannot close the attribute it lands in.
    'https://example.test/?q="',
]
links = [{"url": u, "html": str(render.link(u))} for u in LINKS]

print(json.dumps({"titles": cases, "blocks": blocks, "links": links}, indent=2, ensure_ascii=False))
