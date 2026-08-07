"""What a turn renders to, what never reaches the prompt, and what the hash reads.

Rows come from a real store built by running the pipeline over `tests/fixtures/`. Every
fixture string outside a small structural keep-list is redacted to `[redacted]`, which is
why the exclusion tests plant a **labelled sentinel** into one field of a real row: a render
that included `thinking` and one that excluded it produce identical characters otherwise.
The same redaction is why the cap tests inject a small budget — no recorded row comes near
the real ones.
"""

import dataclasses
import os
from pathlib import Path

import pytest

from aiobserve.enrich.prompts import TURN_BUDGETS, TurnItem, input_hash, render_turn
from aiobserve.enrich.store import EnrichmentStore
from tests.enrich.conftest import SERVER_TOOLS, SPINE, WORKFLOW

# A string no redacted fixture can contain, planted into one field per test.
SENTINEL = "SENTINEL-b4d1e7-content-that-must-not-travel"

# Names a real trace store for the opt-in budget check below. Off by default: the store
# holds private session data.
LIVE_STORE = "AIOBSERVE_LIVE_STORE"


def turn(store: EnrichmentStore, session_id: str, prefix: str) -> TurnItem:
    """The one main turn of `session_id` whose id starts with `prefix`."""
    items = [
        item
        for item in store.turn_items()
        if item.session_id == session_id and item.turn_id.startswith(prefix)
    ]
    assert len(items) == 1, f"{prefix} named {len(items)} turns"
    return items[0]


def test_a_plain_main_turn_renders_its_prompt_then_its_calls(fixture_db: Path) -> None:
    """A turn renders as the prompt, then each response and the tool calls it asked for."""
    # If `spine/`'s third main turn drove two api calls — one asking for a subagent, one
    # reading a file that the session ended before answering...
    with EnrichmentStore(fixture_db) as store:
        rendered = render_turn(turn(store, SPINE, "818588ad"))
    # ...then the whole prompt is this, with every field's presence, order and label visible:
    # the response text capped but present, and one line per tool call carrying its name, the
    # size of what went in, the size of what came back, and the head of the input.
    assert rendered == (
        "# Main turn\n"
        "\n"
        "## Prompt\n"
        "[redacted]\n"
        "\n"
        "## Response\n"
        '- Agent (input 101 chars, result 10 chars) {"description": "[redacted]", '
        '"prompt": "[redacted]", "subagent_type": "[redacted]", "model": "opus"}\n'
        "\n"
        "## Response\n"
        "[redacted]\n"
        '- Read (input 27 chars, unanswered) {"file_path": "[redacted]"}'
    )


def test_a_slash_turn_renders_the_command_not_its_tags(fixture_db: Path) -> None:
    """A slash command renders as the command it ran, never as the tag markup it was stored as."""
    # If both of `spine/`'s slash turns are rendered — one recorded leading with
    # `<command-name>`, one with `<command-message>`, since both orderings occur...
    with EnrichmentStore(fixture_db) as store:
        first = render_turn(turn(store, SPINE, "5b848af7"))
        second = render_turn(turn(store, SPINE, "30aad8e5"))
    # ...then each names its command...
    assert "## Command\n/model [redacted]" in first
    assert "## Command\n/night-run [redacted]" in second
    # ...and neither spends budget on markup that would read as content.
    for rendered in (first, second):
        assert "<command-name>" not in rendered
        assert "<command-message>" not in rendered


def test_thinking_reaches_no_prompt(mutable_db: Path) -> None:
    """Extended thinking is excluded from every prompt, whatever it holds."""
    # If a sentinel is planted into the thinking of a real `spine/` api call — invented
    # content in a recorded row, because redaction leaves every real string identical...
    with EnrichmentStore(mutable_db) as store:
        store.connection.execute(
            "UPDATE api_calls SET thinking = ? WHERE session_id = ?", [SENTINEL, SPINE]
        )
        # ...then no turn of that session carries it: 30.5 MB corpus-wide, and the cost
        # estimate assumes it is gone.
        for item in store.turn_items():
            if item.session_id == SPINE:
                assert SENTINEL not in render_turn(item)


def test_a_tool_result_reaches_no_prompt_but_its_size_does(mutable_db: Path) -> None:
    """A successful tool's output never travels — only how big it was."""
    # If a sentinel is planted into the result of a real, non-error `spine/` tool call...
    with EnrichmentStore(mutable_db) as store:
        store.connection.execute(
            "UPDATE tool_calls SET result = ? WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS'",
            [SENTINEL],
        )
        rendered = render_turn(turn(store, SPINE, "818588ad"))
    # ...then the prompt carries none of it — results are 390 MB corpus-wide, and including
    # them would dominate every prompt...
    assert SENTINEL not in rendered
    # ...but it does carry the length of that same column, which is the one-number signal
    # behind every context-bloat finding.
    assert f"result {len(SENTINEL)} chars" in rendered


