"""What each surface prints store text at.

`test_bounds.py` reads the widths off what a page cited, which reaches only the surfaces a
footer quotes. Four print none — an expansion, a popover, the enrichment block and the
NavTree's own fetches are fragments, and a fragment arrives on a page already served — so a
width only they carry is held here or nowhere.

Swept rather than listed read by read: the gap it closes is a profile that arrives with
nobody's number behind it.
"""

from hyphae.view import bounds

# What each surface prints at, spelled out once. Read off nothing: this is the half of the pin
# that says which width a number is, and `test_bounds.py:test_the_pages_run_at_the_production_sizes`
# is the half that says the pages ran at it. The comments live in `view/bounds.py`, beside the
# numbers they are about — what is repeated here is the number and not the reason for it.
PROFILES: dict[str, bounds.Widths] = {
    "NAV_TREE_WIDTHS": bounds.NavTree(nav_chars=110, chip_chars=110, log_chars=110),
    "HEADER_WIDTHS": bounds.Header(head_chars=100, item_chars=60, head_items=5, chip_chars=100),
    "LOG_WIDTHS": bounds.Log(log_chars=300, chip_chars=300),
    "EXPANSION_WIDTHS": bounds.Expansion(head_chars=100, detail_chars=100),
    "POPOVER_WIDTHS": bounds.Popover(model_chars=60, chip_chars=60, item_chars=60, head_items=5),
    "LIST_WIDTHS": bounds.SessionList(
        head_chars=100,
        item_chars=20,
        head_items=4,
        tag_chars=20,
        kind_chars=20,
        head_kinds=3,
        head_projects=10,
    ),
    "PROJECTS_WIDTHS": bounds.Projects(recent_days=7, window_days=30, head_chars=100, projects=100),
    "ERRORS_WIDTHS": bounds.Errors(nav_chars=110, errors=100),
    "RECORDS_WIDTHS": bounds.Records(preview_chars=160),
    "ENRICHMENT_WIDTHS": bounds.Enrichment(description_chars=200, tag_chars=20, head_chars=100),
}


def test_every_surface_declares_the_widths_it_prints_at() -> None:
    """Each profile at its literals, so a surface cannot arrive unpinned.

    A profile `bounds.py` gains with no line above reds on the first assertion, before a
    reader has to notice that no footer quotes it and nothing says its numbers were chosen.

    The class is pinned beside the numbers because a `NamedTuple` compares as the tuple it is:
    `Errors(nav_chars=110, errors=100)` equals any other two-field profile of 110 and 100, so
    equality alone would let a surface declare another one's widths.
    """
    declared = {
        name: value for name, value in vars(bounds).items() if isinstance(value, bounds.Widths)
    }
    assert sorted(declared) == sorted(PROFILES)
    for name, profile in declared.items():
        assert (type(profile), profile) == (type(PROFILES[name]), PROFILES[name]), name
