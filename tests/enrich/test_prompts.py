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
import shutil
from pathlib import Path

import pytest

from aiobserve.enrich.prompts import (
    RUN_BUDGETS,
    SESSION_BUDGETS,
    TURN_BUDGETS,
    AgentRunItem,
    Item,
    SessionItem,
    TurnItem,
    input_hash,
    render_run,
    render_session,
    render_turn,
)
from aiobserve.enrich.store import EnrichmentStore, Stamp
from aiobserve.enrich.taxonomy import Category, Outcome
from aiobserve.enrich.validation import Enrichment
from tests.enrich.conftest import (
    AUDITOR_RUN,
    BYREF_RUN,
    ORIGIN_RUN,
    SERVER_TOOLS,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
    TEAM_RUN,
    TEAMMATE,
    WORKFLOW,
    WORKFLOW_RUN,
)

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


def run(store: EnrichmentStore, agent_run_id: str) -> AgentRunItem:
    """The store's one agent run with this id."""
    items = [item for item in store.run_items() if item.agent_run_id == agent_run_id]
    assert len(items) == 1, f"{agent_run_id} named {len(items)} runs"
    return items[0]


def session(store: EnrichmentStore, session_id: str) -> SessionItem:
    """The store's one enrichable session with this id."""
    items = [item for item in store.session_items() if item.session_id == session_id]
    assert len(items) == 1, f"{session_id} named {len(items)} sessions"
    return items[0]


def describe(store: EnrichmentStore, item: Item, description: str) -> None:
    """Enrich one item, so a render of its parent has a child description to embed."""
    store.upsert(
        item,
        Enrichment(
            description=description,
            category=Category.explore,
            outcome=Outcome.completed,
            friction=None,
        ),
        # The stamp decides re-enrichment, which no render reads.
        Stamp(input_hash="unused", prompt_version=1, taxonomy_version=1, model="fake"),
    )


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


def test_a_multi_turn_run_renders_every_instruction_in_sequence(fixture_db: Path) -> None:
    """A run renders each instruction it was given, in order, with the teammate markup gone.

    Before this the model saw an agent's replies but never what it was asked, for every run
    its lead came back to.
    """
    # If the `teammate/` architect was given a second instruction an hour after the first...
    with EnrichmentStore(fixture_db) as store:
        rendered = render_run(run(store, TEAM_RUN))
    # ...then the whole prompt is this: the run's type, its task, and then each later
    # instruction with the work it drove, in the order they happened.
    assert rendered == (
        "# Agent run: architect\n"
        "\n"
        "## Task\n"
        "[redacted]\n"
        "\n"
        "## Response\n"
        '- Read (input 27 chars, result 10 chars) {"file_path": "[redacted]"}\n'
        "\n"
        "## Instruction\n"
        "[redacted]\n"
        "\n"
        "## Response\n"
        "[redacted]\n"
        '- Bash (input 54 chars, result 10 chars) {"command": "[redacted]", "description": '
        '"[redacted]"}'
    )
    # The wrapper the transcript stores an instruction in is markup, and would read as
    # content — the attributes especially, which no other turn opener carries.
    assert "<teammate-message" not in rendered
    assert "teammate_id=" not in rendered


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


def test_a_zero_turn_run_renders_as_a_continuation(fixture_db: Path) -> None:
    """A run with no prompt of its own renders its work alone, labeled for what it is.

    All 41 zero-turn runs of the corpus are forks whose task lives in the transcript they
    continue — a render that assumes a task prompt lies about every one of them.
    """
    # If `fork_byref/`'s fork, which holds two api calls and not one turn, is rendered...
    with EnrichmentStore(fixture_db) as store:
        rendered = render_run(run(store, BYREF_RUN))
    # ...then the render says where the task went, and carries both calls in order. The
    # first is the fork's own spawning call, recorded inside the fork: a run does not embed
    # itself.
    assert rendered == (
        "# Agent run: fork\n"
        "\n"
        "## Continuation\n"
        "This run continues a conversation another transcript holds; its task is not here.\n"
        "\n"
        "## Response\n"
        '- Agent (input 84 chars, result 10 chars) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "prompt": "[redacted]"}\n'
        "\n"
        "## Response\n"
        '- Bash (input 54 chars, result 10 chars) {"command": "[redacted]", "description": '
        '"[redacted]"}'
    )


