"""What each level renders to, and the taxonomy contract every level carries.

Rows come from a real store built by running the pipeline over `tests/fixtures/`, picked by
id through `items.py`. What is cut to fit — exclusions, heads, caps, and the hash that reads
the render — is in `test_prompts__budget.py`.
"""

from pathlib import Path

from hyphae.enrich.items import Level
from hyphae.enrich.levels import LEVELS, instructions, render
from hyphae.enrich.prompts import OUTPUT_SCHEMA
from hyphae.enrich.store import EnrichmentStore
from hyphae.enrich.taxonomy import (
    CATEGORY_DEFINITIONS,
    OUTCOME_DEFINITIONS,
    TAXONOMY_VERSION,
    Category,
    Outcome,
)
from tests.conftest import MODEL_ONLY
from tests.enrich.conftest import (
    AUDITOR_RUN,
    BYREF_RUN,
    LEGACY_TITLE,
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
from tests.enrich.items import (
    describe,
    ended,
    run,
    session,
    turn,
)


def test_the_output_schema_is_the_taxonomy_the_validator_enforces() -> None:
    """The schema the model answers under names the same four fields `validate` accepts.

    It travels to `--json-schema`, so it is the first screen on the answer and the validator
    is the second. A vocabulary that drifted between them would fail every item after the
    model obeyed the prompt.
    """
    assert {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "One or two sentences"},
            # Both enums are derived from the taxonomy rather than spelled out, here and in
            # the schema, so a new member cannot reach one side alone.
            "category": {"type": "string", "enum": [str(member) for member in Category]},
            "outcome": {"type": "string", "enum": [str(member) for member in Outcome]},
            "friction": {
                "type": ["string", "null"],
                "description": "One line naming visible struggle, or null when there was none",
            },
        },
        # `friction` is required, nullable: the model must decide there was none.
        "required": ["description", "category", "outcome", "friction"],
    } == OUTPUT_SCHEMA


def test_every_level_asks_for_json_at_the_same_version() -> None:
    """All three levels are at prompt version 4 — the version that carries a command's output.

    Version 1 asked for a forced tool call through the Batches API; version 2 answered in
    JSON; version 3 said how to choose. The instructions and the schema are what `input_hash`
    cannot see, so the bump is the whole mechanism by which the corpus gets re-described under
    new guidance.
    """
    assert {level: spec.prompt_version for level, spec in LEVELS.items()} == {
        Level.turn: 4,
        Level.agent_run: 4,
        Level.session: 4,
    }
    # Both stamps in one place: a pass that bumped one and not the other would describe the
    # corpus under a mixed key, and no query could tell the halves apart afterwards.
    assert TAXONOMY_VERSION == 2
    # Every level's instructions ask for the JSON object the schema describes, and none of
    # them still names a tool to call.
    for level in Level:
        assert "Answer with one JSON object" in instructions(level)
        assert "calling" not in instructions(level)


def test_version_2_of_the_taxonomy_moved_two_borders_and_no_member() -> None:
    """The taxonomy bump rewrote what `debug` and `review` mean, and changed no member.

    That pair is the whole comparability claim: a version-1 `debug` row and a version-2 one
    count the same member under different words, so a query may add them up. A member added
    or dropped would make the two versions two different vocabularies.
    """
    # If the members are spelled out — a literal list, so an edit to `Category` must be
    # written here too rather than following silently...
    members = [
        "design",
        "implement",
        "fix_bug",
        "refactor",
        "test",
        "debug",
        "review",
        "analyze",
        "document",
        "configure",
        "vcs_ops",
        "explore",
        "chat",
        "other",
    ]
    # ...then they are the fourteen the corpus is counted by, at both doors the model meets.
    assert [str(member) for member in Category] == members
    properties = OUTPUT_SCHEMA["properties"]
    assert isinstance(properties, dict)
    assert properties["category"] == {"type": "string", "enum": members}
    # ...and the two definitions that moved say where the border between them now runs: a
    # QC pass found `debug` swallowing every review that went looking for defects.
    assert (
        "Not searching a change for defects it might have" in CATEGORY_DEFINITIONS[Category.debug]
    )
    assert CATEGORY_DEFINITIONS[Category.review].startswith("Judging a change someone else made")


