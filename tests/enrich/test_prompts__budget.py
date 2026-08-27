"""What never reaches a prompt, what is cut to fit, and what the hash reads.

Every fixture string outside a small structural keep-list is redacted to `[redacted]`, which
is why the exclusion tests plant a **labelled sentinel** into one field of a real row: a
render that included `thinking` and one that excluded it produce identical characters
otherwise. The same redaction is why the cap tests inject a small budget — no recorded row
comes near the real ones, which is what the two gated corpus sweeps at the bottom are for.
"""

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from aiobserve.enrich.prompts import (
    RUN_BUDGETS,
    SESSION_BUDGETS,
    TURN_BUDGETS,
    input_hash,
    render_run,
    render_session,
    render_turn,
)
from aiobserve.enrich.store import EnrichmentStore
from tests.enrich.conftest import (
    SERVER_TOOLS,
    SPINE,
    SPINE_RUN,
    TEAM_RUN,
    WORKFLOW,
)
from tests.enrich.items import (
    describe,
    ended,
    run,
    session,
    turn,
)

# A string no redacted fixture can contain, planted into one field per test.
SENTINEL = "SENTINEL-b4d1e7-content-that-must-not-travel"


def test_a_long_command_result_is_capped_and_still_ends_with_how_it_ended(
    mutable_db: Path,
) -> None:
    """A command that printed pages of output costs its budget and no more of the render.

    The body is invented and oversized: the longest recorded one is 2,038 characters, and the
    next `/context` can beat that. Rendered at the real `total`, so the cap is the subject and
    not the elision.
    """
    with EnrichmentStore(mutable_db) as store:
        # If a command printed 100,000 characters, against the recorded `/model` turn...
        store.connection.execute(
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND line_no = 8",
            [
                json.dumps(
                    {
                        "parentUuid": "5b848af7-f86e-4950-b474-cd98125fad24",
                        "type": "system",
                        "content": f"<local-command-stdout>{'x' * 100_000}</local-command-stdout>",
                    }
                ),
                SPINE,
            ],
        )
        rendered = render_turn(turn(store, SPINE, "5b848af7"))
    # ...then the block carries its budget's worth and counts what it dropped...
    assert "## Command result\nxxx" in rendered
    # ...the marker comes out of the budget rather than riding on top of it: 1,985 characters
    # of body and a 15-character marker are the 2,000 the budget allows...
    assert "[+98015 chars]" in rendered
    # ...and the render still fits and still ends by saying how the turn ended — the head is
    # protected from elision, so an unbounded body would have taken the `Ended:` line with it.
    assert len(rendered) <= TURN_BUDGETS.total
    assert ended(rendered) == "## Ended: no model response"


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
    # Four characters is all four characters of error text: the count of what was dropped
    # comes out of the budget too, and here there is no room for it.
    assert "| error tail: able" in capped


def test_the_tool_input_head_is_the_head(fixture_db: Path) -> None:
    """A tool line names what the tool was called on, by carrying the head of its input."""
    # If `workflow/`'s `Workflow` call is rendered...
    with EnrichmentStore(fixture_db) as store:
        item = turn(store, WORKFLOW, "cd7adeae")
        rendered = render_turn(item)
        # ...then the line carries the input's own first characters — the workflow's name
        # here, a file path or a command elsewhere — not a hash and not the tool name again.
        assert '{"name": "deep-research"' in rendered
        # ...and past the budget's head size it stops, saying how much it left behind. The
        # marker comes out of the budget rather than riding on top of it: nine characters of
        # input and an eleven-character marker are the twenty the budget allows.
        capped = render_turn(item, dataclasses.replace(TURN_BUDGETS, input_head=20))
    assert '- Workflow (input 47 chars, result 10 chars) {"name": [+38 chars]' in capped


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
    # budget of 240 characters, two thirds of the 346 it needs (injected, because redaction
    # leaves no fixture within two orders of magnitude of the real 30K)...
    with EnrichmentStore(fixture_db) as store:
        item = turn(store, SPINE, "30aad8e5")
        elided = render_turn(item, dataclasses.replace(TURN_BUDGETS, total=240))
    # ...then the render fits, and what it kept is the prompt, the start of the work and the
    # last thing the turn did — the two ends a description is written from. The middle went,
    # and the gap counts itself rather than reading as the whole sequence. The `Ended:` line
    # is the tail of the elidable sequence, not part of the protected head, so a budget this
    # small keeps it the same way it keeps the last tool call.
    assert elided == (
        "# Main turn\n"
        "\n"
        "## Command\n"
        "/night-run [redacted]\n"
        "\n"
        "## Command result: not recorded\n"
        "\n"
        "## Response\n"
        "[redacted]\n"
        "[… 2 of 8 lines elided …]\n"
        '- Read (input 27 chars, unanswered) {"file_path": "[redacted]"}\n'
        "\n"
        "## Ended: tool_use"
    )
    assert len(elided) <= 240


