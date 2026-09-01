"""The tree the viewer's modules live in, which no rendered page can show.

`src/hyphae/view/` is organized by page and then by kind (`plans/view-layout/design.md`): one
package per page, the same file names inside each, and a shared layer under them all. The
layout is the contract — it is what makes "the context for one page is one directory" true —
and nothing but this file holds it. Each leaf reads the checkout the way
`tests/view/test_components.py` reads the components package's rules: `ast` over the source,
plus a fresh-interpreter probe where an in-process answer is already spoiled.

Every scan carries a companion assertion. A rule that finds nothing passes, and a tree that
moved out from under it is exactly how it comes to find nothing.

The leaves marked `xfail` are the ones the move has not reached yet. Each names the slice that
lands it and is strict, so it reds the day it starts passing: a slice takes its own marker out,
and no rule quietly arrives unread.
"""

import ast
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import hyphae.view
from tests.view import test_components

PACKAGE = "hyphae.view"
VIEW = Path(hyphae.view.__file__).parent
PAGES = VIEW / "pages"
TEXT = VIEW / "text"

# One package per page the glossary names (`CONTEXT.md`, "Viewer pages"). The node page is one
# package for the three route modules that serve it, and the two lists are one page each.
PAGE_NAMES = frozenset({"projects", "sessions", "node", "errors", "query", "records", "offload"})

# How one value prints, and nothing else — the seven modules `text/` gathers.
TEXT_MODULES = frozenset(
    {"format", "cuts", "labels", "tool_names", "render", "highlight", "inline_markdown"}
)

# The two file names that say what kind a module of a page package is, each of which may be a
# module or a package: a page small enough writes `routes.py`, and the node page needs `routes/`.
KINDS = ("routes", "markup")

# Names that say nothing about what a module builds. A presenter is named for the thing it
# makes — `nav_tree.py`, `walk.py` — and these are where the unnamed leftovers collect.
UNNAMED = frozenset({"logic.py", "utils.py", "helpers.py", "common.py", "misc.py"})

# What a module may reach: an import goes down a layer or sideways, never up. The layers, from
# the top: the server, then the pages, then what every page shares, then the store and the
# sizes, and `text/` under all of it.
SERVER, PAGE, SHARED, BASE, LEAF = 4, 3, 2, 1, 0

# The modules of each layer by name, for the ones that are not decided by their directory.
# Anything else at the top level is a page's presenter that has not moved yet, so it is read at
# the page layer — which is what makes an edge the move is meant to delete fail here today.
LAYERED = {
    "app": SERVER,
    "dev": SERVER,
    "deps": PAGE,
    "nodes": SHARED,
    "enrichment": SHARED,
    "citation": SHARED,
    "failures": SHARED,
    "store": BASE,
    "bounds": BASE,
    "manifest": BASE,
}

# Import the named modules in a fresh interpreter and report which web frameworks came in with
# them. A list built from the tree rather than written down, so a presenter that lands next year
# is covered without anyone remembering this file.
PROBE = """
import importlib, sys
for name in {names!r}:
    importlib.import_module(name)
sys.stdout.write(",".join(sorted({{"fastapi", "starlette"}} & set(sys.modules))))
"""


def sources(root: Path) -> list[Path]:
    """Every module under one directory of the package, or none where it does not exist yet."""
    return sorted(root.rglob("*.py")) if root.is_dir() else []


def dotted(path: Path) -> str:
    """The module one file is, as its dotted name inside `hyphae.view`.

    A package's `__init__.py` is the package itself, so an import of `components` and an import
    of `components/__init__.py`'s own name read as the same node of the graph.
    """
    parts = path.relative_to(VIEW).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def exists(module: str) -> bool:
    """Whether a dotted name under `hyphae.view` is a module or a package on the tree."""
    at = VIEW / Path(*module.split("."))
    return at.with_suffix(".py").is_file() or (at / "__init__.py").is_file()


