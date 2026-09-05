"""What each surface prints store text at, and which surface a read names.

`test_bounds.py` reads the widths off what a page cited, which reaches only the surfaces a
footer quotes. Four print none — an expansion, a popover, the enrichment block and the
NavTree's own fetches are fragments, and a fragment arrives on a page already served — so a
width only they carry is held here or nowhere.

Swept rather than listed read by read: the gap it closes is a profile that arrives with
nobody's number behind it.
"""

import ast
from pathlib import Path

import hyphae.view
from hyphae.view import bounds
from hyphae.view.store import Page

VIEW = Path(hyphae.view.__file__).parent

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


# Which surface each read of a session's agent runs names. Two reads of one query at two
# widths: the node page prints a run as a children log row and draws it as a NavTree row, so
# it reads at the wider of the two and cuts again at each; the tail row's fetch draws NavTree
# rows and nothing else, so it reads at a row's width.
RUNS_READS = [
    ("pages/node/routes/browse.py", "LOG_WIDTHS"),
    ("pages/node/routes/expansions.py", "NAV_TREE_WIDTHS"),
]


def called(func: ast.expr) -> str:
    """The name a call is written under, however it was imported."""
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def runs_reads() -> list[tuple[str, str]]:
    """Every `bound(Page.RUNS, …)` the viewer makes, by module, with the surface it names.

    The seam takes its page and its surface positionally (`view/store.py:bound`), so the call
    says which is which without being run. A read that named its surface some other way trips
    the assertion rather than dropping out of the sweep.
    """
    found: list[tuple[str, str]] = []
    for path in sorted(VIEW.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or called(node.func) != "bound":
                continue
            page, widths = node.args[0], node.args[1]
            if not isinstance(page, ast.Attribute) or page.attr != Page.RUNS.name:
                continue
            assert isinstance(widths, ast.Attribute), ast.unparse(node)
            found.append((str(path.relative_to(VIEW)), widths.attr))
    return sorted(found)


def test_each_read_of_a_sessions_runs_names_the_surface_it_is_drawn_at() -> None:
    """The one read whose surface nothing else can see, held to the surface it is drawn at.

    Read off the source, because no rendered byte carries the choice: the tail row's fetch is
    a fragment with no footer to quote a width, and the NavTree cuts a title to a row's width
    whatever the query brought back. Swapping that read to the log's profile leaves all 137
    tail-row fetches the fixture corpus offers at `?kin=1` byte for byte identical (probed) —
    it ships green, prints the same page, and fetches three times the string for a surface
    that shows a third of it.

    A list rather than a map, so a second read in one module cannot take the place of the
    first, and the map is the scan's companion: a read that moved, a read that arrived, or a
    scan that stopped finding anything all red here rather than passing on an empty sweep.
    """
    assert runs_reads() == RUNS_READS
