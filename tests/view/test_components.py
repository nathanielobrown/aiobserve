"""The contracts the components package holds that no rendered page can show.

The viewer's markup is typed Python (`src/hyphae/view/components/`), so promises Jinja's
environment used to make structurally have to be made here instead: a component imports no web
framework, its signature is something a checker can hold, and it never writes markup where htpy
will escape it. Each leaf reads the source a reviewer reads, or drives htpy itself — what a page
finally serves is the rest of this tier's business.

The two source scans each carry a companion assertion. A scan that finds nothing passes, and a
name that moved is exactly how it comes to find nothing, so every "no component does X" leaf is
paired with a "the thing X names is still there" one.

The last two leaves are about the one thing pyrefly is not allowed to check here. htpy's child
parameter is a recursive type alias, which pyrefly cannot decide, so `bad-index` is off over this
package alone — and a canary holds that narrowing to its reason.
"""

import ast
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import htpy
import pytest
from markupsafe import Markup

import hyphae.view
import hyphae.view.components
from hyphae.analyze import queries
from hyphae.view.components import layout, logs, nav_tree, parts
from hyphae.view.text.highlight import Syntax, lit

COMPONENTS = Path(hyphae.view.components.__file__).parent
VIEW = Path(hyphae.view.__file__).parent

# This checkout, for the config the narrowing is declared in: tests/view/… → the root.
REPO = Path(__file__).resolve().parents[2]

# The one path pyrefly is allowed to check less of, and the one kind it may stop checking there.
NARROWED = "src/hyphae/view/components/**"
# The one page composed outside the package: the gallery index, which nests htpy the same way.
GALLERY = "tests/gallery/**"
UNCHECKED = {"bad-index": False}

# Every module that defines components. `__init__.py` holds the package's rules and the one
# type they are written in, so it is scanned for markup but never asked for a signature.
MODULES = sorted(path for path in COMPONENTS.rglob("*.py") if path.name != "__init__.py")
SOURCES = sorted(COMPONENTS.rglob("*.py"))

# The names that make the one thing htpy will not escape. A component may hand a `Markup` in as
# a child and may never write one into an attribute, because htpy escapes an attribute value
# even when it is already markup (`.claude/rules/viewer-ui.md`).
PRODUCERS = ("nav_tree_title", "crumb_title", "markdown(", "lit(", "link(")

# Where a `Markup` is actually constructed, for the companion half of the scan below. Not
# `nodes.py`, whose titles return one without ever calling the constructor — it asks
# `inline_markdown` for it.
PRODUCER_MODULES = ("render.py", "highlight.py", "inline_markdown.py")

# Annotations a component parameter may not name: two the checker cannot hold, and three the
# package exists not to import.
DENIED = ("Any", "Row", "Request", "Response")

# And the annotation it may not be, whole. `dict[str, str]` says what is in it and is fine;
# bare `dict` is `Any` spelt differently.
BARE = "dict"

# What a component hands back: markup, or nothing where the thing it draws is absent. The
# concrete union rather than htpy's `Renderable` protocol, which pyrefly cannot match an
# `Element` against — `components/__init__.py` says why.
RETURNS = ("Html", "Html | None")

# Import every module of the package in a fresh interpreter and report which web frameworks
# came in with them. The walk rather than a written list, so a component that lands next year
# is covered without anyone remembering to add it here.
PROBE = """
import importlib, pkgutil, sys
import hyphae.view.components as package
for found in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
    importlib.import_module(found.name)
sys.stdout.write(",".join(sorted({"fastapi", "starlette"} & set(sys.modules))))
"""

# The same question asked of the app, which is nothing but framework: the negative control that
# keeps a typo in the probe from reading as purity.
CONTROL = """
import sys
import hyphae.view.app
sys.stdout.write(",".join(sorted({"fastapi", "starlette"} & set(sys.modules))))
"""

# The htmx swap vocabularies the package names, keyed by the constant that holds each. A swap
# is a handful of attributes that only mean anything together, so each set is written once and
# spread into the elements that take it — `hx-get` is in none of them, because the URL is the
# element's own.
SWAPS = {
    "PANE_SWAP": nav_tree.PANE_SWAP,
    "UNSET_SWAP": nav_tree.UNSET_SWAP,
    "OPEN_SWAP": logs.OPEN_SWAP,
}

# The three of those attributes that mean the pane swap and nothing else — what to select out of
# the response, what to swap out of band with it, and whether the URL follows. A widget that
# replaces itself names `hx-target` and `hx-swap` on its own and is right to; naming one of
# these is a second pane swap written by hand.
PANE_ONLY = ("hx-select", "hx-select-oob", "hx-push-url")

