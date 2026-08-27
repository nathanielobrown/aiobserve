"""The two bounds tables in `docs/viewer-bounds.md`: what a URL can ask for, and what a page holds.

Run by two cog blocks in that document — `uv run python -m tools.gen_bounds knobs` and
`… bounds` — because the tables sit in different sections. Every number comes from
`view/bounds.py` or the query manifest it composes; the words around them live here, since a
size beside a ceiling is not prose anything else already writes.

Each row names the constants it prints, in the order it prints them and one per number
(`Row.cites`), which is what lets the tests check each number against the symbol standing in
that position rather than against a literal, and what makes `UNCITED` the only place a bound
can be left out of both tables.
"""

import sys
from enum import StrEnum
from typing import NamedTuple

from hyphae.analyze import queries
from hyphae.view import bounds, nodes
from hyphae.view.knobs import KNOB_DEFAULTS
from tools import text

# Where a cited name is looked up. `bounds.py` names every page size beside its ceiling and
# re-exports the widths the queries declare; a width it does not re-export is read here from
# the manifest that does declare it, so no number is copied to be printed.
MODULES = {"bounds": bounds, "queries": queries}


class Table(StrEnum):
    """The two tables, named as the cog block's argument spells them."""

    KNOBS = "knobs"
    BOUNDS = "bounds"


class Row(NamedTuple):
    """One row: what it is about, what it says, and what each number in it stands for.

    `cites` runs parallel to the numbers `says` spells — one name per number, in that order,
    so a swapped pair reads as a mismatch rather than as two cited numbers. A bound is cited
    by the end it prints: `bounds.RECORDS.default`, never bare `bounds.RECORDS`.
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
    "NAV_TREE_ROW_BYTES": "what one NavTree row weighs — the page arithmetic's multiplicand",
}


def declared() -> set[str]:
    """Every page size `bounds.py` declares: a number, or a default beside its ceiling."""
    return {
        name
        for name, value in vars(bounds).items()
        if not name.startswith("_") and isinstance(value, bounds.Bound | int)
    }


def valued(name: str) -> int:
    """The one number a cited name stands for, a bound named by the end it prints."""
    module, _, rest = name.partition(".")
    symbol, _, end = rest.partition(".")
    value = getattr(MODULES[module], symbol)
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


HEADERS = {
    Table.KNOBS: ("Knob", "What it does"),
    Table.BOUNDS: ("Surface", "Default and limit"),
}


def rows(table: Table) -> list[Row]:
    """The rows of one table."""
    return knob_rows() if table == Table.KNOBS else bound_rows()


def generate(table: Table) -> str:
    """One table as the cog block that names it splices it."""
    # A knob is what a reader types into a URL, so that column prints as code; a bound's
    # subject is the name of a surface, and prints as prose.
    typed = table == Table.KNOBS
    return text.table(
        HEADERS[table],
        ((f"`{row.subject}`" if typed else row.subject, row.says) for row in rows(table)),
    )


def main() -> None:
    """Print the table named by the one argument — `knobs` or `bounds`."""
    if len(sys.argv) != 2:
        raise SystemExit(f"name one table: {' | '.join(table.value for table in Table)}")
    print(generate(Table(sys.argv[1])))


if __name__ == "__main__":
    main()