def test_every_level_names_every_taxonomy_member() -> None:
    """A level's instructions carry the whole vocabulary, each member with its definition."""
    # If a member reached `OUTPUT_SCHEMA` alone, the model could answer it without ever being
    # told what it means, so every definition `taxonomy.py` holds must reach every level...
    definitions = {**CATEGORY_DEFINITIONS, **OUTCOME_DEFINITIONS}
    for level in Level:
        rendered = instructions(level)
        # ...verbatim, as the line the prompt is written from.
        for member, text in definitions.items():
            assert f"- {member}: {text}" in rendered


def test_every_level_carries_the_rules_for_choosing_between_members() -> None:
    """Each level is told how to break the ties a QC pass found the model getting wrong.

    Guidance, not vocabulary: the rules sit past the definitions, so no `TAXONOMY_VERSION`
    bump is implied and a row written under the old prompt stays comparable.
    """
    # Each rule is checked by a phrase only that rule's own wording can produce. Bare member
    # names would not do it: `implement`, `design`, `review` and `debug` are all printed by
    # the vocabulary block above, so a test spelling those passes with the rules deleted —
    # and the direction of a tie-break is the whole content of one, so the phrase carries it.
    rules = (
        "implement over design when the item produced the working thing",
        "configure for a turn the CLI handled by itself",
        "review over debug when the work judges a change someone else made",
        "end_turn means the model finished its answer",
        # The other half of that last rule, and the one a QC pass found the model reading
        # backwards: a turn whose last call asked for a tool nobody answered was cut off.
        # `tool_use` alone would not do — the render prints `## Ended: tool_use` — and
        # `abandoned` alone is printed by the vocabulary, so each phrase carries its direction.
        "tool_use means the last call asked for a tool and the records stop there",
        "abandoned, not completed",
    )
    # If each level's instructions are rendered...
    for level in Level:
        rendered = instructions(level)
        # ...then every tie-break is there, in the direction it was decided...
        for rule in rules:
            assert rule in rendered
        # ...the configure rule names all three settings commands the CLI answers by itself...
        for command in ("/model", "/effort", "/clear"):
            assert command in rendered
        # ...and every one of them sits past the last vocabulary line, so a reader looking up
        # what a member means still finds only the definition. That is what makes this
        # guidance rather than a taxonomy edit — the rules carry no `TAXONOMY_VERSION` of
        # their own, so a row written before them stays comparable to one written after.
        vocabulary_end = rendered.rindex(
            f"- {Outcome.unclear}: {OUTCOME_DEFINITIONS[Outcome.unclear]}"
        )
        for text in (*rules, "/model", "/effort", "/clear"):
            assert rendered.index(text) > vocabulary_end


def test_only_a_session_is_told_it_is_reading_other_readers_descriptions() -> None:
    """A session is told its lines are descriptions to relay, and no other level is.

    A session render is one line per child, each written by an earlier pass. A QC pass found
    the model reading them as a plan and reporting the session did what it set out to do.
    """
    # The phrase carries the direction, not the subject: "description" appears at every level
    # in the answer contract, so only the rule's own wording can tell the levels apart.
    relaying = "if a line says a cause was found, the session found a cause — it did not fix it"
    session = instructions(Level.session)
    # If a session is rendered, it carries the rule, past the last vocabulary line the way
    # every other piece of guidance does...
    assert relaying in session
    assert session.index(relaying) > session.rindex(
        f"- {Outcome.unclear}: {OUTCOME_DEFINITIONS[Outcome.unclear]}"
    )
    # ...and neither of the levels reading a transcript does: their lines are records, and
    # telling them to relay would be telling them not to read what they are looking at.
    for level in (Level.turn, Level.agent_run):
        assert relaying not in instructions(level)