def named(path: Path) -> set[str]:
    """Every top-level package one file imports, whatever it took from it."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module.split(".")[0])
    return found


def imports(path: Path) -> set[str]:
    """Every module inside `view/` that one file imports, by dotted name.

    `from hyphae.view import format as fmt` names a module and `from hyphae.view.components
    import Html` names a package, so each imported name is resolved against the tree and falls
    back to the package it was taken from. Absolute imports only: relative ones are banned
    repo-wide (`pyproject.toml`, `flake8-tidy-imports`).
    """
    here = dotted(path)
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {
                alias.name.removeprefix(PACKAGE).lstrip(".")
                for alias in node.names
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}.")
            }
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module != PACKAGE and not node.module.startswith(f"{PACKAGE}."):
                continue
            base = node.module.removeprefix(PACKAGE).lstrip(".")
            for alias in node.names:
                named = f"{base}.{alias.name}" if base else alias.name
                found.add(named if exists(named) else base)
    # A module reaching into itself is not an edge, and neither is the package root, which
    # holds a docstring and nothing to import.
    return {name for name in found if name and name != here}


def edges() -> set[tuple[str, str]]:
    """Every import inside `view/`, as the pair `(importer, imported)`."""
    return {(dotted(path), name) for path in sources(VIEW) for name in imports(path)}


def layer(module: str) -> int:
    """Which layer one module of `view/` sits on."""
    head = module.split(".", maxsplit=1)[0]
    if head == "text":
        return LEAF
    if head == "components":
        return SHARED
    if head == "pages":
        return PAGE
    return LAYERED.get(module, PAGE)


def page_packages() -> list[Path]:
    """Every page package on the tree, discovered rather than listed."""
    return sorted(
        at.parent for at in sources(PAGES) if at.name == "__init__.py" and at.parent != PAGES
    )


def kind_of(page: Path, path: Path) -> str:
    """Which kind of module of its page a file is: `routes`, `markup`, or a presenter's name."""
    head = path.relative_to(page).parts[0]
    return head.removesuffix(".py")


def markup_modules() -> list[Path]:
    """Every page's markup, whether the page writes a `markup.py` or a `markup/`."""
    return [
        path
        for page in page_packages()
        for path in sources(page)
        if kind_of(page, path) == "markup"
    ]