def test_each_instruction_is_capped_on_its_own(fixture_db: Path) -> None:
    """Every prompt of a run gets the whole per-prompt budget, not a share of one."""
    # If the two-instruction run is rendered at a per-prompt cap of four characters
    # (injected: redaction leaves each recorded prompt at ten, so the real 4K cannot bite)...
    with EnrichmentStore(fixture_db) as store:
        capped = render_run(run(store, TEAM_RUN), dataclasses.replace(RUN_BUDGETS, prompt=4))
    # ...then both instructions are still there, and each was truncated to four characters of
    # its own rather than to four between them.
    assert "## Task\n[red\n" in capped
    assert "## Instruction\n[red\n" in capped


def test_an_over_budget_run_drops_the_middle_of_its_work(fixture_db: Path) -> None:
    """Past its budget a run drops the middle of its call sequence and says how much went."""
    # If `spine/`'s subagent run is rendered at 300 characters, half what it needs
    # (injected — 209 of 2,458 real runs hit the real 30K cap, and no fixture comes near
    # it)...
    with EnrichmentStore(fixture_db) as store:
        elided = render_run(run(store, SPINE_RUN), dataclasses.replace(RUN_BUDGETS, total=300))
    # ...then the task and the start of the work survive, the last thing the run did
    # survives, the gap between them counts itself, and the `Ended:` line rides the tail.
    assert elided == (
        "# Agent run: claude\n"
        "\n"
        "## Task\n"
        "[redacted]\n"
        "\n"
        "## Response\n"
        "[redacted]\n"
        "[… 4 of 10 lines elided …]\n"
        '- Agent (input 112 chars, result 10 chars) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "run_in_background": false, "prompt": "[redacted]"}\n'
        "\n"
        "## Ended: not recorded"
    )
    assert len(elided) <= 300


def test_an_over_budget_session_drops_the_middle_of_its_work(mutable_db: Path) -> None:
    """Past its budget a session keeps its first and last child and says how many went."""
    # If `spine/`'s four described turns are rendered at a budget that fits two of them
    # (injected: real sessions reach 92 children, and no fixture comes near the real cap)...
    with EnrichmentStore(mutable_db) as store:
        for item in store.turn_items():
            if item.session_id == SPINE:
                describe(store, item, f"Did thing {item.index}.")
        elided = render_session(
            session(store, SPINE), dataclasses.replace(SESSION_BUDGETS, total=300)
        )
    # ...then the session keeps how it opened and how it ended, and counts what it dropped.
    assert elided.endswith(
        "## Work\n"
        "- Main turn [explore/completed] Did thing 3.\n"
        "[… 2 of 4 lines elided …]\n"
        "- Main turn [explore/completed] Did thing 2."
    )
    assert len(elided) <= 300


# Names a real trace store for the opt-in budget check below. Off by default: the store
# holds private session data.
LIVE_STORE = "AIOBSERVE_LIVE_STORE"