def test_a_plain_main_turn_renders_its_prompt_then_its_calls(fixture_db: Path) -> None:
    """A turn renders as the prompt, then each response and the tool calls it asked for."""
    # If `spine/`'s third main turn drove four api calls — one asking for a subagent, one
    # asking for two tools at once, one notifying, and one reading a file that the session
    # ended before answering...
    with EnrichmentStore(fixture_db) as store:
        rendered = render(turn(store, SPINE, "818588ad"))
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
        '- Agent (input 115 chars, result 10 chars) {"description": "Grill doc: needs-design '
        'pair", "prompt": "[redacted]", "subagent_type": "claude", "model": "opus"}\n'
        "\n"
        "## Response\n"
        '- Bash (input 234 chars, result 10 chars) {"command": "ls -la /Users/nob/repos/mycelia'
        "/handoffs/grilling_2026_08_07_*.md /Users/nob/repos/mycelia/hand[+126 chars]\n"
        '- ToolSearch (input 54 chars, result 0 chars) {"query": "select:PushNotification", '
        '"max_results": 1}\n'
        "\n"
        "## Response\n"
        '- PushNotification (input 111 chars, result 10 chars) {"message": "Invented for this '
        'fixture: the run finished and the report is written up", "status": "[redacted]"}\n'
        "\n"
        "## Response\n"
        "[redacted]\n"
        '- Read (input 130 chars, unanswered) {"file_path": "/Users/nob/repos/mycelia/.claude'
        "/worktrees/wk-triage/issues/0069-sub-workflow-durable-identit[+22 chars]\n"
        "\n"
        "## Ended: tool_use"
    )


def test_a_turn_says_how_it_ended(fixture_db: Path) -> None:
    """Every turn ends with a line naming how the model stopped — or that it never answered.

    The one thing a render must never leave to inference. A run graded `partial` and
    "truncated mid-sentence" by the QC pass had in fact stopped `end_turn`; the render had
    simply not said so, and a missing section is what the model reads absence from.
    """
    with EnrichmentStore(fixture_db) as store:
        # If a turn's last api call recorded why generation stopped, that value is the last
        # line, verbatim — `end_turn` is the one the fix exists for, and `stop_sequence` is
        # the rarest of the recorded values...
        assert ended(render(turn(store, LEGACY_TITLE, "7d30c171"))) == "## Ended: end_turn"
        assert ended(render(turn(store, SPINE, "8cdceb31"))) == "## Ended: stop_sequence"
        # ...if it recorded none, the line says so rather than going missing: `server_tools/`'s
        # turn made three calls, stopping `end_turn`, `tool_use` and NULL in that order, so the
        # null is the one that decides...
        assert ended(render(turn(store, SERVER_TOOLS, "9ae45aaa"))) == "## Ended: not recorded"
        # ...and a turn the model never answered at all says that, which is the whole fix for
        # turns: `/model` and `/coordinator` are handled by the CLI, and turns are not gated.
        for session_id, prefix in (
            (SPINE, "5b848af7"),
            (TEAMMATE, "97d6f3d4"),
            (MODEL_ONLY, "264ef04d"),
        ):
            assert ended(render(turn(store, session_id, prefix))) == ("## Ended: no model response")


def test_a_run_says_how_it_ended_once(fixture_db: Path) -> None:
    """A run ends with one line saying how it stopped, after its last section — not per response."""
    with EnrichmentStore(fixture_db) as store:
        # If a run's calls recorded no stop reason — `spine/`'s outer run made two, both
        # NULL — then the run says so once, after everything it did...
        outer = render(run(store, SPINE_RUN))
        assert ended(outer) == "## Ended: not recorded"
        # ...and a run whose last call recorded one carries it verbatim, also once. 51 of the
        # 69 recorded stop reasons are `tool_use`, which is why the design put the line at the
        # end rather than beside every response.
        leaf = render(run(store, SPINE_LEAF))
        assert ended(leaf) == "## Ended: tool_use"
        for rendered in (outer, leaf):
            assert rendered.count("## Ended:") == 1


def test_a_slash_turn_renders_the_command_not_its_tags(fixture_db: Path) -> None:
    """A slash command renders as the command it ran, never as the tag markup it was stored as."""
    # If both of `spine/`'s slash turns are rendered — one recorded leading with
    # `<command-name>`, one with `<command-message>`, since both orderings occur...
    with EnrichmentStore(fixture_db) as store:
        first = render(turn(store, SPINE, "5b848af7"))
        second = render(turn(store, SPINE, "30aad8e5"))
    # ...then each names its command...
    assert "## Command\n/model [redacted]" in first
    assert "## Command\n/night-run [redacted]" in second
    # ...and neither spends budget on markup that would read as content.
    for rendered in (first, second):
        assert "<command-name>" not in rendered
        assert "<command-message>" not in rendered


