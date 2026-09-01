"""Each small part rendered on its own: the spaces it owes, and where markup may go.

The level the conversion to htpy creates. A part is a function now, so it can be called with
the view-model it takes and read straight — no app, no store, no route. What every page built
out of these finally serves is the rest of this tier's business.

The centre of gravity is **spaces**. Jinja emitted the whitespace a template was written with;
htpy emits none between elements, so every space a reader sees is a `" "` child somebody wrote
on purpose. A leaf here reads the rendered text back through `conftest.plain` and asserts the
gap, because `0 errors` and `0errors` are the same string to every `data-*` reader in the suite.
"""

import datetime as dt
import re

import htpy
import pytest

from hyphae.analyze import queries
from hyphae.enrich.items import Level
from hyphae.enrich.levels import LEVELS
from hyphae.view.citation import cited
from hyphae.view.components import citation, parts
from hyphae.view.detail import Detail, EnrichmentLines
from hyphae.view.enrichment import GLYPH, TAXONOMY_VERSION, Enrichment
from hyphae.view.text.highlight import Syntax
from tests.conftest import SPINE
from tests.view.conftest import block, classed, plain, prose, values, walled


@pytest.fixture
def described() -> Enrichment:
    """One enrichment as a pane reads it, current on both versions this build writes."""
    return Enrichment(
        level=Level.turn,
        item_id="turn-7",
        description="Read the extractor and fixed the offload path.",
        description_chars=46,
        category="implementation",
        outcome="completed",
        friction=None,
        friction_chars=None,
        model="claude-opus-4",
        enriched_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
        prompt_version=LEVELS[Level.turn].prompt_version,
        taxonomy_version=TAXONOMY_VERSION,
    )


def line(name: str, head: str, cut: int, *, markdown: bool = False) -> Detail:
    """One fat value as a pane previews it, with `cut` characters left behind it."""
    return Detail(name, head, cut, f"/fragment/{name}", None, markdown)


# --- The spaces htpy stopped emitting -----------------------------------------------------


def test_a_stacked_cell_holds_one_space_between_its_secondary_and_the_unit_word() -> None:
    """The gap Jinja wrote as an expression, because a formatter could have dropped a literal.

    The unit sits outside the labelled span — a `data-field` carries the value the store holds
    and nothing else — so without a `" "` child of its own the page reads `3errors`.
    """
    served = str(
        parts.stacked(
            field="error_rate",
            primary="4%",
            secondary_field="tool_errors",
            secondary="3",
            unit="errors",
            primary_mark=None,
            secondary_mark=None,
        )
    )
    # The unit is outside the labelled span, and one space stands between the two...
    assert "</span> errors</span>" in served
    assert "3 errors" in plain(served)


def test_a_stacked_cell_with_no_unit_ends_at_its_number() -> None:
    """The converse: a cell whose secondary needs no word owes no space either.

    The space rides inside the test rather than beside it, so the two `when` columns — which
    print a timestamp under a duration and name no unit — do not end in a stray gap.
    """
    served = str(
        parts.stacked(
            field="ago",
            primary="2 days ago",
            secondary_field="last_active",
            secondary="2026-03-01 09:00",
            unit=None,
            primary_mark=None,
            secondary_mark=None,
        )
    )
    assert served.endswith("09:00</span></span>")


def test_a_glyph_carries_the_space_after_it_and_a_plain_title_carries_none() -> None:
    """The mark on a model-written title, and the gap between it and the title it marks.

    Both halves matter: the space is inside the test, so a row for something no pass described
    renders nothing at all rather than a lone space the NavTree would pay 3,217 times for.
    """
    assert str(parts.glyph(enriched=True)) == f'<span class="glyph">{GLYPH}</span> '
    assert parts.glyph(enriched=False) is None


def test_a_counted_list_spaces_each_count_off_its_name_and_commas_the_rest() -> None:
    """`Task ×3, Explore ×1` — the spacing Jinja wrote across two source lines.

    Read as text rather than as markup: `counted` writes no elements of its own, so a lost
    space here is invisible to everything else in the suite.
    """
    entries = [parts.Count("Task", 3), parts.Count("Explore", 1)]
    assert plain(str(parts.counted(entries=entries, mark_cuts=True))) == "Task ×3, Explore ×1"


def test_the_tail_of_a_cut_list_opens_with_a_space() -> None:
    """`and 4 more` follows a list on the same line, so the gap belongs to the tail."""
    assert plain(str(parts.more(cut=4))) == " and 4 more"
    # And a list the query did not cut says nothing, rather than saying it left out none.
    assert parts.more(cut=0) is None


# --- The opt-outs, which are the reason these take a flag ----------------------------------


def test_a_counted_list_of_closed_vocabulary_marks_no_name_it_prints() -> None:
    """A taxonomy value is cut at a width its own words cannot reach, so a mark would lie.

    The name is passed at its full length either way; what changes is whether the cut mark
    `fmt.cut` writes can appear. Proven with a name past the cut, so the two arms differ.
    """
    long = parts.Count("a" * (queries.LIST_ITEM_CHARS + 10), 2)
    marked = plain(str(parts.counted(entries=[long], mark_cuts=True)))
    whole = plain(str(parts.counted(entries=[long], mark_cuts=False)))
    # The marked arm stopped the name and said so; the closed-vocabulary arm printed it whole.
    assert len(marked) < len(whole)
    assert whole.startswith("a" * (queries.LIST_ITEM_CHARS + 10))


