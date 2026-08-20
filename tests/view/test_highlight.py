"""What `view/highlight.py` hands a template: marked-up code, and plain text where it must be.

The unit rather than a page, because two of the three arms are unreachable through the fixture
corpus: no recorded value nests deep enough to blow the indent budget, and none is a quarter of
a million characters long. Both are invented here and labelled as such.

Escaping is the load-bearing part. A tool's arguments are a string a model wrote, so a `<img
onerror=…>` inside one has to arrive at the browser as text whichever arm rendered it.
"""

import json

from aiobserve.analyze import queries
from aiobserve.view import bounds
from aiobserve.view.highlight import Syntax, lit
from tests.view.conftest import plain

# One tool argument in the shape a recorded one has — a path and a pattern — with markup put
# inside it. Invented: redaction flattens the recorded strings, so no fixture carries a `<`.
HOSTILE = '{"pattern": "</script><img src=x onerror=y>", "path": "/tmp/a.py"}'


def test_a_value_is_marked_up_by_class_and_never_by_style() -> None:
    """JSON comes back as classed spans, which is what the CSP leaves room for.

    An inline `style` attribute would be blocked by `app.CSP` and the value would render
    unstyled; a class is painted by `static/pygments.css`, which is served from this app.
    """
    shown = lit(HOSTILE, Syntax.JSON)
    assert shown.syntax is Syntax.JSON
    assert shown.over == 0
    # The key, the string and the punctuation each carry a class of their own...
    assert '<span class="nt">' in shown.html
    assert '<span class="s2">' in shown.html
    # ...and nothing carries a color the policy would refuse.
    assert "style=" not in shown.html


def test_the_markup_inside_a_value_arrives_as_text() -> None:
    """A tool argument holding markup is readable on the page and inert in it."""
    shown = lit(HOSTILE, Syntax.JSON)
    assert "&lt;/script&gt;&lt;img src=x onerror=y&gt;" in shown.html
    assert "<img" not in shown.html
    assert "</script>" not in shown.html
    # Indented for reading, which is what makes a recorded argument scannable at all.
    assert "\n  " in shown.html


def test_a_value_that_is_not_json_is_shown_as_it_was_stored() -> None:
    """Not every stored value parses — a tool's plain-text output is shown, not swallowed.

    Marked up as JSON it would be a line of error tokens, so the arm is plain and escaped.
    """
    shown = lit("Traceback: <module> failed\n  at line 3", Syntax.JSON)
    assert shown.syntax is None
    assert shown.over == 0
    assert "&lt;module&gt; failed" in shown.html
    assert "at line 3" in shown.html


def test_a_deeply_nested_value_is_shown_at_the_size_it_was_stored() -> None:
    """A value nested past what anyone reads costs its own length to serve, not more.

    Indenting is quadratic in nesting, so these two invented values — and they have to be
    invented; nothing recorded nests near this deep — are the whole risk in one line. The
    first parses and would indent to 50 MB; the second overflows the parser's own stack.
    """
    for depth in (5_000, 10_000):
        value = "[" * depth + "]" * depth
        shown = lit(value, Syntax.JSON)
        # Nothing was added — no newline, no indentation, and every character still there.
        assert "\n" not in shown.html
        assert plain(shown.html) == value
    # Unindented is not unmarked: a value that parses is still JSON, and the page still classes
    # it. Only the value the parser itself refused comes back as plain text.
    assert lit("[" * 5_000 + "]" * 5_000, Syntax.JSON).syntax is Syntax.JSON
    assert lit("[" * 10_000 + "]" * 10_000, Syntax.JSON).syntax is None
    # Nothing beside the nesting excuses it. The walk is a stack, so the *last* member is the
    # first thing it reaches — and whether that member is an empty container or a number, the
    # measuring has to step over it and carry on to the deep one behind it.
    deep = json.loads("[" * 3_000 + "]" * 3_000)
    for neighbour in ([], 1):
        beside = json.dumps({"deep": deep, "beside": neighbour})
        assert "\n" not in lit(beside, Syntax.JSON).html, neighbour
    # ...while a value that nests as deep as a real record does is still indented.
    assert "\n    " in lit('{"a": {"b": {"c": [1, 2]}}}', Syntax.JSON).html


def test_whitespace_is_written_bare_rather_than_wrapped_in_a_span_of_its_own() -> None:
    """The one thing this viewer changes about Pygments' markup, and it is worth 10 KB.

    Pygments classes every run of whitespace `w`, and `static/pygments.css` paints none of
    them. On the widest query the repo ships that is 10 KB of markup for nothing, so the
    lexers carry a filter that re-types whitespace as text — which the formatter writes bare.
    Read on SQL, which is indented enough for the spans to be most of it.
    """
    shown = lit(queries.load("view_sessions"), Syntax.SQL)
    assert '<span class="k">' in shown.html, "the tokens that are painted are still classed"
    assert 'class="w"' not in shown.html


def test_a_value_past_the_ceiling_is_printed_as_stored_and_says_how_long_it_is() -> None:
    """The ceiling is a line, not a slope: one character over and the markup stops.

    A JSON string is the value either side, because indenting one changes nothing about its
    length — so the two cases differ in the one character the ceiling is about. Invented:
    the largest recorded tool result is far shorter than a quarter of a million characters.
    """
    at = json.dumps("x" * (bounds.HIGHLIGHT_CHARS - 2))
    assert len(at) == bounds.HIGHLIGHT_CHARS
    assert lit(at, Syntax.JSON).syntax is Syntax.JSON
    over = json.dumps("x" * (bounds.HIGHLIGHT_CHARS - 1))
    shown = lit(over, Syntax.JSON)
    assert shown.syntax is None
    # It says its own length rather than that it was cut: the whole value is still served.
    assert shown.over == len(over)
    assert len(shown.html) >= len(over)


def test_the_ceiling_counts_characters_rather_than_bytes() -> None:
    """A multibyte value under the ceiling is marked up though its bytes run past it.

    The deliberate deviation (`plans/viewer-node-browser/design.md`): what the ceiling guards
    is the tokenizer's work and the markup a span per token adds, and both follow the tokens.
    Invented, for the same reason as the leaf above.
    """
    value = json.dumps("é" * (bounds.HIGHLIGHT_CHARS - 2), ensure_ascii=False)
    assert len(value) == bounds.HIGHLIGHT_CHARS
    assert len(value.encode()) > bounds.HIGHLIGHT_CHARS
    assert lit(value, Syntax.JSON).syntax is Syntax.JSON


def test_an_absent_value_renders_to_nothing() -> None:
    """A NULL column reaches the template as None, and an empty block beats a crash."""
    assert lit(None, Syntax.JSON) == lit("", Syntax.SQL) == ("", None, 0)


def test_sql_is_marked_up_whole_and_loses_nothing() -> None:
    """A query file comes back as the same characters, spans and all — SQL is not reformatted.

    The value the `/query` page serves is a file this repo ships, so the strong check is
    available here and nowhere else: every character of it survives the markup.
    """
    sql = queries.load("view_sessions")
    shown = lit(sql, Syntax.SQL)
    assert shown.syntax is Syntax.SQL
    assert plain(shown.html) == sql