def test_a_command_turn_carries_what_the_cli_printed(fixture_db: Path) -> None:
    """A slash command's own output travels with it, and a turn with none says so.

    Most command turns drive no model response at all, so before this the render said what
    was asked and nothing about what happened — and a reader with no answer in front of it
    infers one. `/model` reads as a question the session never got an answer to.
    """
    with EnrichmentStore(fixture_db) as store:
        # If `spine/`'s `/model` turn is rendered — the CLI answered it, and the archive kept
        # what it printed — then the whole prompt is this: the answer sits in the head beside
        # the command, ahead of the work, and the `Ended:` line still ends the render.
        assert render(turn(store, SPINE, "5b848af7")) == (
            "# Main turn\n"
            "\n"
            "## Command\n"
            "/model [redacted]\n"
            "\n"
            "## Command result\n"
            "[redacted]\n"
            "\n"
            "## Ended: no model response"
        )
        # ...if the record that answered it printed nothing — `/clear` always does — the
        # render says so in its own words...
        assert "## Command result: the command printed nothing" in (
            render(turn(store, MODEL_ONLY, "144af379"))
        )
        # ...and if nothing archived an answer — `/night-run` is the one recorded turn in
        # that state — the render says that instead. The two read alike and mean opposite
        # things, which is why one test renders both.
        assert "## Command result: not recorded" in render(turn(store, SPINE, "30aad8e5"))


def test_a_multi_turn_run_renders_every_instruction_in_sequence(fixture_db: Path) -> None:
    """A run renders each instruction it was given, in order, with the teammate markup gone.

    Before this the model saw an agent's replies but never what it was asked, for every run
    its lead came back to.
    """
    # If the `teammate/` architect was given a second instruction an hour after the first...
    with EnrichmentStore(fixture_db) as store:
        rendered = render(run(store, TEAM_RUN))
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
        '"[redacted]"}\n'
        "\n"
        "## Ended: tool_use"
    )
    # The wrapper the transcript stores an instruction in is markup, and would read as
    # content — the attributes especially, which no other turn opener carries.
    assert "<teammate-message" not in rendered
    assert "teammate_id=" not in rendered


def test_a_zero_turn_run_renders_as_a_continuation(fixture_db: Path) -> None:
    """A run with no prompt of its own renders its work alone, labeled for what it is.

    All 41 zero-turn runs of the corpus are forks whose task lives in the transcript they
    continue — a render that assumes a task prompt lies about every one of them.
    """
    # If `fork_byref/`'s fork, which holds two api calls and not one turn, is rendered...
    with EnrichmentStore(fixture_db) as store:
        rendered = render(run(store, BYREF_RUN))
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
        '"[redacted]"}\n'
        "\n"
        "## Ended: tool_use"
    )


def test_a_replayed_turn_is_not_the_runs_task(fixture_db: Path) -> None:
    """A fork's copy of the turn it continues is not that fork's task.

    The renders read the `live_*` views for exactly this: over the base tables the same turn
    id appears twice, and the copy would hand the fork the auditor's prompt as its own.
    """
    # If `fork_origin/`'s auditor and the fork it spawned both hold turn `33438141…` — the
    # auditor because it ran it, the fork because forking replays it...
    with EnrichmentStore(fixture_db) as store:
        auditor = render(run(store, AUDITOR_RUN))
        fork = render(run(store, ORIGIN_RUN))
    # ...then the run that ran the turn renders it as its task...
    assert auditor.startswith("# Agent run: auditor\n\n## Task\n[redacted]\n")
    # ...and the fork renders as a continuation, with no task at all...
    assert fork.startswith("# Agent run: fork\n\n## Continuation\n")
    assert "## Task" not in fork
    # ...while still carrying its own work, error tail and unanswered call included, under the
    # line that says how it ended.
    assert fork.endswith(
        "## Response\n"
        "[redacted]\n"
        '- Agent (input 84 chars, result 10 chars, ERROR) {"description": "[redacted]", '
        '"subagent_type": "[redacted]", "prompt": "[redacted]"} | error tail: [redacted]\n'
        '- Agent (input 84 chars, unanswered) {"description": "[redacted]", "subagent_type": '
        '"[redacted]", "prompt": "[redacted]"}\n'
        "\n"
        "## Ended: tool_use"
    )