def test_a_fact_prints_the_dash_the_viewer_prints_for_a_column_the_store_left_null() -> None:
    """A header names its fields whether or not the session filled them."""
    served = str(parts.fact(name="git_branch", value=None, cut=True))
    assert '<dd data-field="git_branch">—</dd>' in served


def test_a_fact_reads_its_label_off_its_value_with_a_space_between_them() -> None:
    """`Cost $1.48`, never `Cost$1.48` — the one gap the stylesheet is not the only thing holding.

    A `<dt>` and the `<dd>` beside it are two elements, so htpy writes nothing between them and
    a reader whose stylesheet never arrived meets the label welded to the number
    (`tests/view/test_app__headers.py` reads the same gap off a served header).
    """
    assert plain(str(parts.fact(name="cost_usd", value="$1.48", cut=True))) == "Cost $1.48"


def test_a_fact_whose_value_is_composed_carries_the_markup_the_caller_built() -> None:
    """The mount for a value no formatter makes: a list, and the count of what its query cut.

    The `<dl>` shape is the same one `fact` writes — one place decides what a labelled fact
    looks like — and what changes is that the caller hands markup rather than a string. What a
    body composes through it is `test_node_body.py`'s business.
    """
    served = str(parts.labelled(name="skills", value=htpy.span["commit, pr"]))
    assert plain(served) == "Skills commit, pr"
    assert '<dd data-field="skills"><span>commit, pr</span></dd>' in served


def test_a_fact_that_opts_out_of_the_cut_keeps_the_count_of_what_its_query_left() -> None:
    """A joined list is already bounded by its query, and the pane's width would cut the tail.

    The value here ends in the count `parts.more` wrote, which is the part a second cut would
    take — so the two arms are told apart by whether that count survives.
    """
    joined = "x" * queries.HEADER_CHARS + " and 3 more"
    assert plain(str(parts.fact(name="skills", value=joined, cut=False))).endswith("and 3 more")
    assert not plain(str(parts.fact(name="skills", value=joined, cut=True))).endswith("and 3 more")


# --- Where markup may go, and where it may not ---------------------------------------------


def test_prose_renders_the_markdown_a_session_wrote_rather_than_printing_it() -> None:
    """`view/text/render.py` owns the escaping, and its `Markup` reaches the page as markup.

    The other half of the rule the package holds: a component constructs no `Markup` and
    consumes the ones the four producers make.
    """
    served = str(parts.prose(field="brief", value="Read **schema.md** first."))
    assert "<strong>schema.md</strong>" in prose(served, "brief")


def test_a_detail_reads_its_head_the_one_way_its_row_said_the_value_was_written() -> None:
    """Three arms, one flag each: a syntax the record named, markdown, or the stored bytes.

    A value cannot be two of them — the same flag decides how it is marked up and whether the
    pane walls it as a quotation — so the classes are asserted beside the markup.
    """
    lit_up = str(parts.detail(item=line("input", '{"a": 1}', 0)._replace(syntax=Syntax.JSON)))
    written = str(parts.detail(item=line("brief", "Read **schema.md**.", 0, markdown=True)))
    stored = str(parts.detail(item=line("result", "plain output", 0)))
    # The syntax the row named is markup Pygments paints, and the block wears its name...
    assert walled(lit_up, "input") == "code json"
    assert classed(block(lit_up, "input"))
    # ...markdown is rendered and walled as the quotation it is...
    assert "<strong>schema.md</strong>" in prose(written, "brief")
    assert 'class="detail quoted"' in written
    # ...and everything else is the characters the store holds, in an unclassed block.
    assert walled(stored, "result") == ""
    assert plain(block(stored, "result")) == "plain output"


def test_a_cut_value_offers_the_rest_of_itself_where_the_head_stood() -> None:
    """The one place a fat column crosses the wire whole, in both blocks that preview one.

    A detail swaps its whole section and an enrichment line swaps its own span; the targets
    differ and the offer does not, which is why one function writes both links.
    """
    detail = str(parts.detail(item=line("result", "head", 900)))
    written = str(parts.enrichment_line(item=line("description", "head", 40)))
    assert values(detail, "hx-target") == ["closest .detail"]
    assert values(written, "hx-target") == ["closest .enrichment-line"]
    # Each says how much is behind the head, and each opens with the space that follows it.
    assert "+900 more character(s)" in plain(detail)
    assert " +40 more character(s)" in plain(written)
    # A value the query did not cut offers nothing.
    assert "hx-get" not in str(parts.detail(item=line("result", "head", 0)))