def test_an_error_result_tail_is_the_one_exception(fixture_db: Path) -> None:
    """A failed tool call carries the tail of its error, which is where friction shows."""
    # If `server_tools/`'s one recorded failing call is rendered...
    with EnrichmentStore(fixture_db) as store:
        item = turn(store, SERVER_TOOLS, "9ae45aaa")
        rendered = render_turn(item)
        # ...then its line is flagged and carries the error text...
        assert "- advisor (input 2 chars, result 11 chars, ERROR) {} | error tail: unavailable" in (
            rendered
        )
        # ...and the tail is a tail: capped at the budget's size, the *end* of the message
        # survives. Injected small, since no recorded error runs to the real 300 chars.
        capped = render_turn(item, dataclasses.replace(TURN_BUDGETS, error_tail=4))
    assert "| error tail: [+7 chars]able" in capped


def test_the_tool_input_head_is_the_head(fixture_db: Path) -> None:
    """A tool line names what the tool was called on, by carrying the head of its input."""
    # If `workflow/`'s `Workflow` call is rendered...
    with EnrichmentStore(fixture_db) as store:
        item = turn(store, WORKFLOW, "cd7adeae")
        rendered = render_turn(item)
        # ...then the line carries the input's own first characters — the workflow's name
        # here, a file path or a command elsewhere — not a hash and not the tool name again.
        assert '{"name": "deep-research"' in rendered
        # ...and past the budget's head size it stops, saying how much it left behind.
        capped = render_turn(item, dataclasses.replace(TURN_BUDGETS, input_head=20))
    assert '{"name": "deep-resea[+27 chars]' in capped


def test_input_hash_reads_the_rendered_content_and_nothing_else(mutable_db: Path) -> None:
    """The staleness hash moves when the prompt does, and only then."""
    with EnrichmentStore(mutable_db) as store:
        # If the same turn is rendered twice, the hash is the same...
        before = input_hash(render_turn(turn(store, SPINE, "818588ad")))
        assert before == input_hash(render_turn(turn(store, SPINE, "818588ad")))
        # ...if a field the render reads changes — a tool call's name...
        store.connection.execute(
            "UPDATE tool_calls SET name = 'Grep' WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS'"
        )
        renamed = input_hash(render_turn(turn(store, SPINE, "818588ad")))
        # ...then the hash moves, so the turn re-enriches...
        assert renamed != before
        # ...and if a field the render does not read changes, it does not, so a re-extract
        # that changed no text re-buys nothing.
        store.connection.execute(
            "UPDATE api_calls SET request_id = 'req_rewritten' WHERE session_id = ?", [SPINE]
        )
        assert input_hash(render_turn(turn(store, SPINE, "818588ad"))) == renamed


def test_an_over_budget_turn_drops_the_middle_of_its_work(fixture_db: Path) -> None:
    """Past its budget a turn drops the middle of its call sequence and says how much went."""
    # If `spine/`'s longest turn — three tool calls under one response — is rendered at a
    # budget of 200 characters, two thirds of what it needs (injected, because redaction
    # leaves no fixture within two orders of magnitude of the real 30K)...
    with EnrichmentStore(fixture_db) as store:
        item = turn(store, SPINE, "30aad8e5")
        elided = render_turn(item, dataclasses.replace(TURN_BUDGETS, total=200))
    # ...then the render fits, and what it kept is the prompt, the start of the work and the
    # last thing the turn did — the two ends a description is written from. The middle went,
    # and the gap counts itself rather than reading as the whole sequence.
    assert elided == (
        "# Main turn\n"
        "\n"
        "## Command\n"
        "/night-run [redacted]\n"
        "\n"
        "## Response\n"
        "[redacted]\n"
        "[… 2 of 6 lines elided …]\n"
        '- Read (input 27 chars, unanswered) {"file_path": "[redacted]"}'
    )
    assert len(elided) <= 200


@pytest.mark.slow  # Renders a whole real corpus — minutes, and it reads private sessions.
@pytest.mark.skipif(
    LIVE_STORE not in os.environ, reason=f"set {LIVE_STORE} to a real trace store to run"
)
def test_no_real_turn_renders_past_its_budget() -> None:
    """Every turn in a real store renders within the budget the enricher would send.

    The fixtures cannot show this: redaction leaves them two orders of magnitude short of
    the cap, so this is the only check that the default budgets hold on real text.
    """
    with EnrichmentStore(Path(os.environ[LIVE_STORE])) as store:
        items = store.turn_items()
    assert items, f"{LIVE_STORE} names a store with no turns in it"
    over = [item.key for item in items if len(render_turn(item)) > TURN_BUDGETS.total]
    assert over == []
