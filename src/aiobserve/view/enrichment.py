"""What the model said about the items on one page, or nothing at all.

Enrichment rows are written by a pass that may never have run (`docs/enrichment.md`), and the
tables themselves are created by that pass rather than by the exporter — so a store the viewer
opens read-only may not hold them. `described()` asks the catalog first and hands back an empty
answer when they are absent, which is what makes a page over an un-enriched store render the
same as a page over an item the pass has not reached yet: nothing beside the item.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

import duckdb

from aiobserve.analyze import queries
from aiobserve.analyze.queries import ParamValue
from aiobserve.enrich.prompts import PROMPT_VERSION, Level
from aiobserve.enrich.store import LEVELS
from aiobserve.enrich.taxonomy import TAXONOMY_VERSION
from aiobserve.view.store import Page, page_rows

# The enrichment tables, by the level whose rows they hold. Read off the level map rather than
# listed, so a fourth level is asked about here too.
TABLES = {level: spec.table for level, spec in LEVELS.items()}


class Described(NamedTuple):
    """One item's enrichment, as a page shows it."""

    # The turn, run or session the description is about — what keys the block on the page.
    item_id: str
    description: str
    category: str
    outcome: str
    # One line of visible struggle, or None when the model saw none.
    friction: str | None
    # Written under a prompt or taxonomy version this build no longer writes. Two of the four
    # staleness axes are invisible from a read — whether the rendered content moved needs a
    # re-render, and which model a pass would use today is the pass's own configuration — so
    # a row this leaves untagged is current on the versions and unjudged on the rest.
    stale: bool


@dataclass(frozen=True)
class Descriptions:
    """What one page's items were described as, by level, keyed by item id."""

    # Whether the store held the tables to ask at all. What a page cites is what it ran, and
    # a store with the tables and no rows in them ran the query — an empty answer is one.
    queried: bool = False
    session: Described | None = None
    turns: Mapping[str, Described] = field(default_factory=dict)
    runs: Mapping[str, Described] = field(default_factory=dict)


def enriched(connection: duckdb.DuckDBPyConnection) -> bool:
    """Whether this store holds the enrichment tables at all — a pass creates them, not the
    exporter, so a store nothing has enriched holds none of them."""
    held = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
        ).fetchall()
    }
    return set(TABLES.values()) <= held


def described(connection: duckdb.DuckDBPyConnection, session_id: str, source: str) -> Descriptions:
    """What the store says about one session, one thread's turns, and the session's runs.

    `source` is the thread the page renders — `main` on a session page, the run's id on a run
    page. An item with no row is absent from the mapping rather than present and empty, so a
    template asks `.get(id)` and gets a description or nothing.
    """
    if not enriched(connection):
        return Descriptions()
    bindings: dict[str, ParamValue] = {
        "session_id": session_id,
        "source": source,
        "description_chars": queries.ENRICHMENT_CHARS,
        "tag_chars": queries.TAG_CHARS,
    }
    by_level: dict[Level, dict[str, Described]] = {level: {} for level in Level}
    for row in page_rows(connection, Page.ENRICHMENT, **bindings):
        level = Level(row["level"])
        by_level[level][row["item_id"]] = Described(
            item_id=row["item_id"],
            description=row["description"],
            category=row["category"],
            outcome=row["outcome"],
            friction=row["friction"],
            stale=row["prompt_version"] != PROMPT_VERSION[level]
            or row["taxonomy_version"] != TAXONOMY_VERSION,
        )
    sessions = by_level[Level.session]
    return Descriptions(
        queried=True,
        session=sessions.get(session_id),
        turns=by_level[Level.turn],
        runs=by_level[Level.agent_run],
    )