def test_a_replayed_turn_is_not_the_runs_task(fixture_db: Path) -> None:
    """A fork's copy of the turn it continues is not that fork's task.

    The renders read the `live_*` views for exactly this: over the base tables the same turn
    id appears twice, and the copy would hand the fork the auditor's prompt as its own.
    """
    # If `fork_origin/`'s auditor and the fork it spawned both hold turn `33438141…` — the
    # auditor because it ran it, the fork because forking replays it...
    with EnrichmentStore(fixture_db) as store:
        auditor = render_run(run(store, AUDITOR_RUN))
        fork = render_run(run(store, ORIGIN_RUN))
    # ...then the run that ran the turn renders it as its task...
    assert auditor.startswith("# Agent run: auditor\n\n## Task\n[redacted]\n")
    # ...and the fork renders as a continuation, with no task at all...
    assert fork.startswith("# Agent run: fork\n\n## Continuation\n")
    assert "## Task" not in fork
    # ...while still carrying its own work, error tail and unanswered call included.
    assert fork.endswith(
        "## Response\n"
        "[redacted]\n"
        '- Agent (input 84 chars, result 10 chars, ERROR) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "prompt": "[redacted]"} | error tail: [redacted]\n'
        '- Agent (input 84 chars, unanswered) {"description": "[redacted]", "subagent_type": '
        '"[redacted]", "prompt": "[redacted]"}'
    )


def test_a_spawned_run_renders_as_its_description(mutable_db: Path) -> None:
    """A run's children reach its prompt as their descriptions, never as their text."""
    # If `spine/`'s leaf run has been enriched, and its parent — the run whose `Agent` call
    # spawned it — is rendered...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, SPINE_LEAF), "Read one file and reported back.")
        rendered = render_run(run(store, SPINE_RUN))
    # ...then the spawning line carries what the child did, which is how a parent describes
    # work it never saw the text of.
    assert (
        '- Agent (input 112 chars, result 10 chars) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "run_in_background": false, "prompt": "[redacted]"}'
        " | subagent: Read one file and reported back." in rendered
    )


def test_a_spawning_call_with_no_run_renders_plainly(mutable_db: Path) -> None:
    """An `Agent` call whose run is missing renders as an ordinary tool line."""
    # If the same run is rendered with its one enriched child — its other `Agent` call
    # really spawned no run row, the subagent having left no transcript...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, SPINE_LEAF), "Read one file and reported back.")
        rendered = render_run(run(store, SPINE_RUN))
    # ...then that call is a tool line like any other, with no description slot and no
    # crash: exactly one of the two `Agent` lines carries a child.
    assert rendered.count(" | subagent: ") == 1
    assert rendered.endswith(
        '- Agent (input 112 chars, result 10 chars) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "run_in_background": false, "prompt": "[redacted]"}'
    )


def test_an_over_budget_run_drops_the_middle_of_its_work(fixture_db: Path) -> None:
    """Past its budget a run drops the middle of its call sequence and says how much went."""
    # If `spine/`'s subagent run is rendered at 300 characters, half what it needs
    # (injected — 209 of 2,458 real runs hit the real 30K cap, and no fixture comes near
    # it)...
    with EnrichmentStore(fixture_db) as store:
        elided = render_run(run(store, SPINE_RUN), dataclasses.replace(RUN_BUDGETS, total=300))
    # ...then the task and the start of the work survive, the last thing the run did
    # survives, and the gap between them counts itself.
    assert elided == (
        "# Agent run: claude\n"
        "\n"
        "## Task\n"
        "[redacted]\n"
        "\n"
        "## Response\n"
        "[redacted]\n"
        "[… 4 of 8 lines elided …]\n"
        '- Agent (input 112 chars, result 10 chars) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "run_in_background": false, "prompt": "[redacted]"}'
    )
    assert len(elided) <= 300


def test_a_session_renders_its_metrics_then_what_it_did(mutable_db: Path) -> None:
    """A session renders what it cost and a line per thing it did, in the order it did them."""
    # If every main turn of `spine/` has been described...
    with EnrichmentStore(mutable_db) as store:
        for item in store.turn_items():
            if item.session_id == SPINE:
                describe(store, item, f"Did thing {item.index}.")
        rendered = render_session(session(store, SPINE))
    # ...then the whole prompt is this: the title and branch the session recorded, the time it
    # took, what it spent, and its children as their own descriptions — never their text.
    assert rendered == (
        "# Session: fixture-title-2\n"
        "\n"
        "## Metrics\n"
        "branch fixture-branch-1\n"
        "wall 30d 23h, active 3m 39s\n"
        "tokens 11 in, 5,091 out, 115,575 cache read, 143,029 cache write\n"
        "cost $2.83\n"
        "\n"
        "## Work\n"
        # The turn recorded a month before the other three comes first: children are in
        # chronological order, which is not the order the store returns them in.
        "- Main turn [explore/completed] Did thing 3.\n"
        "- Main turn [explore/completed] Did thing 0.\n"
        "- Main turn [explore/completed] Did thing 1.\n"
        "- Main turn [explore/completed] Did thing 2."
    )