def frameworks(names: Sequence[str]) -> str:
    """The web frameworks a fresh interpreter importing `names` ended up holding."""
    done = subprocess.run(
        [sys.executable, "-c", PROBE.format(names=list(names))],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return done.stdout


# --- Rule 1: what a file's name says about what is inside it -------------------------------


@pytest.mark.xfail(strict=True, reason="the last page package lands in slice 6")
def test_every_page_the_viewer_serves_has_a_package_of_its_own() -> None:
    """The pages the glossary names are the packages on the tree, one for one.

    The companion every leaf below leans on: each of them is parametrized over what `iterdir`
    finds, so a `pages/` that emptied or moved would leave them all passing on nothing. This is
    the leaf that says the discovery found the viewer.
    """
    assert {page.name for page in page_packages()} == set(PAGE_NAMES)


@pytest.mark.parametrize("page", page_packages(), ids=lambda page: page.name)
def test_only_a_pages_routes_module_reaches_a_web_framework(page: Path) -> None:
    """A presenter is callable without a request, so pyrefly rather than FastAPI owns its types.

    A fresh interpreter, because this tier's conftest builds `TestClient`s: by the time any test
    runs `fastapi` is in `sys.modules`, and an in-process assertion would pass on a presenter
    importing `Request` on its first line.
    """
    routes = [path for path in sources(page) if kind_of(page, path) == "routes"]
    rest = [path for path in sources(page) if kind_of(page, path) != "routes"]
    # The page has both halves to tell apart...
    assert routes, f"{page.name} declares no routes"
    assert rest, f"{page.name} is nothing but routes"
    # ...nothing but the routes reaches a framework...
    assert frameworks([f"{PACKAGE}.{dotted(path)}" for path in rest]) == ""
    # ...and the routes do, which is what shows the probe can see a framework at all.
    assert frameworks([f"{PACKAGE}.{dotted(path)}" for path in routes]) == "fastapi,starlette"


@pytest.mark.parametrize("page", page_packages(), ids=lambda page: page.name)
def test_every_page_package_holds_one_routes_kind_and_one_markup_kind(page: Path) -> None:
    """`routes.py` or `routes/`, `markup.py` or `markup/`, one of each and never both.

    The consistency the tree is for: an agent looking for where a page answers a request opens
    the file whose name says so, without listing the directory first.
    """
    for kind in KINDS:
        module = (page / f"{kind}.py").is_file()
        package = (page / kind / "__init__.py").is_file()
        assert module != package, f"{page.name}: {kind}.py={module} {kind}/={package}"


@pytest.mark.xfail(strict=True, reason="the first page packages land in slice 4")
def test_only_a_markup_module_imports_htpy() -> None:
    """Markup is written in one file of a page, so a change to what a page shows has one home."""
    naming = {path for path in sources(PAGES) if "htpy" in named(path)}
    # The scan found the pages' markup...
    assert naming, "no module under `pages/` imports htpy"
    # ...and every file that names htpy is one whose name says it holds markup.
    assert naming == set(markup_modules())


@pytest.mark.xfail(strict=True, reason="the first page packages land in slice 4")
def test_no_module_of_a_page_is_named_for_nothing() -> None:
    """A presenter is named for what it builds; these five names are where leftovers collect.

    The checkable floor under rule 1. That `nav_tree.py` is a better name than `logic.py` for
    what is in it is a judgement about English, and review owns it (`.claude/rules/viewer-ui.md`).
    """
    found = sources(PAGES)
    # There are modules to read...
    assert found, "`pages/` holds no module"
    # ...and none of them took one of the names that says nothing.
    assert [str(path.relative_to(VIEW)) for path in found if path.name in UNNAMED] == []


def test_a_models_module_appears_only_where_a_second_markup_module_reads_it() -> None:
    """A view-model lives in the markup that consumes it until a second markup module reads it.

    Both halves, because today the first is empty: no page has a `models.py`, and the design
    says none should until one model gains a second reader (`design.md`, "Decisions"). So the
    leaf pins the absence as well as the rule, and reds the day a `models.py` arrives with one
    reader — the split of a `NamedTuple` from the one function that reads it.
    """
    for path in sources(PAGES):
        if path.name != "models.py":
            continue
        page = path.parent
        readers = [
            module
            for module in markup_modules()
            if module.is_relative_to(page) and dotted(path) in imports(module)
        ]
        assert len(readers) > 1, f"{path.relative_to(VIEW)} is read by {len(readers)} markup module"
    # And the other half: no page has one, so the first to arrive is a decision the design
    # asked to be revisited rather than a file that slipped in under the rule above.
    assert [
        str(path.relative_to(VIEW)) for path in sources(PAGES) if path.name == "models.py"
    ] == [], "a `models.py` arrived — revisit `design.md`'s decision and say so here"


# --- Rule 2: a page package is a leaf ------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="the first page packages land in slice 4")
def test_no_page_package_imports_a_sibling_page() -> None:
    """One page is one directory, so what two pages share is lifted rather than reached for.

    `failures.py` and `header_bound` were lifted into the shared layer for exactly this
    (`design.md`, "Three lifts"): a session's failures are a session fact the errors page and
    the node page's stepper both read, and a sibling import would make the errors page the node
    page's dependency.
    """
    found = [(dotted(path), name) for path in sources(PAGES) for name in imports(path)]
    # There are page modules importing something...
    assert found, "no module under `pages/` imports anything inside the viewer"
    for importer, imported in found:
        page = importer.split(".")[1]
        assert not (imported.startswith("pages.") and imported.split(".")[1] != page), (
            f"{importer} → {imported}"
        )


# --- Rule 3: downward only -----------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="the layers are not all in place until slice 6")
def test_no_import_inside_the_viewer_points_up_a_layer() -> None:
    """The layers hold: pages over the shared view-models, over the store, over `text/`.

    What keeps the shared layer readable without the pages and testable without a request. The
    failure prints the edge, because an edge is what has to be deleted to fix it.
    """
    found = edges()
    # The graph found the package...
    assert found, "no import inside the viewer was resolved"
    # ...including the edge this whole tree is built around: a presenter reading the node model.
    assert any(
        imported == "nodes" and importer.split(".")[-1] == "walk" for importer, imported in found
    )
    # ...and every edge in it goes down a layer or sideways.
    for importer, imported in sorted(found):
        assert layer(imported) <= layer(importer), f"{importer} → {imported}"


def test_text_reaches_nothing_in_the_viewer_but_itself_and_the_sizes_it_cuts_to() -> None:
    """`text/` is the leaf of the package: how one value prints, and nothing above it.

    `bounds` is the one exception the design takes, and it is the same kind of thing: a cut is
    a size, and `highlight` and `inline_markdown` read theirs from it.
    """
    found = sources(TEXT)
    # The seven printing modules are there to be read...
    assert {path.stem for path in found} - {"__init__"} == set(TEXT_MODULES)
    # ...and not one of them reaches past itself.
    for path in found:
        for name in imports(path):
            assert name == "bounds" or name.split(".")[0] == "text", f"{dotted(path)} → {name}"


@pytest.mark.xfail(strict=True, reason="`nodes` reads the kind icons from `columns` until slice 3")
def test_the_shared_node_model_reads_nothing_from_a_page() -> None:
    """`nodes.py` is under every page, so an import of a page's own module inverts the tree.

    Named rather than left to the graph above because this is where a cycle comes back
    silently: the icons moved into `nodes.GLYPHS` to delete one such edge, and the move is only
    real while the edge stays gone.
    """
    reached = imports(VIEW / "nodes.py")
    # The model still imports what it is built on...
    assert reached, "`nodes` imports nothing inside the viewer"
    # ...and nothing that a page owns.
    assert [name for name in reached if layer(name) >= PAGE] == []


# --- Rule 4: the components rules cover a page's markup ------------------------------------


@pytest.mark.xfail(strict=True, reason="the markup globs widen in slice 4")
def test_the_components_rules_reach_every_markup_module_a_page_holds() -> None:
    """A page's markup is markup, so the three rules `test_components.py` holds cover it too.

    The scope of those rules is asserted here rather than there: a page whose markup escapes
    them would leave that file green and this one red, which is the way round that says which
    file has to change.
    """
    covered = set(test_components.SOURCES)
    # The pages hold markup to cover...
    assert markup_modules(), "no page holds a markup module"
    # ...and every module of it is one the components rules are read over.
    assert set(markup_modules()) <= covered
