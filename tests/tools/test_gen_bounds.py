"""What the four bounds tables have to hold: the viewer's own numbers, and all of them.

The bounds prose is the viewer's payload contract, so a table that drifts from `bounds.py` or
from the page arithmetic is worse than no table at all. Every leaf here reads the live modules
— none of them spells a number, and the coverage leaf makes a new bound impossible to leave
undocumented by accident.
"""

import pytest

from hyphae.view import bounds, nodes
from hyphae.view.pages.node.knobs import Knobs
from tests.tools.conftest import cells, numbers
from tests.view import budgets
from tools import gen_bounds

TABLES = list(gen_bounds.Table)


def knob_rows() -> dict[str, str]:
    """The knob table as a mapping: the knob a reader types, to what the row says it does."""
    return {row[0].strip("`"): row[1] for row in cells(gen_bounds.generate(gen_bounds.Table.KNOBS))}


@pytest.mark.parametrize("table", TABLES)
def test_every_number_a_row_prints_is_the_constant_it_cites_in_that_position(
    table: gen_bounds.Table,
) -> None:
    # The headline: no number in either table is written by hand, and each stands for the one
    # constant the row cites for that position. Pooling a row's citations into a set would let
    # two of its numbers trade places — a ceiling printed where the default belongs, a list's
    # item count where its category count belongs — and still read as cited. What this pins is
    # the pairing: same numbers, same order, same count. Two names that hold the same value are
    # indistinguishable here by construction, which is the limit of reading printed numbers.
    for row in gen_bounds.rows(table):
        cited = [gen_bounds.valued(name) for name in row.cites]
        assert numbers(row.says) == cited, (
            f"`{row.subject}` prints {numbers(row.says)}, but cites {list(row.cites)} = {cited}"
        )


@pytest.mark.parametrize("table", TABLES)
def test_the_generated_table_prints_no_number_of_its_own(table: gen_bounds.Table) -> None:
    # And the weaker property end to end, over the spliced text rather than the rows behind it,
    # so a subject or a header that grew a number is caught as well.
    every = {gen_bounds.valued(name) for name in gen_bounds.cited()}
    assert set(numbers(gen_bounds.generate(table))) <= every


def test_every_page_the_table_prints_fits_under_the_ceiling_beside_it() -> None:
    # The reading the page table exists to give: each worst case is under what that page is
    # allowed. `tests/view/test_bounds.py` is what enforces it — this pins that the table says
    # so, since a row printing its two numbers the other way round would read as a page over
    # its ceiling and still be cited correctly.
    for row in gen_bounds.rows(gen_bounds.Table.PAGES):
        worst, ceiling = numbers(row.says)
        assert worst < ceiling, f"`{row.subject}` prints {worst} against {ceiling}"


def test_the_node_table_accounts_for_every_byte_the_node_page_is_allowed() -> None:
    # The node table is a decomposition, so it has to close: every part, plus the spare the
    # ceiling leaves over the arithmetic, is the ceiling. A part added to `worst_node_bytes`
    # and left out of the table would fall short here rather than quietly under-describe the
    # dearest page the viewer serves.
    parts = [numbers(row.says)[-1] for row in gen_bounds.rows(gen_bounds.Table.NODE)]
    assert sum(parts) == budgets.NODE_BYTES


def test_every_bound_is_cited_by_a_table_or_named_as_uncited() -> None:
    # The ratchet: a size added to `bounds.py` lands in a table or is named as one the tables
    # leave to the prose around them. Silence is the one thing it cannot do.
    covered = gen_bounds.cited_bounds() | set(gen_bounds.UNCITED)
    uncovered = gen_bounds.declared() - covered
    assert not uncovered, f"bounds no table documents: {sorted(uncovered)}"


def test_nothing_is_named_uncited_that_bounds_no_longer_declares() -> None:
    # The other direction: a deleted bound takes its excuse with it, and a bound cannot be
    # both cited and excused.
    assert set(gen_bounds.UNCITED) <= gen_bounds.declared()
    assert not set(gen_bounds.UNCITED) & gen_bounds.cited_bounds()


def test_the_knob_table_lists_exactly_the_knobs_the_app_reads() -> None:
    # A knob table is a promise about the URL, so the app's own knob list is what it renders:
    # `?nav=` once per preset, and one row for each size a reader can type down.
    typed = [knob.removeprefix("?") for knob in knob_rows()]
    assert {knob.split("=")[0] for knob in typed} == set(Knobs._fields)
    navs = {knob.split("=")[1] for knob in typed if knob.startswith("nav=")}
    assert navs == {preset.value for preset in nodes.Preset}


def test_a_size_knob_carries_the_ceiling_that_caps_it() -> None:
    # The number beside `?kin=` is what a URL asking for more than it gets refused at.
    said = knob_rows()
    for knob, bound in (("kin", bounds.KIN), ("log", bounds.LOG), ("detail", bounds.DETAIL)):
        assert numbers(said[f"?{knob}="]) == [bound.ceiling]


def test_the_word_maps_describe_exactly_what_the_app_still_offers() -> None:
    # Both label maps are hand-written, so both rot in both directions: a knob or a preset with
    # no words would print a blank cell, and words for one the app dropped would describe a
    # URL nobody can type. Neither is visible in a green table, so it is pinned here.
    assert set(gen_bounds.SIZE_WORDS) == set(Knobs._fields) - {"nav"}
    assert set(gen_bounds.PRESET_WORDS) == set(nodes.Preset)


def test_a_size_knob_with_no_words_crashes_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    # And the crash the map's absence has to cause: a table is not spliced with a knob left
    # undescribed. Deleting the words is how a knob renamed in `app.py` reaches the generator.
    monkeypatch.delitem(gen_bounds.SIZE_WORDS, "kin")
    with pytest.raises(ValueError, match="kin"):
        gen_bounds.generate(gen_bounds.Table.KNOBS)


def test_a_preset_with_no_words_crashes_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    # A new preset must be described before it can be listed: a blank cell would say the viewer
    # has a view nobody can explain.
    monkeypatch.delitem(gen_bounds.PRESET_WORDS, nodes.Preset.AGENTS)
    with pytest.raises(ValueError, match="agents"):
        gen_bounds.generate(gen_bounds.Table.KNOBS)


@pytest.mark.parametrize("table", TABLES)
def test_the_table_ends_without_its_own_newline(table: gen_bounds.Table) -> None:
    # `main()` prints, and the cog splice owns the framing newline.
    assert not gen_bounds.generate(table).endswith("\n")