def test_a_spawned_run_renders_as_its_description(mutable_db: Path) -> None:
    """A run's children reach its prompt as their descriptions, never as their text."""
    # If `spine/`'s leaf run has been enriched, and its parent — the run whose `Agent` call
    # spawned it — is rendered...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, SPINE_LEAF), "Read one file and reported back.")
        rendered = render(run(store, SPINE_RUN))
    # ...then the spawning line carries what the child did, which is how a parent describes
    # work it never saw the text of.
    assert (
        '- Agent (input 131 chars, result 10 chars) {"description": "Research 0149 '
        'multi-instance pg0", "subagent_type": "Explore", "run_in_background": false, '
        "[+23 chars] | subagent: Read one file and reported back." in rendered
    )


def test_a_spawning_call_with_no_run_renders_plainly(mutable_db: Path) -> None:
    """An `Agent` call whose run is missing renders as an ordinary tool line."""
    # If the same run is rendered with its one enriched child — its other `Agent` call
    # really spawned no run row, the subagent having left no transcript...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, SPINE_LEAF), "Read one file and reported back.")
        rendered = render(run(store, SPINE_RUN))
    # ...then that call is a tool line like any other, with no description slot and no
    # crash: exactly one of the two `Agent` lines carries a child.
    assert rendered.count(" | subagent: ") == 1
    assert rendered.endswith(
        '- Agent (input 132 chars, result 10 chars) {"description": "Research 0155 '
        'data-edge semantics", "subagent_type": "Explore", "run_in_background": false,'
        "[+24 chars]\n"
        "\n"
        "## Ended: not recorded"
    )


def test_a_session_renders_its_metrics_then_what_it_did(mutable_db: Path) -> None:
    """A session renders what it cost and a line per thing it did, in the order it did them."""
    # If every main turn of `spine/` has been described...
    with EnrichmentStore(mutable_db) as store:
        for item in store.turn_items():
            if item.session_id == SPINE:
                describe(store, item, f"Did thing {item.index}.")
        rendered = render(session(store, SPINE))
    # ...then the whole prompt is this: the title and branch the session recorded, the time it
    # took, what it spent, and its children as their own descriptions — never their text.
    assert rendered == (
        "# Session: fixture-title-2\n"
        "\n"
        "## Metrics\n"
        "branch fixture-branch-1\n"
        "wall 30d 23h, active 3m 39s\n"
        "tokens 17 in, 5,846 out, 362,120 cache read, 145,722 cache write\n"
        "cost $3.17\n"
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
        rendered = render(session(store, TEAMMATE))
        # ...then both are children of the session, each once...
        assert rendered.endswith(
            "## Work\n"
            "- Main turn [explore/completed] Asked for a plan.\n"
            "- Agent run (architect) [explore/completed] Drew up the plan."
        )
        # ...while `spine/`'s subagent, which a main turn spawned, is not a child of the
        # session at all: it reaches the session through that turn's own description.
        describe(store, run(store, SPINE_RUN), "Ran the subagent.")
        assert "Ran the subagent." not in render(session(store, SPINE))


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
        assert "- Agent run (claude) [explore/completed] Ran the subagent." in render(
            session(store, SPINE)
        )


def test_a_workflow_line_embeds_its_spawned_run(mutable_db: Path) -> None:
    """A `Workflow` call carries its run's description, exactly as an `Agent` call does."""
    # If the run `workflow/`'s main turn started has been described...
    with EnrichmentStore(mutable_db) as store:
        describe(store, run(store, WORKFLOW_RUN), "Researched the question.")
        rendered = render(turn(store, WORKFLOW, "cd7adeae"))
    # ...then the turn that spawned it reads what it did — the second of the two tools that
    # start a run, and the one a rule keyed on the `Agent` name alone would miss.
    assert rendered.endswith(
        '- Workflow (input 47 chars, result 10 chars) {"name": "deep-research", '
        '"args": "[redacted]"} | subagent: Researched the question.\n'
        "\n"
        "## Ended: tool_use"
    )
