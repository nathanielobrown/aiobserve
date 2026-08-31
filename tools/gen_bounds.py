"""The four bounds tables in `docs/viewer-bounds.md`: what a URL asks for, and what it weighs.

Run by four cog blocks in that document — `uv run python -m tools.gen_bounds knobs` and one
each for `bounds`, `pages` and `node` — because the tables sit in different sections. Every
number comes from `view/bounds.py`, the query manifest it composes, or the page arithmetic
`tests/view/budgets.py` holds; the words around them live here, since a size beside a ceiling
is not prose anything else already writes.

Each row names the constants it prints, in the order it prints them and one per number
(`Row.cites`), which is what lets the tests check each number against the symbol standing in
that position rather than against a literal, and what makes `UNCITED` the only place a bound
can be left out of both tables.
"""

import json
import sys
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from hyphae.analyze import queries
from hyphae.view import bounds, nodes
from hyphae.view.knobs import KNOB_DEFAULTS
from tests.view import budgets
from tools import text

ROOT = Path(__file__).resolve().parent.parent
# The same numbers as data, for the implementation that cannot import them
# (`plans/rust-prototype/full-port.md`). Derived from the modules the four tables read rather
# than from a second list, so a bound the prose leaves out is in here all the same.
REGISTRY = ROOT / "rust" / "metadata" / "bounds.json"
# What a failing staleness check tells the reader to run.
REGISTRY_ARG = "registry"
COMMAND = f"uv run python -m tools.gen_bounds {REGISTRY_ARG}"

# Where a cited name is looked up. `bounds.py` names every page size beside its ceiling and
# re-exports the widths the queries declare; a width it does not re-export is read here from
# the manifest that does declare it, so no number is copied to be printed.
# `budgets.py` is where the arithmetic itself lives, beside the measurements it multiplies,
# because the tests weigh each page against it — printing a page's worst case here would be a
# second derivation of a number the suite already enforces.
MODULES = {"bounds": bounds, "queries": queries, "budgets": budgets}


class Table(StrEnum):
    """The four tables, named as the cog block's argument spells them."""

    KNOBS = "knobs"
    BOUNDS = "bounds"
    PAGES = "pages"
    NODE = "node"


class Row(NamedTuple):
    """One row: what it is about, what it says, and what each number in it stands for.

    `cites` runs parallel to the numbers `says` spells — one name per number, in that order,
    so a swapped pair reads as a mismatch rather than as two cited numbers. A bound is cited
    by the end it prints: `bounds.RECORDS.default`, never bare `bounds.RECORDS`. A derivation
    is cited as the function that performs it, and `valued` calls it.
    """

    subject: str
    says: str
    cites: tuple[str, ...]


# What each preset of the NavTree shows, for the reader of a URL rather than of the control —
# `Preset` carries its own label there, which says which preset rather than what it does.
# A preset missing from here crashes `generate`.
PRESET_WORDS = {
    nodes.Preset.FULL: "The whole NavTree",
    nodes.Preset.NO_API: (
        "The api calls folded away, each turn's tool calls standing directly under it"
    ),
    nodes.Preset.AGENTS: (
        "The runs alone, each under the run that spawned it — the session's org chart"
    ),
}

# What each size knob narrows. The ceiling beside it is not written here: a knob is capped by
# the bound of the same name, which is where `SIZE_KNOBS` reads it.
SIZE_WORDS = {
    "kin": "Children per open level",
    "log": "Rows in one page of the reading pane's children log",
    "detail": "Characters of each value the reading pane previews",
}

# Bounds no table prints, each with the reason it is only prose. The tables cover what a
# reader can ask for and what a page holds; these three are arithmetic behind those numbers,
# and the fourth is a fetch a page triggers rather than a size anyone types.
UNCITED = {
    "OPENED_RECORD_CHARS": "how long a record the records browser opens by itself may be",
    "CURSORLESS_TURNS": "how many turn rows a level renders that no cursor reaches",
    "INDENT_CHARS": "when a JSON value is re-indented rather than served as stored",
}