def live_store_copy(tmp_path: Path) -> Path:
    """A private copy of the real archive `AIOBSERVE_LIVE_STORE` names.

    Never the store itself: it is the archive (`docs/store.md`) and opening one runs the
    enrichment DDL against it. The write-ahead log comes along, or the copy would be the
    archive as of its last checkpoint.
    """
    archive = Path(os.environ[LIVE_STORE])
    copy = tmp_path / archive.name
    shutil.copy(archive, copy)
    wal = archive.with_name(f"{archive.name}.wal")
    if wal.exists():
        shutil.copy(wal, copy.with_name(f"{copy.name}.wal"))
    return copy


@pytest.mark.slow  # Renders a whole real corpus — minutes, and it reads private sessions.
@pytest.mark.skipif(
    LIVE_STORE not in os.environ, reason=f"set {LIVE_STORE} to a real trace store to run"
)
def test_no_real_item_renders_past_its_budget(tmp_path: Path) -> None:
    """Every turn and run in a real store renders within the budget the enricher would send.

    The fixtures cannot show this: redaction leaves them two orders of magnitude short of
    the cap, so this is the only check that the default budgets hold on real text — including
    the command result block, which adds up to 2,054 characters to a turn's protected head.
    """
    with EnrichmentStore(live_store_copy(tmp_path)) as store:
        turn_items, run_items = store.turn_items(), store.run_items()
    assert turn_items, f"{LIVE_STORE} names a store with no turns in it"
    assert run_items, f"{LIVE_STORE} names a store with no agent runs in it"
    over = [item.key for item in turn_items if len(render_turn(item)) > TURN_BUDGETS.total]
    over += [item.key for item in run_items if len(render_run(item)) > RUN_BUDGETS.total]
    assert over == []


@pytest.mark.slow  # Reads every recorded session, and they are private.
@pytest.mark.skipif(
    LIVE_STORE not in os.environ, reason=f"set {LIVE_STORE} to a real trace store to run"
)
def test_every_real_command_turn_is_classified(tmp_path: Path) -> None:
    """Over the whole corpus, every archived command output is read, and none is unclassifiable.

    The one place the archive read meets all the recorded sessions. The fixtures carry one
    example of each shape by construction; this says the corpus holds no other.

    Counts only, never the items: a `TurnItem` reprs as transcript content, and a failing
    assertion prints its operands.
    """
    # If the real store's turns are read — which raises on any record the shape guard cannot
    # classify, so reaching the next line is the guard's verdict on the whole corpus...
    with EnrichmentStore(live_store_copy(tmp_path)) as store:
        commands = [item for item in store.turn_items() if item.command_name is not None]
        # ...and both carriers really are in use, so the `coalesce` is load-bearing rather
        # than a branch the corpus never takes — 279 and 37 recorded instances.
        by_carrier = store.connection.execute(
            """SELECT count(*) FILTER (WHERE json_extract_string(raw, '$.message.content')
                                             LIKE '%<local-command-stdout>%'),
                      count(*) FILTER (WHERE json_extract_string(raw, '$.content')
                                             LIKE '%<local-command-stdout>%')
               FROM raw_records WHERE raw LIKE '%<local-command-stdout>%'"""
        ).fetchone()
    assert commands, f"{LIVE_STORE} names a store with no command turns in it"
    assert by_carrier is not None and min(by_carrier) > 0
    # ...then nearly every command turn the CLI answered by itself carries what it printed:
    # 272 of 280 (measured 2026-08-13), asserted as a floor rather than the count, since the
    # corpus grows. That class is the one the read serves — a turn that drove no api call has
    # nothing else to be described from. The wider population is deliberately not the
    # invariant: 143 of the 423 command turns drove the model instead (`/manager`, `/handoff`)
    # and only 39 of those archived an output, so a threshold over all 423 would measure how
    # people use slash commands rather than whether the read works.
    quiet = [item.command_result for item in commands if not item.api_calls]
    answered, quiet_turns = sum(result is not None for result in quiet), len(quiet)
    assert answered > quiet_turns * 0.95
    # ...and both recorded states are really in there: bodies, and the empty ones `/clear`
    # writes. An empty share of zero would mean the read had stopped telling them apart.
    empty = sum(result == "" for result in quiet)
    assert 0 < empty < answered
