"""How one value prints: the widths it is cut to, the words it is named in, and its markup.

Each module here answers a question about one value and nothing about the page around it —
what a duration reads as, where a string was stopped, what a tool call is called, which
characters of a prompt are markdown. That is what makes this the leaf of the package: `text/`
imports `bounds`, because a cut is a size, and nothing else `view/` holds
(`tests/view/test_layout.py`).
"""