def declared() -> set[str]:
    """Every page size `bounds.py` declares: a number, or a default beside its ceiling."""
    return {
        name
        for name, value in vars(bounds).items()
        if not name.startswith("_") and isinstance(value, bounds.Bound | int)
    }


def valued(name: str) -> int:
    """The one number a cited name stands for: a bound named by the end it prints, or a
    derivation named by the function that performs it."""
    module, _, rest = name.partition(".")
    symbol, _, end = rest.partition(".")
    value = getattr(MODULES[module], symbol)
    if callable(value):
        value = value()
    if isinstance(value, bounds.Bound):
        if end not in ("default", "ceiling"):
            raise ValueError(f"`{name}` is a bound: cite its `.default` or its `.ceiling`")
        return getattr(value, end)
    if end:
        raise ValueError(f"`{name}` is a plain number, so `.{end}` names nothing")
    return value


def cited() -> set[str]:
    """Every constant either table prints."""
    return {name for table in Table for row in rows(table) for name in row.cites}


def cited_bounds() -> set[str]:
    """Those of them that are `bounds.py`'s own, as it names them."""
    return {
        name.removeprefix("bounds.").split(".")[0] for name in cited() if name.startswith("bounds.")
    }


def described_preset(preset: nodes.Preset) -> str:
    """What `?nav=` set to this preset does, with the default marked as one."""
    if preset not in PRESET_WORDS:
        raise ValueError(f"preset `{preset.value}` has no words in the knob table")
    words = PRESET_WORDS[preset]
    return f"{words}. The default" if KNOB_DEFAULTS["nav"] == preset else words


def knob_rows() -> list[Row]:
    """One row per knob a node URL takes, in the order the app declares them.

    `?nav=` is a row per preset rather than one row, because the value is what a reader types.
    A size knob's ceiling is the bound of the same name — the tie that keeps a knob a reader
    can type from outrunning what the page was measured at.
    """
    listed: list[Row] = []
    for knob in KNOB_DEFAULTS:
        if knob == "nav":
            listed += [
                Row(f"?nav={preset.value}", described_preset(preset), ()) for preset in nodes.Preset
            ]
            continue
        bound = getattr(bounds, knob.upper())
        if not isinstance(bound, bounds.Bound):
            raise TypeError(f"knob `?{knob}=` has no bound named `{knob.upper()}` to cap it")
        if knob not in SIZE_WORDS:
            raise ValueError(f"knob `?{knob}=` has no words in the knob table")
        listed.append(
            Row(
                f"?{knob}=",
                f"{SIZE_WORDS[knob]}, at most {text.count(bound.ceiling)}",
                (f"bounds.{knob.upper()}.ceiling",),
            )
        )
    return listed