def test_a_stored_value_that_reads_as_markup_is_printed_rather_than_obeyed() -> None:
    """The escaping every component gets for free, asserted where store text reaches a page.

    A transcript can hold anything the agent read, so a branch named `<script>` has to come
    back out as the characters it is. htpy escapes every `str` child; what a component owes is
    to hand values in as children and never to build a `Markup` around one.
    """
    served = str(parts.fact(name="git_branch", value="<script>alert(1)</script>", cut=False))
    assert "<script>" not in served
    assert plain(served).endswith("<script>alert(1)</script>")


def test_a_summary_stands_the_provenance_on_the_glyph_and_the_words_beside_it(
    described: Enrichment,
) -> None:
    """What a pass wrote, under the mark that says a model wrote it and who.

    The glyph carries the provenance because the pane is the one surface with room for it; a
    NavTree row carries the mark alone.
    """
    written = line("description", described.description, 0)
    lines = EnrichmentLines(description=written, friction=None)
    served = str(parts.summary(enrichment=described, lines=lines))
    assert described.provenance in values(served, "title")
    # The words follow the glyph with a space between, and the closed vocabularies follow them.
    assert f"{GLYPH} {described.description}" in plain(served)
    assert 'data-field="category">implementation' in served
    # Nothing said the row was stale, so no tag claims it was.
    assert "stale" not in served


# --- The parts no page consumes until a later slice ----------------------------------------


def test_an_unpriced_mark_appears_only_where_our_table_priced_nothing() -> None:
    """A total missing calls is not what was spent, and the page has to say so.

    Outside the labelled span either way, so a reader of `data-field="cost_usd"` gets the
    number the store holds whether or not the mark is beside it.
    """
    marked = str(parts.unpriced(calls=3))
    assert marked.startswith("<sup ") and plain(marked) == "*"
    assert "3 call(s)" in values(marked, "title")[0]
    # A cost our table priced whole carries no mark at all.
    assert parts.unpriced(calls=0) is None


def test_a_kind_mark_is_written_with_no_space_and_no_word_of_its_own() -> None:
    """The mark stands for a word the markup around it already carries.

    Two claims, both about bytes: no trailing space — every caller writes its own, and a byte
    here is 3,217 bytes of NavTree — and no `title`, which would be the same word as often.
    """
    served = str(parts.mark(character="◆"))
    assert served == '<span class="icon" aria-hidden="true">◆</span>'


def test_a_cost_badge_carries_its_share_as_a_class_and_its_money_as_the_field() -> None:
    """The two readings of one badge: what it is worth, and how deep its ground is drawn.

    The step rides the class rather than a `data-*` of its own — the stylesheet is the only
    reader of it — and the money rides the field, which is what a test asserting spend reads.
    """
    served = str(parts.badge(step="warm-3", field="cost_usd", value=1.25))
    assert values(served, "class") == ["badge warm-3"]
    assert plain(served) == "$1.25"


def test_a_stacked_cell_hangs_each_mark_off_the_line_that_owns_what_it_qualifies() -> None:
    """Two slots, because the two lists stack the value a mark belongs to at different heights.

    The session list stacks output tokens under a cost and marks the cost; the projects landing
    stacks a cost under a session count and marks the cost again. One slot would put the mark
    on the wrong number for one of them.
    """
    served = str(
        parts.stacked(
            field="cost_usd",
            primary="$3.10",
            secondary_field="output_tokens",
            secondary="900",
            unit="out",
            primary_mark=parts.unpriced(calls=2),
            secondary_mark=parts.mark(character="◆"),
        )
    )
    # The first mark closes the primary line before the secondary span opens...
    marked = '<sup title="2 call(s) at a model our price table lacks">*</sup>'
    assert f"$3.10</span>{marked}<span" in served
    # ...and the second sits inside the secondary span, between its number and the unit word.
    assert '>900</span><span class="icon" aria-hidden="true">◆</span> out</span>' in served


# --- The two mounts of a page's provenance --------------------------------------------------


def test_a_footer_and_a_fragments_list_cite_a_query_the_same_way() -> None:
    """What produced a page is written once and mounted twice: folded, and open.

    A page's footer folds it away — it is provenance, not content — while an element swapped
    into someone else's page has no footer to end and stands its lines open. The `<li>` used to
    be written in both templates, which is two answers to one question, so the leaf is that the
    two mounts carry the same lines.
    """
    ran = {
        "session": cited("view_session_header", {"session_id": SPINE, "head_chars": 80}),
        "runs": cited("view_runs", {"session_id": SPINE}),
    }
    folded = str(citation.footer(citations=ran))
    open_lines = str(citation.listed(citations=ran))
    lines = re.findall(r"<li>.*?</li>", folded)
    # Two queries in, two lines out, and the same two either way...
    assert len(lines) == len(ran)
    assert lines == re.findall(r"<li>.*?</li>", open_lines)
    # ...with the fold the only thing that differs between the mounts...
    assert "what produced this page" in folded
    assert "what produced this page" not in open_lines
    # ...and a page that ran no query carrying no footer at all, where the open mount is part
    # of the element it was swapped in with and always has its count to show.
    assert citation.footer(citations={}) is None
    assert 'data-citations="0"' in str(citation.listed(citations={}))