def test_a_sessions_children_are_its_turns_and_the_runs_nothing_embeds(mutable_db: Path) -> None:
    """A session carries its main turns and the runs no turn or run of its own already carries.

    Reading depth-1 runs as the children instead would drop every recorded teammate agent —
    43 of them — out of every session summary, and embed ten other runs twice.
    """
    with EnrichmentStore(mutable_db) as store:
        # If `teammate/` has one main turn and one run that no tool call spawned...
        describe(store, run(store, TEAM_RUN), "Drew up the plan.")
        for item in store.turn_items():
            if item.session_id == TEAMMATE:
                describe(store, item, "Asked for a plan.")
        rendered = render_session(session(store, TEAMMATE))
        # ...then both are children of the session, each once...
        assert rendered.endswith(
            "## Work\n"
            "- Main turn [explore/completed] Asked for a plan.\n"
            "- Agent run (architect) [explore/completed] Drew up the plan."
        )
        # ...while `spine/`'s subagent, which a main turn spawned, is not a child of the
        # session at all: it reaches the session through that turn's own description.
        describe(store, run(store, SPINE_RUN), "Ran the subagent.")
        assert "Ran the subagent." not in render_session(session(store, SPINE))


def test_a_run_spawned_outside_every_turn_is_still_a_session_child(mutable_db: Path) -> None:
    """A run whose spawning call belongs to no turn is carried by the session itself.

    Planted, but the shape is recorded: 9 runs of the corpus were spawned by a main-transcript
    call that belongs to no turn, and no turn's render can reach them. A rule keyed on the
    spawning call's *existence* rather than on what embeds the run would drop all nine.
    """
    with EnrichmentStore(mutable_db) as store:
        # If the api call that spawned `spine/`'s subagent belongs to no turn...
        store.connection.execute(
            "UPDATE api_calls SET turn_id = NULL WHERE id ="
            " (SELECT api_call_id FROM tool_calls WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS')"
        )
        describe(store, run(store, SPINE_RUN), "Ran the subagent.")
        # ...then nothing else embeds it, so the session does.
        assert "- Agent run (claude) [explore/completed] Ran the subagent." in render_session(
            session(store, SPINE)
        )


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


def test_a_workflow_line_embeds_its_spawned_run(mutable_db: Path) -> None:
    """A `Workflow` call carries its run's description, exactly as an `Agent` call does."""
    # If the run `workflow/`'s main turn started has been described...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, WORKFLOW_RUN), "Researched the question.")
        rendered = render_turn(turn(store, WORKFLOW, "cd7adeae"))
    # ...then the turn that spawned it reads what it did — the second of the two tools that
    # start a run, and the one a rule keyed on the `Agent` name alone would miss.
    assert rendered.endswith(
        '- Workflow (input 47 chars, result 10 chars) {"name": "deep-research", '
        '"args": "[redacted]"} | subagent: Researched the question.'
    )


@pytest.mark.slow  # Renders a whole real corpus — minutes, and it reads private sessions.
@pytest.mark.skipif(
    LIVE_STORE not in os.environ, reason=f"set {LIVE_STORE} to a real trace store to run"
)
def test_no_real_item_renders_past_its_budget(tmp_path: Path) -> None:
    """Every turn and run in a real store renders within the budget the enricher would send.

    The fixtures cannot show this: redaction leaves them two orders of magnitude short of
    the cap, so this is the only check that the default budgets hold on real text.
    """
    # A copy, never the store itself: `AIOBSERVE_LIVE_STORE` names the archive (`docs/store.md`)
    # and opening one runs the enrichment DDL against it. The write-ahead log comes along, or
    # the copy would be the archive as of its last checkpoint.
    archive = Path(os.environ[LIVE_STORE])
    copy = tmp_path / archive.name
    shutil.copy(archive, copy)
    wal = archive.with_name(f"{archive.name}.wal")
    if wal.exists():
        shutil.copy(wal, copy.with_name(f"{copy.name}.wal"))
    with EnrichmentStore(copy) as store:
        turn_items, run_items = store.turn_items(), store.run_items()
    assert turn_items, f"{LIVE_STORE} names a store with no turns in it"
    assert run_items, f"{LIVE_STORE} names a store with no agent runs in it"
    over = [item.key for item in turn_items if len(render_turn(item)) > TURN_BUDGETS.total]
    over += [item.key for item in run_items if len(render_run(item)) > RUN_BUDGETS.total]
    assert over == []