def bound_rows() -> list[Row]:
    """One row per surface a bound caps: what a reader who types nothing gets, and the most."""
    return [
        Row(
            "Session list",
            f"{text.count(bounds.SESSIONS.default)} sessions; each long string is cut to "
            f"{text.count(queries.LIST_CHARS)} characters, skills and agent types to "
            f"{queries.LIST_ITEMS} {queries.LIST_ITEM_CHARS}-character names, and work to "
            f"{queries.LIST_CATEGORIES}",
            (
                "bounds.SESSIONS.default",
                "queries.LIST_CHARS",
                "queries.LIST_ITEMS",
                "queries.LIST_ITEM_CHARS",
                "queries.LIST_CATEGORIES",
            ),
        ),
        Row(
            "Projects",
            f"{text.count(bounds.PROJECTS.default)} projects; the path is cut to "
            f"{text.count(queries.LIST_CHARS)} characters",
            ("bounds.PROJECTS.default", "queries.LIST_CHARS"),
        ),
        Row(
            "A session's errors",
            f"{text.count(bounds.ERRORS.default)} failed tool calls; each title is cut to "
            f"{text.count(queries.NAV_CHARS)} characters",
            ("bounds.ERRORS.default", "queries.NAV_CHARS"),
        ),
        Row(
            "NavTree",
            f"{text.count(bounds.KIN.default)} children per open level, "
            f"{text.count(bounds.DEPTH)} levels deep, each title cut to "
            f"{text.count(queries.NAV_CHARS)} characters",
            ("bounds.KIN.default", "bounds.DEPTH", "queries.NAV_CHARS"),
        ),
        Row(
            "Children log",
            f"{text.count(bounds.LOG.default)} rows a page, each string cut to "
            f"{text.count(bounds.LOG_CHARS)} characters",
            ("bounds.LOG.default", "bounds.LOG_CHARS"),
        ),
        Row(
            "Previewed value",
            f"{text.count(bounds.DETAIL.default)} characters, with the rest a fetch away",
            ("bounds.DETAIL.default",),
        ),
        Row(
            "Raw records",
            f"{text.count(bounds.RECORDS.default)} rows by default, at most "
            f"{text.count(bounds.RECORDS.ceiling)}",
            ("bounds.RECORDS.default", "bounds.RECORDS.ceiling"),
        ),
        Row(
            "Offload",
            f"{text.count(bounds.CHUNK.default)} characters by default, at most "
            f"{text.count(bounds.CHUNK.ceiling)}",
            ("bounds.CHUNK.default", "bounds.CHUNK.ceiling"),
        ),
        Row(
            "Syntax highlighting",
            f"{text.count(bounds.HIGHLIGHT_CHARS)} characters, above which the value prints "
            "as stored",
            ("bounds.HIGHLIGHT_CHARS",),
        ),
    ]


def page_rows() -> list[Row]:
    """One row per page shape: what its worst case weighs, and the ceiling it is weighed against.

    Each is the arithmetic `tests/view/budgets.py` performs and `tests/view/test_bounds.py`
    asserts, so a page that grew past its ceiling reds the suite before it reaches this table.
    """
    return [
        Row(
            "Node page",
            f"{text.count(budgets.worst_node_bytes())} of the "
            f"{text.count(budgets.NODE_BYTES)} it is allowed",
            ("budgets.worst_node_bytes", "budgets.NODE_BYTES"),
        ),
        Row(
            "Expansion",
            f"{text.count(budgets.worst_expansion_bytes())} of "
            f"{text.count(budgets.EXPANSION_BYTES)}",
            ("budgets.worst_expansion_bytes", "budgets.EXPANSION_BYTES"),
        ),
        Row(
            "Session list",
            f"{text.count(budgets.worst_session_list_bytes())} of {text.count(budgets.PAGE_BYTES)}",
            ("budgets.worst_session_list_bytes", "budgets.PAGE_BYTES"),
        ),
        Row(
            "Projects",
            f"{text.count(budgets.worst_projects_page_bytes())} of "
            f"{text.count(budgets.PAGE_BYTES)}",
            ("budgets.worst_projects_page_bytes", "budgets.PAGE_BYTES"),
        ),
        Row(
            "A session's errors",
            f"{text.count(budgets.worst_errors_page_bytes())} of {text.count(budgets.PAGE_BYTES)}",
            ("budgets.worst_errors_page_bytes", "budgets.PAGE_BYTES"),
        ),
        Row(
            "Raw records",
            f"{text.count(budgets.worst_records_page_bytes())} of {text.count(budgets.PAGE_BYTES)}",
            ("budgets.worst_records_page_bytes", "budgets.PAGE_BYTES"),
        ),
    ]


