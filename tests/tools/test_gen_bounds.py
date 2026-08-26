"""What the two bounds tables have to hold: the viewer's own numbers, and all of them.

The bounds prose is the viewer's payload contract, so a table that drifts from `bounds.py` is
worse than no table at all. Every leaf here reads the live module — none of them spells a
number, and the coverage leaf makes a new bound impossible to leave undocumented by accident.
"""

import pytest

from aiobserve.view import bounds, nodes
from aiobserve.view.app import KNOB_DEFAULTS
from tests.tools.conftest import cells, numbers
from tools import gen_bounds

TABLES = list(gen_bounds.Table)


def knob_rows() -> dict[str, str]:
    """The knob table as a mapping: the knob a reader types, to what the row says it does."""
    return {row[0].strip("`"): row[1] for row in cells(gen_bounds.generate(gen_bounds.Table.KNOBS))}


@pytest.mark.parametrize("table", TABLES)
def test_every_number_a_row_prints_is_a_constant_it_cites(table: gen_bounds.Table) -> None:
    # The headline: no number in either table is written by hand. Each row declares the
    # constants it prints, and nothing else may appear in it as a number.
    for row in gen_bounds.rows(table):
        allowed = {value for name in row.cites for value in gen_bounds.valued(name)}
        printed = set(numbers(row.says))
        assert printed <= allowed, f"`{row.subject}` prints {sorted(printed - allowed)}"


@pytest.mark.parametrize("table", TABLES)
def test_the_generated_table_prints_no_number_of_its_own(table: gen_bounds.Table) -> None:
    # And the same property end to end, over the spliced text rather than the rows behind it,
    # so a subject or a header that grew a number is caught as well.
    every = {value for name in gen_bounds.cited() for value in gen_bounds.valued(name)}
    assert set(numbers(gen_bounds.generate(table))) <= every


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
    assert {knob.split("=")[0] for knob in typed} == set(KNOB_DEFAULTS)
    navs = {knob.split("=")[1] for knob in typed if knob.startswith("nav=")}
    assert navs == {preset.value for preset in nodes.Preset}


def test_a_size_knob_carries_the_ceiling_that_caps_it() -> None:
    # The number beside `?kin=` is what a URL asking for more than it gets refused at.
    said = knob_rows()
    for knob, bound in (("kin", bounds.KIN), ("log", bounds.LOG), ("detail", bounds.DETAIL)):
        assert numbers(said[f"?{knob}="]) == [bound.ceiling]


def test_a_preset_with_no_words_crashes_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    # A new fold must be described before it can be listed: a blank cell would say the viewer
    # has a view nobody can explain.
    monkeypatch.delitem(gen_bounds.PRESET_WORDS, nodes.Preset.AGENTS)
    with pytest.raises(ValueError, match="agents"):
        gen_bounds.generate(gen_bounds.Table.KNOBS)


@pytest.mark.parametrize("table", TABLES)
def test_the_table_ends_without_its_own_newline(table: gen_bounds.Table) -> None:
    # `main()` prints, and the cog splice owns the framing newline.
    assert not gen_bounds.generate(table).endswith("\n")
