"""The four field tables in `docs/schema.md`, written from `extract/records/`.

Run by one cog block per table — `uv run python -m tools.gen_schema identity`, and so on for the
other three — because each sits under its own heading. Every meaning and every citation comes
from the models; nothing here says what a field is.

What this module owns is the layout: which table a row appears in and in what order, since that
is a document's editorial choice rather than something the models know. `SECTIONS` is that
choice, and it is closed on both sides — a field no section names, and a section naming a field
no model documents, each stop the run.
"""

import sys
from enum import StrEnum

from aiobserve.extract.records.evidence import Cited
from aiobserve.extract.records.schema import EVERY_RECORD, Documentation, documentation, spell
from tools import text


class Section(StrEnum):
    """The four tables, named as the cog block's argument spells them."""

    IDENTITY = "identity"
    CONTENT = "content"
    API = "api"
    EVENTS = "events"


# Which table each documented field appears in, and where. The order is the reader's: a
# container, then what it holds. A field missing from here crashes rather than going unprinted.
SECTIONS: dict[Section, tuple[str, ...]] = {
    Section.IDENTITY: (
        "type",
        "subtype",
        "sessionId",
        "uuid",
        "parentUuid",
        "timestamp",
        "cwd",
        "gitBranch",
        "version",
        "entrypoint",
        "isMeta",
        "isCompactSummary",
        "isSidechain",
    ),
    Section.CONTENT: (
        "message",
        "message.content",
        "text",
        "thinking",
        "tool_use",
        "tool_use.id",
        "tool_use.name",
        "tool_use.input",
        "tool_result",
        "tool_result.tool_use_id",
        "tool_result.content",
        "tool_result.is_error",
        "server_tool_use",
        "advisor_tool_result",
        "advisor_tool_result.content",
        "content.type",
        "content.error_code",
        "content.encrypted_content",
        "fallback",
        "fallback.from",
        "from.model",
        "toolUseResult",
        "toolUseResult.persistedOutputPath",
        "toolUseResult.runId",
    ),
    Section.API: (
        "message.id",
        "message.model",
        "message.stop_reason",
        "message.usage",
        "usage.input_tokens",
        "usage.output_tokens",
        "usage.cache_read_input_tokens",
        "usage.cache_creation_input_tokens",
        "usage.cache_creation",
        "cache_creation.ephemeral_5m_input_tokens",
        "cache_creation.ephemeral_1h_input_tokens",
        "attributionSkill",
        "effort",
        "requestId",
    ),
    Section.EVENTS: (
        "durationMs",
        "compactMetadata",
        "compactMetadata.trigger",
        "compactMetadata.preTokens",
        "compactMetadata.postTokens",
        "compactMetadata.durationMs",
        "content",
        "originalModel",
        "fallbackModel",
        "choice",
        "persistedAsDefault",
        "customTitle",
        "aiTitle",
        "agentName",
        "prNumber",
        "prUrl",
        "prRepository",
        "parentSessionId",
        "parentLastUuid",
        "contextLength",
    ),
}

HEADERS = ("Field", "Records", "Meaning", "Evidence")


def documented() -> dict[str, Documentation]:
    """Every row the models write, by the name the Field column prints."""
    return {doc.path: doc for doc in documentation()}


def placed() -> dict[str, Section]:
    """Which table each field is laid out in, crashing on a field laid out twice."""
    where: dict[str, Section] = {}
    for section, paths in SECTIONS.items():
        for path in paths:
            if path in where:
                raise ValueError(f"`{path}` is in both the {where[path]} and {section} tables")
            where[path] = section
    return where


def carried(doc: Documentation) -> str:
    """The Records cell: every record that carries the field, each name in a code span.

    `every record` is prose about the set rather than a record type, so it stays bare.
    """
    return ", ".join(
        name if name == EVERY_RECORD else " / ".join(f"`{part}`" for part in name.split(" / "))
        for name in spell(doc.carriers)
    )


def sourced(cite: Cited) -> str:
    """One citation, as a reader would check it: the fixture, the version, and what it shows."""
    if cite.fixture:
        said = f"`{cite.fixture}`"
        if cite.version:
            said += f", CC {cite.version}"
        if cite.absent:
            said = f"absent from {said}"
    elif cite.scan:
        said = f"corpus scan: {cite.scan}"
    else:
        raise ValueError("a citation names neither a fixture nor a corpus scan")
    return f"{said} — {cite.note}" if cite.note else said


def cells(doc: Documentation) -> tuple[str, str, str, str]:
    """One row's four cells.

    The gate `docs/schema.md` states in prose lives here: a field with no meaning, or none with
    a recording behind it, stops the generator instead of printing an empty cell that reads as
    though someone checked.
    """
    if not doc.meaning:
        raise ValueError(f"`{doc.path}` has no description, so its Meaning cell would be blank")
    if not doc.evidence:
        raise ValueError(f"`{doc.path}` cites no recording, so its Evidence cell would be blank")
    return (
        f"`{doc.path}`",
        carried(doc),
        doc.meaning,
        "; ".join(sourced(cite) for cite in doc.evidence),
    )


def rows(section: Section) -> list[tuple[str, str, str, str]]:
    """One table's rows, in the order this module lays them out.

    Checks the whole layout, not just this table: a field this document has never placed would
    otherwise be missing from a table nobody is generating at the time.
    """
    where = placed()
    known = documented()
    for path in where:
        if path not in known:
            raise ValueError(f"the {where[path]} table names `{path}`, which no model documents")
    for path in known:
        if path not in where:
            raise ValueError(f"`{path}` is documented but in no table — add it to SECTIONS")
    return [cells(known[path]) for path in SECTIONS[section]]


def generate(section: Section) -> str:
    """One table as the cog block that names it splices it."""
    return text.table(HEADERS, rows(section))


def main() -> None:
    """Print the table named by the one argument."""
    if len(sys.argv) != 2:
        raise SystemExit(f"name one table: {' | '.join(section.value for section in Section)}")
    print(generate(Section(sys.argv[1])))


if __name__ == "__main__":
    main()