def node_rows() -> list[Row]:
    """What the widest node page is made of, in the order the page's own budget adds it up."""
    return [
        Row(
            "NavTree",
            f"{text.count(budgets.nav_tree_rows())} rows at "
            f"{text.count(bounds.NAV_TREE_ROW_BYTES)}: "
            f"{text.count(budgets.worst_nav_tree_bytes())}",
            ("budgets.nav_tree_rows", "bounds.NAV_TREE_ROW_BYTES", "budgets.worst_nav_tree_bytes"),
        ),
        Row(
            "Children log",
            f"{text.count(bounds.LOG.ceiling)} rows at "
            f"{text.count(budgets.worst_log_row_bytes())}: "
            f"{text.count(budgets.worst_log_bytes())}",
            ("bounds.LOG.ceiling", "budgets.worst_log_row_bytes", "budgets.worst_log_bytes"),
        ),
        Row(
            "Previewed values",
            f"{text.count(budgets.DEAR_PANE_DETAILS)} rendered at "
            f"{text.count(budgets.worst_rendered_detail_bytes())}: "
            f"{text.count(budgets.worst_details_bytes())}",
            (
                "budgets.DEAR_PANE_DETAILS",
                "budgets.worst_rendered_detail_bytes",
                "budgets.worst_details_bytes",
            ),
        ),
        Row(
            "Crumbs",
            f"{text.count(bounds.DEPTH)} at {text.count(budgets.worst_crumb_bytes())}: "
            f"{text.count(budgets.worst_crumbs_bytes())}",
            ("bounds.DEPTH", "budgets.worst_crumb_bytes", "budgets.worst_crumbs_bytes"),
        ),
        Row(
            "Pager",
            text.count(budgets.MEASURED_PAGER_BYTES),
            ("budgets.MEASURED_PAGER_BYTES",),
        ),
        Row(
            "Chrome",
            text.count(budgets.MEASURED_NODE_CHROME),
            ("budgets.MEASURED_NODE_CHROME",),
        ),
        Row(
            "Spare",
            text.count(budgets.node_spare()),
            ("budgets.node_spare",),
        ),
    ]


HEADERS = {
    Table.KNOBS: ("Knob", "What it does"),
    Table.BOUNDS: ("Surface", "Default and limit"),
    Table.PAGES: ("Page", "Worst case, in bytes"),
    Table.NODE: ("Part of the node page", "What it comes to, in bytes"),
}

ROWS = {
    Table.KNOBS: knob_rows,
    Table.BOUNDS: bound_rows,
    Table.PAGES: page_rows,
    Table.NODE: node_rows,
}


def rows(table: Table) -> list[Row]:
    """The rows of one table."""
    return ROWS[table]()


def generate(table: Table) -> str:
    """One table as the cog block that names it splices it."""
    # A knob is what a reader types into a URL, so that column prints as code; a bound's
    # subject is the name of a surface, and prints as prose.
    typed = table == Table.KNOBS
    return text.table(
        HEADERS[table],
        ((f"`{row.subject}`" if typed else row.subject, row.says) for row in rows(table)),
    )


def plain(module: ModuleType) -> dict[str, int]:
    """Every plain number a module declares, in the order it declares them.

    `type(value) is int` rather than `isinstance`, so a `Bound` — a `NamedTuple` of two of
    them — is not counted as one number, and neither is a flag.
    """
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_") and type(value) is int
    }


def registry() -> str:
    """Every number that bounds a page, for the implementation that cannot import them.

    Four sections, because a reader binds against all four: what a URL naming no knob is
    served at, the sizes that carry a ceiling, the ones that are a single number, and the
    widths the query library declares beside the parameters that bind them.
    """
    written = {
        "knobs": {
            knob: value.value if isinstance(value, nodes.Preset) else value
            for knob, value in KNOB_DEFAULTS.items()
        },
        "bounds": {
            name: {"default": value.default, "ceiling": value.ceiling}
            for name, value in vars(bounds).items()
            if isinstance(value, bounds.Bound)
        },
        "sizes": plain(bounds),
        "widths": plain(queries),
    }
    return json.dumps(written, indent=2) + "\n"


def write(path: Path) -> None:
    """Write the registry, creating its directory if the tree does not hold one yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(registry())


def main() -> None:
    """Print the table named by the one argument, or write the registry when it names that."""
    named = " | ".join([*(table.value for table in Table), REGISTRY_ARG])
    if len(sys.argv) != 2:
        raise SystemExit(f"name one of: {named}")
    if sys.argv[1] == REGISTRY_ARG:
        write(REGISTRY)
        print(f"wrote {REGISTRY.relative_to(ROOT)}")
        return
    print(generate(Table(sys.argv[1])))


if __name__ == "__main__":
    main()
