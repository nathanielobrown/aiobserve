"""What may not leave the machine, and what must.

Publishing a transcript to a third party is irreversible, so this tier sweeps the raw request
bytes rather than the parsed attributes: a stray field, a `logfire.msg` built from a prompt,
or an attribute added next month is caught without anyone remembering to update a list.

Redaction flattened every recorded string to `[redacted]` or a `fixture-*` pseudonym, so no
leaf here can assert on real transcript text. Sentinels are planted onto real rows instead.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from opentelemetry.proto.trace.v1 import trace_pb2

from aiobserve.export.duckdb import open_trace_store
from aiobserve.export.otlp import TextPolicy
from tests.conftest import MYCELIA
from tests.export.conftest import Receiver, any_value, deliver

# Every column holding text the agent or the user wrote, and a distinct planted value for
# each — distinct so a failure names the field that leaked, and every one longer than
# `TRUNCATED` so the widening leaf below can tell a truncated value from a whole one.
# Invented strings, planted onto real rows of a copied store: the recorded values were
# redacted away.
EXCLUDED = {
    ("sessions", "title"): "planted-leak-session-title",
    ("sessions", "agent_name"): "planted-leak-session-agent-name",
    ("turns", "prompt"): "planted-leak-turn-prompt",
    # The name of a slash command is structure; what the user typed after it is not.
    ("turns", "command_args"): "planted-leak-turn-command-args",
    ("api_calls", "text"): "planted-leak-api-call-text",
    ("api_calls", "thinking"): "planted-leak-api-call-thinking",
    ("tool_calls", "input"): "planted-leak-tool-call-input",
    ("tool_calls", "result"): "planted-leak-tool-call-result",
    ("agent_runs", "brief"): "planted-leak-agent-run-brief",
    ("pr_links", "pr_url"): "planted-leak-pr-link-url",
    ("pr_links", "pr_repository"): "planted-leak-pr-repository",
}


@pytest.fixture
def planted(exportable_db: Path, tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The exportable corpus with a sentinel in every excluded column of every row."""
    path = tmp_path / "planted.duckdb"
    path.write_bytes(exportable_db.read_bytes())
    connection = open_trace_store(path, read_only=False)
    for (table, column), sentinel in EXCLUDED.items():
        # A column with no rows would make its sentinel unfalsifiable, so each one is
        # checked to have landed somewhere.
        connection.execute(f'UPDATE {table} SET "{column}" = ?', [sentinel])
        assert connection.execute(
            f'SELECT count(*) FROM {table} WHERE "{column}" = ?', [sentinel]
        ).fetchone() != (0,), f"nothing to plant {sentinel} onto"
    yield connection
    connection.close()


def values(spans: list[trace_pb2.Span], key: str) -> list[Any]:
    """Every value the shipped spans carry under one attribute key."""
    return [
        any_value(attribute.value)
        for span in spans
        for attribute in span.attributes
        if attribute.key == key
    ]


def column(connection: duckdb.DuckDBPyConnection, sql: str) -> list[Any]:
    """One column of the shipped rows: the corpus under the analyzed project, live only."""
    return [row[0] for row in connection.execute(sql, [MYCELIA]).fetchall()]