# The one line `--dev` adds to a page. Bare, because htpy writes no whitespace between elements.
DEV_TAG = '<script src="/static/dev-reload.js" defer></script>'

# The nesting that forces the narrowing, whole: htpy's `Node` alias names itself inside
# `Iterable`, `Callable`, `AsyncIterable` and `Awaitable`, and pyrefly cannot decide a recursive
# alias in that position. Beside it, the same call with a plain string child, which pyrefly has
# always been able to decide — the control that keeps the canary from reading an unrelated error
# as its reason.
NESTED = "import htpy\n\nnested = htpy.nav[htpy.a(href='/')['x']]\n"
FLAT = "import htpy\n\nflat = htpy.nav['x']\n"

# A project with nothing turned off, so the canary reads pyrefly's own answer rather than this
# repo's. `project-includes` names the file the leaf writes beside it.
BARE_PROJECT = '[tool.pyrefly]\nproject-includes = ["probe.py"]\n'


def imported(source: str) -> str:
    """The frameworks a fresh interpreter running `source` ended up holding."""
    done = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=120, check=True
    )
    return done.stdout


def checked(source: str, tmp_path: Path) -> str:
    """What pyrefly says about one file, under a project that turns nothing off."""
    (tmp_path / "probe.py").write_text(source)
    (tmp_path / "pyproject.toml").write_text(BARE_PROJECT)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyrefly",
            "check",
            # The interpreter named explicitly: pyrefly looks for htpy in a site-packages it
            # infers from the project it is checking, and that project is the empty one above.
            "--python-interpreter-path",
            sys.executable,
            str(tmp_path / "probe.py"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return done.stdout + done.stderr


def components(module: Path) -> list[ast.FunctionDef]:
    """Every public function a module defines at its top level."""
    return [
        found
        for found in ast.parse(module.read_text()).body
        if isinstance(found, ast.FunctionDef) and not found.name.startswith("_")
    ]


def attributes(module: Path) -> list[tuple[str, str]]:
    """Every attribute an htpy element in `module` is written with, as `(name, expression)`.

    Elements are called `htpy.div(...)` throughout the package rather than imported one by one
    (`components/__init__.py`), which is what makes an attribute position something a scan can
    find: a keyword on one of those calls writes an attribute, and a keyword on anything else
    is a component being handed a child.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(module.read_text())):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if not (isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name)):
            continue
        if called.value.id != "htpy":
            continue
        found.extend((keyword.arg or "**", ast.unparse(keyword.value)) for keyword in node.keywords)
    return found


# --- What a component may import ---------------------------------------------------------


def test_importing_a_component_pulls_in_no_web_framework() -> None:
    """The components package is framework-free, checked where the answer has not been spoiled.

    A fresh interpreter, because this suite's conftest builds `TestClient`s: by the time any
    test runs `fastapi` is already in `sys.modules`, and an in-process assertion would pass on
    a component that imports `Request` on its first line.
    """
    # Nothing the package holds reaches a framework...
    assert imported(PROBE) == ""
    # ...and the same question asked of the app, which is made of one, comes back naming both —
    # which is what shows the probe can see a framework at all.
    assert imported(CONTROL) == "fastapi,starlette"


# --- What a component's signature must look like ------------------------------------------


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.name)
def test_every_component_clears_the_signature_floor(module: Path) -> None:
    """Each component takes keyword arguments the checker can hold and hands back markup.

    The floor a scan can check; "precise" above it — a `Kind` where a `str` would typecheck —
    is review's, and `.claude/rules/viewer-ui.md` says so. An AST scan rather than `inspect`,
    so what is read is the source a reviewer reads and no import is needed.
    """
    found = components(module)
    # Every module of the package defines at least one component, so a parse that found
    # nothing — a renamed directory, a file that stopped being scanned — fails rather than
    # passing empty...
    assert found, f"{module.name} defines no component"
    for function in found:
        where = f"{module.name}:{function.name}"
        taken = function.args
        # ...each is called by keyword alone, so a call site names every value it passes...
        assert not taken.posonlyargs and not taken.args, where
        assert taken.vararg is None and taken.kwarg is None, where
        for argument in taken.kwonlyargs:
            assert argument.annotation is not None, f"{where}({argument.arg})"
            written = ast.unparse(argument.annotation)
            # ...and every argument says what it is, in something better than `Any` under
            # another name and never a type the route layer owns...
            assert written != BARE, f"{where}({argument.arg}: {written})"
            named = set(re.findall(r"\w+", written))
            assert not named & set(DENIED), f"{where}({argument.arg}: {written})"
        # ...and what comes back is markup rather than a string the caller has to trust.
        assert function.returns is not None, where
        assert ast.unparse(function.returns) in RETURNS, where


# --- Where markup may and may not go ------------------------------------------------------


def test_no_component_constructs_markup() -> None:
    """Components consume the escape hatch and never open one, so escaping stays in four files.

    htpy escapes every string it renders. A `Markup` is the only opt-out, and keeping its
    construction outside this package is what makes "escaped unless `view/text/render.py`,
    `highlight.py`, `inline_markdown.py` or `nodes.py` said otherwise" a rule a reader can check.
    """
    # Nothing under `components/` builds one...
    assert [path.name for path in SOURCES if "Markup(" in path.read_text()] == []
    # ...and the four modules that do still do, so the scan above found nothing because the
    # package holds the line rather than because `Markup` was renamed out from under it. The
    # whole package is walked: three of the four print one value and live in `view/text/`.
    makers = {path.name for path in VIEW.rglob("*.py") if "Markup(" in path.read_text()}
    assert set(PRODUCER_MODULES) <= makers


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.name)
def test_no_attribute_a_component_writes_is_handed_a_markup_producer(module: Path) -> None:
    """No component routes markup into an attribute, where htpy would escape it a second time.

    The failure this stands in for has no reader: an attribute holding double-escaped markup
    renders as visible `&lt;b&gt;` text that every `data-*` reader in this tier reads straight
    past. So today's zero is pinned, which is the same grep the design audit ran over the
    templates it replaced.
    """
    written = attributes(module)
    # Every module writes attributes, so a scan that stopped finding element calls fails...
    assert written, f"{module.name} writes no element attribute"
    # ...and none of them is handed a value one of the four producers made.
    for name, expression in written:
        for producer in PRODUCERS:
            assert producer not in expression, f"{module.name}: {name}={expression}"


def test_the_producers_the_attribute_scan_names_are_names_components_still_use() -> None:
    """The scan above is looking for names this package actually mentions, in child position.

    Its companion: a producer renamed everywhere would empty the scan silently, and a leaf that
    can only pass is not a control. Not all five yet — a producer arrives with the component
    that consumes it, and the conversion is not finished.
    """
    source = "".join(path.read_text() for path in SOURCES)
    assert [producer for producer in PRODUCERS if producer in source]


def test_a_markup_child_reaches_the_page_as_the_markup_its_producer_made() -> None:
    """The other half of the rule: what `highlight.lit` marked up is not escaped again.

    Real material, and the material this component actually renders in production — a query
    file this build ships, marked up by the producer the query page hands to it. A hand-built
    `Markup` would prove that htpy honours the type; this proves the producer still makes one.
    """
    statement = queries.load("view_sessions")
    shown = lit(statement, Syntax.SQL)
    # The producer really made markup out of it, so there is something here to escape...
    assert "<span" in shown.html
    # ...and every byte of it reaches the page as markup rather than as visible tag text.
    served = str(parts.code(value=statement, syntax=Syntax.SQL, field="sql"))
    assert str(shown.html) in served
    assert "&lt;span" not in served


def test_an_attribute_is_escaped_even_when_its_value_is_already_markup() -> None:
    """htpy escapes an attribute whether or not the value is `Markup` — pinned against upgrades.

    This is the behaviour that inverts the rule everywhere else: a `Markup` child passes
    through untouched, and the same value in an attribute is escaped like any string. The whole
    attribute-position rule rests on it, so it is held here rather than remembered.
    """
    served = str(parts.code(value="SELECT 1", syntax=Syntax.SQL, field=Markup("<b>&</b>")))
    assert 'data-field="&lt;b&gt;&amp;&lt;/b&gt;"' in served
    assert "<b>&</b>" not in served


# --- The htmx vocabularies, each written once ----------------------------------------------


def test_each_swap_attribute_a_component_writes_belongs_to_a_named_vocabulary() -> None:
    """A swap vocabulary is spelled where it is defined and nowhere else.

    The composability the conversion was for, made checkable. `hx-select-oob` was written
    verbatim in three templates, and a page whose tree swapped while its pane did not is a
    reader looking at two nodes at once. Counted rather than grepped for absence: an attribute
    two vocabularies share is written twice on purpose, and the count says which.

    What this reads is the quoted spelling, so it is a check on the vocabularies rather than on
    every element: a component may still write `hx_swap=` as a keyword, and five self-replacing
    widgets do. The behavioural guard for the pane is
    `test_nav_tree__rows.py:test_every_link_that_swaps_the_pane_lands_the_pane_in_the_pane`,
    which resolves inheritance over every link that swaps it. The keyword half checked here is
    the narrow one that cannot be right: an ad-hoc `hx_push_url=` or `hx_select_oob=`.
    """
    source = "".join(path.read_text() for path in SOURCES)
    holding = Counter(name for swap in SWAPS.values() for name in swap)
    assert holding, "the package names no swap vocabulary"
    # Each attribute appears exactly as often as there are vocabularies holding it...
    for name, times in holding.items():
        assert source.count(f'"{name}"') == times, name
    # ...and every vocabulary is spread into an element, so the counts above are what the
    # pages actually carry rather than a dead constant nothing reads.
    for named in SWAPS:
        assert f"**{named}" in source, named
    # ...and no component reaches the pane's own three by their keyword spelling, which is the
    # one way past the count above: `hx_push_url=False` on a widget is a link the reader's
    # history loses, written where nothing says it is a swap.
    for name in PANE_ONLY:
        assert source.count(f"{name.replace('-', '_')}=") == 0, name


# --- The frame a page is served in --------------------------------------------------------


def test_a_dev_page_is_a_prod_page_plus_the_reload_script() -> None:
    """`dev` decides one script tag on the page and nothing else about it."""
    dev = str(
        layout.page(tab_title="hyphae", scripts=None, main=htpy.p["x"], footer=None, dev=True)
    )
    prod = str(
        layout.page(tab_title="hyphae", scripts=None, main=htpy.p["x"], footer=None, dev=False)
    )
    # Taking the tag out of the dev page leaves the prod page, byte for byte...
    assert dev.replace(DEV_TAG, "") == prod
    # ...it lands once...
    assert dev.count(DEV_TAG) == 1
    # ...and a shipped page names neither the client script nor the route it listens on.
    assert "dev-reload" not in prod
    assert "/dev/" not in prod


def test_the_frame_escapes_what_a_page_stands_inside_it() -> None:
    """A page's own markup is escaped by the frame, tab title and body alike."""
    served = str(
        layout.page(
            tab_title="<script>alert(1)</script>",
            scripts=None,
            main=htpy.p["</main><script>"],
            footer=None,
            dev=False,
        )
    )
    assert "<script>alert(1)</script>" not in served
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in served
    assert "<p>&lt;/main&gt;&lt;script&gt;</p>" in served


# --- The one thing pyrefly is not asked to check here, and its expiry ---------------------


def test_the_only_check_this_package_is_excused_is_the_one_htpy_forces() -> None:
    """One error kind, over one path, and no line-level escape anywhere under it.

    The narrowing is the whole price of the conversion's type safety, so it is pinned rather
    than trusted: a second kind turned off here, or a `# pyrefly: ignore` scattered through a
    component, would buy quiet at the cost of the thing the checker was brought in for.
    """
    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["pyrefly"]
    scoped = {sub["matches"]: sub.get("errors", {}) for sub in declared["sub-config"]}
    # Exactly the one kind, over exactly the components package...
    assert scoped[NARROWED] == UNCHECKED
    # ...and no other narrowing reaches shipped code at all.
    assert [matched for matched in scoped if matched.startswith("src/")] == [NARROWED]
    # htpy's bug reaches one page written outside the package too, and that is the whole list:
    # a third path excused for it is a component composed somewhere a component should not be.
    excused = {matched for matched, errors in scoped.items() if errors.get("bad-index") is False}
    assert excused == {NARROWED, GALLERY}
    # A per-line escape would be the other way to buy the same quiet, and none is taken.
    assert [path.name for path in SOURCES if "pyrefly: ignore" in path.read_text()] == []


def test_the_narrowing_still_has_the_reason_it_was_taken_for(tmp_path: Path) -> None:
    """The canary: pyrefly still cannot decide an htpy element nested inside another.

    Every element in this package is written inside another one, so `bad-index` off here is not
    a judgement about subscripts — it is this bug, and nothing else. When a pyrefly release
    fixes it this leaf reds, which is the only notice anyone will get that the narrowing has
    outlived its reason.
    """
    # A nesting still cannot be decided, under a project that turns nothing off...
    assert "bad-index" in checked(NESTED, tmp_path), (
        "pyrefly now decides htpy's recursive `Node` alias. Delete the "
        f"`[[tool.pyrefly.sub-config]]` matching `{NARROWED}` from `pyproject.toml`, re-run "
        "`mise run typecheck`, and delete this leaf."
    )
    # ...and the same call with a child pyrefly has never had trouble with is clean, so what
    # the assertion above read is the nesting rather than some other complaint about the file.
    assert "bad-index" not in checked(FLAT, tmp_path)
