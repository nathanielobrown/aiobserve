"""Write the escaping cases the Rust renderers are checked against.

The viewer's two escape paths are the one place a mistake is live on every page at once, so
the expected side comes from the Python that already serves them rather than from what a
reader of `render.py` believes it does. Regenerate after either module changes:

    mise x -- python rust/crates/hyphae-view/tests/fixtures/render_cases_from_python.py \
        > rust/crates/hyphae-view/tests/fixtures/render_cases.json

The inputs are written rather than lifted from the corpus on purpose: what has to hold is
what a *hostile* title does, and no recorded session wrote one.
"""

import json
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
]
# A fence whose language the viewer has a lexer for takes the highlighter's path, which the
# Rust port defers. The flag lets the leaf hold the rest to parity and say what it excused.
blocks = [
    {
        "text": t,
        "html": str(render.markdown(t)),
        "highlighted": bool(highlight.by_fence(t.split("\n", 1)[0].removeprefix("```"))),
    }
    for t in BLOCKS
]

LINKS = [
    "https://github.com/x/y/pull/1",
    "http://example.com",
    "javascript:alert(1)",
    "https://example.com/very/long…",
    "",
]
links = [{"url": u, "html": str(render.link(u))} for u in LINKS]

print(json.dumps({"titles": cases, "blocks": blocks, "links": links}, indent=2, ensure_ascii=False))