def test_the_default_ship_set_carries_metadata_and_no_transcript_text(
    planted: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """No attribute carries what the user or the model wrote, and the metadata still ships."""
    # If the whole corpus is exported with a distinct sentinel in every excluded column...
    result = deliver(planted, receiver)
    assert result.extracted, "nothing was exported, so nothing below is evidence"
    # ...then not one sentinel appears anywhere in the bytes that went out — the raw payload
    # rather than the parsed attributes, so a span name or a `logfire.msg` built from a
    # prompt is caught as surely as an attribute is.
    leaked = {
        f"{table}.{column_name}"
        for (table, column_name), sentinel in EXCLUDED.items()
        if any(sentinel.encode() in body for body in receiver.bodies)
    }
    assert leaked == set()
    # ...and the metadata the analysis needs did ship, so a mapper that sends empty spans
    # cannot pass this leaf. Slice 1 ships the session, its turns and its model calls; tool
    # names, agent types and PR numbers arrive with their spans in slice 2.
    spans = receiver.spans
    assert set(values(spans, "gen_ai.request.model")) == set(
        column(
            planted,
            "SELECT DISTINCT c.model FROM api_calls c JOIN sessions s ON s.id = c.session_id"
            " WHERE s.project_dir = ? AND NOT c.replayed",
        )
    )
    assert set(values(spans, "gen_ai.response.finish_reasons")) == set(
        column(
            planted,
            "SELECT DISTINCT c.stop_reason FROM api_calls c JOIN sessions s ON s.id = c.session_id"
            " WHERE s.project_dir = ? AND NOT c.replayed AND c.stop_reason IS NOT NULL",
        )
    )
    assert set(values(spans, "claude_code.turn.command_name")) == set(
        column(
            planted,
            "SELECT DISTINCT t.command_name FROM turns t JOIN sessions s ON s.id = t.session_id"
            " WHERE s.project_dir = ? AND NOT t.replayed AND t.command_name IS NOT NULL",
        )
    )
    for attribute, stored in (
        ("gen_ai.usage.input_tokens", "input_tokens"),
        ("gen_ai.usage.output_tokens", "output_tokens"),
        ("claude_code.api_call.cost_usd", "cost_usd"),
    ):
        assert sum(values(spans, attribute)) == pytest.approx(
            sum(
                column(
                    planted,
                    f"SELECT c.{stored} FROM api_calls c JOIN sessions s ON s.id = c.session_id"
                    f" WHERE s.project_dir = ? AND NOT c.replayed AND c.{stored} IS NOT NULL",
                )
            )
        )


# The attribute key each excluded column ships under when text is opted in.
TEXT_KEYS = {
    "claude_code.session.title",
    "claude_code.session.agent_name",
    "claude_code.turn.prompt",
    "claude_code.turn.command_args",
    "claude_code.api_call.text",
    "claude_code.api_call.thinking",
    "claude_code.tool_call.input",
    "claude_code.tool_call.result",
    "claude_code.agent_run.brief",
    "claude_code.pr_link.url",
    "claude_code.pr_link.repository",
}

# Characters kept per field in the widening pass. Shorter than every sentinel, so a whole
# planted value cannot pass for a truncated one.
TRUNCATED = 20


def keys(receiver: Receiver) -> set[str]:
    """Every attribute key the payload carries — span and event alike, since PR links are
    events and a key-set sweep that read only spans would miss them."""
    return {attribute.key for span in receiver.spans for attribute in span.attributes} | {
        attribute.key
        for span in receiver.spans
        for event in span.events
        for attribute in event.attributes
    }


def test_include_text_widens_the_ship_set_by_exactly_the_named_fields(
    planted: duckdb.DuckDBPyConnection, receiver: Receiver
) -> None:
    """Opting text in adds the excluded fields and nothing else, each cut to the ceiling."""
    # If the planted corpus ships once under the default policy...
    assert deliver(planted, receiver).extracted, "nothing was exported, so nothing below holds"
    metadata = keys(receiver)
    # ...and again with text opted in and a cut far shorter than any sentinel — the delivery
    # rows cleared first, since a second pass otherwise skips what it already shipped...
    receiver.bodies.clear()
    planted.execute("DELETE FROM otlp_delivery")
    widening = TextPolicy(include=True, max_chars=TRUNCATED)
    assert deliver(planted, receiver, text=widening).extracted
    widened = keys(receiver)
    # ...then the flag adds exactly the fields the design names and drops nothing, so a field
    # added to a span next month is either metadata or is listed here...
    assert widened - metadata == TEXT_KEYS
    assert metadata - widened == set()
    # ...and every one of them arrives cut to the ceiling: truncation is not redaction, and
    # what a reader of this data gets is a prefix, never the whole recorded value.
    for sentinel in EXCLUDED.values():
        prefix = sentinel[:TRUNCATED].encode()
        assert any(prefix in body for body in receiver.bodies), f"{sentinel} never shipped"
        assert not any(sentinel.encode() in body for body in receiver.bodies), sentinel
