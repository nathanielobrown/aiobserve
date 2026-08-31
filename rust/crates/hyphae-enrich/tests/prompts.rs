//! What each level renders to, and the taxonomy contract every level carries.
//!
//! Ported from `tests/enrich/test_prompts.py`. Rows come from the cached fixture corpus,
//! picked by id through [`common`]; what is cut to fit is in `prompts_budget.rs`.

use hyphae_enrich::prompts::{
    OUTPUT_SCHEMA, RUN_BUDGETS, SESSION_BUDGETS, TURN_BUDGETS, instructions, render_run,
    render_session, render_turn,
};
use hyphae_enrich::{Level, taxonomy};
use serde_json::json;

mod common;

use common::{
    BYREF_FORK, DEEP_RESEARCH_SESSION, FORK_ORIGIN_RUN, FORK_RUN, MODEL_ONLY, SERVER_TOOLS, SPINE,
    SPINE_LEAF, SPINE_RUN, TEAMMATE, TEAMMATE_RUN, WORKFLOW_AGENT, WORKTREE_SESSION, describe,
    ended, open_copy, run, session, turn,
};

/// The schema the model answers under names the same four fields `validate` accepts.
///
/// It travels to `--json-schema`, so it is the first screen on the answer and the validator is
/// the second. A vocabulary that drifted between them would fail every item after the model
/// obeyed the prompt.
#[test]
fn the_output_schema_is_the_taxonomy_the_validator_enforces() {
    let vocabulary = taxonomy::enrichment();
    assert_eq!(
        *OUTPUT_SCHEMA,
        json!({
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "One or two sentences"},
                // Both enums are derived from the taxonomy rather than spelled out, here and
                // in the schema, so a new member cannot reach one side alone.
                "category": {"type": "string", "enum": vocabulary.categories},
                "outcome": {"type": "string", "enum": vocabulary.outcomes},
                "friction": {
                    "type": ["string", "null"],
                    "description":
                        "One line naming visible struggle, or null when there was none",
                },
            },
            // `friction` is required, nullable: the model must decide there was none.
            "required": ["description", "category", "outcome", "friction"],
        })
    );
}

/// All three levels are at prompt version 4 — the version that carries a command's output.
///
/// Version 1 asked for a forced tool call through the Batches API; version 2 answered in JSON;
/// version 3 said how to choose. The instructions and the schema are what `input_hash` cannot
/// see, so the bump is the whole mechanism by which the corpus gets re-described.
#[test]
fn every_level_asks_for_json_at_the_same_version() {
    for level in Level::ALL {
        assert_eq!(level.prompt_version(), 4, "{level}");
    }
    // Both stamps in one place: a pass that bumped one and not the other would describe the
    // corpus under a mixed key, and no query could tell the halves apart afterwards.
    assert_eq!(taxonomy::enrichment().taxonomy_version, 2);
    // Every level's instructions ask for the JSON object the schema describes, and none of
    // them still names a tool to call.
    for level in Level::ALL {
        assert!(
            instructions(level).contains("Answer with one JSON object"),
            "{level}"
        );
        assert!(!instructions(level).contains("calling"), "{level}");
    }
}

/// The taxonomy bump rewrote what `debug` and `review` mean, and changed no member.
///
/// That pair is the whole comparability claim: a version-1 `debug` row and a version-2 one
/// count the same member under different words, so a query may add them up. A member added or
/// dropped would make the two versions two different vocabularies.
#[test]
fn version_2_of_the_taxonomy_moved_two_borders_and_no_member() {
    // If the members are spelled out — a literal list, so an edit to `Category` must be
    // written here too rather than following silently...
    let members = [
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
    ];
    let vocabulary = taxonomy::enrichment();
    // ...then they are the fourteen the corpus is counted by, at both doors the model meets.
    assert_eq!(vocabulary.categories, members);
    assert_eq!(
        OUTPUT_SCHEMA["properties"]["category"],
        json!({"type": "string", "enum": members})
    );
    // ...and the two definitions that moved say where the border between them now runs: a QC
    // pass found `debug` swallowing every review that went looking for defects.
    assert!(
        taxonomy::definition(&vocabulary.category_definitions, "debug")
            .contains("Not searching a change for defects it might have")
    );
    assert!(
        taxonomy::definition(&vocabulary.category_definitions, "review")
            .starts_with("Judging a change someone else made")
    );
}

/// A level's instructions carry the whole vocabulary, each member with its definition.
#[test]
fn every_level_names_every_taxonomy_member() {
    let vocabulary = taxonomy::enrichment();
    // If a member reached `OUTPUT_SCHEMA` alone, the model could answer it without ever being
    // told what it means, so every definition the taxonomy holds must reach every level...
    let definitions = vocabulary
        .category_definitions
        .iter()
        .chain(&vocabulary.outcome_definitions);
    for level in Level::ALL {
        let rendered = instructions(level);
        // ...verbatim, as the line the prompt is written from.
        for (member, text) in definitions.clone() {
            assert!(
                rendered.contains(&format!("- {member}: {text}")),
                "{level} {member}"
            );
        }
    }
}

/// Each level is told how to break the ties a QC pass found the model getting wrong.
///
/// Guidance, not vocabulary: the rules sit past the definitions, so no taxonomy bump is implied
/// and a row written under the old prompt stays comparable.
#[test]
fn every_level_carries_the_rules_for_choosing_between_members() {
    // Each rule is checked by a phrase only that rule's own wording can produce. Bare member
    // names would not do it: `implement`, `design`, `review` and `debug` are all printed by
    // the vocabulary block above, so a test spelling those passes with the rules deleted —
    // and the direction of a tie-break is the whole content of one, so the phrase carries it.
    let rules = [
        "implement over design when the item produced the working thing",
        "configure for a turn the CLI handled by itself",
        "review over debug when the work judges a change someone else made",
        "end_turn means the model finished its answer",
        // The other half of that last rule, and the one a QC pass found the model reading
        // backwards: a turn whose last call asked for a tool nobody answered was cut off.
        // `tool_use` alone would not do — the render prints `## Ended: tool_use` — and
        // `abandoned` alone is printed by the vocabulary, so each phrase carries its direction.
        "tool_use means the last call asked for a tool and the records stop there",
        "abandoned, not completed",
    ];
    let vocabulary = taxonomy::enrichment();
    let last_line = format!(
        "- unclear: {}",
        taxonomy::definition(&vocabulary.outcome_definitions, "unclear")
    );
    // If each level's instructions are rendered...
    for level in Level::ALL {
        let rendered = instructions(level);
        // ...then every tie-break is there, in the direction it was decided...
        for rule in rules {
            assert!(rendered.contains(rule), "{level}: {rule}");
        }
        // ...the configure rule names all three settings commands the CLI answers itself...
        // ...and every one of them sits past the last vocabulary line, so a reader looking up
        // what a member means still finds only the definition. That is what makes this
        // guidance rather than a taxonomy edit — the rules carry no version of their own, so a
        // row written before them stays comparable to one written after.
        let vocabulary_end = rendered
            .rfind(&last_line)
            .expect("the last definition is printed");
        for text in rules.iter().chain(&["/model", "/effort", "/clear"]) {
            let at = rendered
                .find(text)
                .unwrap_or_else(|| panic!("{level}: {text}"));
            assert!(at > vocabulary_end, "{level}: {text}");
        }
    }
}

/// A session is told its lines are descriptions to relay, and no other level is.
///
/// A session render is one line per child, each written by an earlier pass. A QC pass found the
/// model reading them as a plan and reporting the session did what it set out to do.
#[test]
fn only_a_session_is_told_it_is_reading_other_readers_descriptions() {
    // The phrase carries the direction, not the subject: "description" appears at every level
    // in the answer contract, so only the rule's own wording can tell the levels apart.
    let relaying =
        "if a line says a cause was found, the session found a cause — it did not fix it";
    let vocabulary = taxonomy::enrichment();
    let last_line = format!(
        "- unclear: {}",
        taxonomy::definition(&vocabulary.outcome_definitions, "unclear")
    );
    let session = instructions(Level::Session);
    // If a session is rendered, it carries the rule, past the last vocabulary line the way
    // every other piece of guidance does...
    assert!(session.contains(relaying));
    assert!(
        session.find(relaying).expect("the rule is printed")
            > session
                .rfind(&last_line)
                .expect("the last definition is printed")
    );
    // ...and neither of the levels reading a transcript does: their lines are records, and
    // telling them to relay would be telling them not to read what they are looking at.
    for level in [Level::Turn, Level::AgentRun] {
        assert!(!instructions(level).contains(relaying), "{level}");
    }
}

/// A turn renders as the prompt, then each response and the tool calls it asked for.
#[test]
fn a_plain_main_turn_renders_its_prompt_then_its_calls() {
    // If `spine/`'s third main turn drove four api calls — one asking for a subagent, one
    // asking for two tools at once, one notifying, and one reading a file that the session
    // ended before answering...
    let (_scratch, store) = open_copy();
    let rendered = render_turn(&turn(&store, SPINE, "818588ad"), &TURN_BUDGETS);
    // ...then the whole prompt is this, with every field's presence, order and label visible:
    // the response text capped but present, and one line per tool call carrying its name, the
    // size of what went in, the size of what came back, and the head of the input.
    assert_eq!(
        rendered,
        "\
# Main turn

## Prompt
[redacted]

## Response
- Agent (input 115 chars, result 10 chars) {\"description\": \"Grill doc: needs-design pair\", \
\"prompt\": \"[redacted]\", \"subagent_type\": \"claude\", \"model\": \"opus\"}

## Response
- Bash (input 234 chars, result 10 chars) {\"command\": \"ls -la /Users/nob/repos/mycelia\
/handoffs/grilling_2026_08_07_*.md /Users/nob/repos/mycelia/hand[+126 chars]
- ToolSearch (input 54 chars, result 0 chars) {\"query\": \"select:PushNotification\", \
\"max_results\": 1}

## Response
- PushNotification (input 111 chars, result 10 chars) {\"message\": \"Invented for this \
fixture: the run finished and the report is written up\", \"status\": \"[redacted]\"}

## Response
[redacted]
- Read (input 130 chars, unanswered) {\"file_path\": \"/Users/nob/repos/mycelia/.claude\
/worktrees/wk-triage/issues/0069-sub-workflow-durable-identit[+22 chars]

## Ended: tool_use"
    );
}

/// Every turn ends with a line naming how the model stopped — or that it never answered.
///
/// The one thing a render must never leave to inference. A run graded `partial` and "truncated
/// mid-sentence" by the QC pass had in fact stopped `end_turn`; the render had simply not said
/// so, and a missing section is what the model reads absence from.
#[test]
fn a_turn_says_how_it_ended() {
    let (_scratch, store) = open_copy();
    let render = |session_id: &str, prefix: &str| {
        render_turn(&turn(&store, session_id, prefix), &TURN_BUDGETS)
    };
    // If a turn's last api call recorded why generation stopped, that value is the last line,
    // verbatim — `end_turn` is the one the fix exists for, and `stop_sequence` is the rarest
    // of the recorded values...
    assert_eq!(
        ended(&render(WORKTREE_SESSION, "7d30c171")),
        "## Ended: end_turn"
    );
    assert_eq!(ended(&render(SPINE, "8cdceb31")), "## Ended: stop_sequence");
    // ...if it recorded none, the line says so rather than going missing: `server_tools/`'s
    // turn made three calls, stopping `end_turn`, `tool_use` and NULL in that order, so the
    // null is the one that decides...
    assert_eq!(
        ended(&render(SERVER_TOOLS, "9ae45aaa")),
        "## Ended: not recorded"
    );
    // ...and a turn the model never answered at all says that, which is the whole fix for
    // turns: `/model` and `/coordinator` are handled by the CLI, and turns are not gated.
    for (session_id, prefix) in [
        (SPINE, "5b848af7"),
        (TEAMMATE, "97d6f3d4"),
        (MODEL_ONLY, "264ef04d"),
    ] {
        assert_eq!(
            ended(&render(session_id, prefix)),
            "## Ended: no model response"
        );
    }
}

/// A run ends with one line saying how it stopped, after its last section — not per response.
#[test]
fn a_run_says_how_it_ended_once() {
    let (_scratch, store) = open_copy();
    // If a run's calls recorded no stop reason — `spine/`'s outer run made two, both NULL —
    // then the run says so once, after everything it did...
    let outer = render_run(&run(&store, SPINE_RUN), &RUN_BUDGETS);
    assert_eq!(ended(&outer), "## Ended: not recorded");
    // ...and a run whose last call recorded one carries it verbatim, also once. 51 of the 69
    // recorded stop reasons are `tool_use`, which is why the design put the line at the end
    // rather than beside every response.
    let leaf = render_run(&run(&store, SPINE_LEAF), &RUN_BUDGETS);
    assert_eq!(ended(&leaf), "## Ended: tool_use");
    for rendered in [&outer, &leaf] {
        assert_eq!(rendered.matches("## Ended:").count(), 1);
    }
}

/// A slash command renders as the command it ran, never as the tag markup it was stored as.
#[test]
fn a_slash_turn_renders_the_command_not_its_tags() {
    // If both of `spine/`'s slash turns are rendered — one recorded leading with
    // `<command-name>`, one with `<command-message>`, since both orderings occur...
    let (_scratch, store) = open_copy();
    let first = render_turn(&turn(&store, SPINE, "5b848af7"), &TURN_BUDGETS);
    let second = render_turn(&turn(&store, SPINE, "30aad8e5"), &TURN_BUDGETS);
    // ...then each names its command...
    assert!(first.contains("## Command\n/model [redacted]"));
    assert!(second.contains("## Command\n/night-run [redacted]"));
    // ...and neither spends budget on markup that would read as content.
    for rendered in [&first, &second] {
        assert!(!rendered.contains("<command-name>"));
        assert!(!rendered.contains("<command-message>"));
    }
}

/// A slash command's own output travels with it, and a turn with none says so.
///
/// Most command turns drive no model response at all, so before this the render said what was
/// asked and nothing about what happened — and a reader with no answer in front of it infers
/// one. `/model` reads as a question the session never got an answer to.
#[test]
fn a_command_turn_carries_what_the_cli_printed() {
    let (_scratch, store) = open_copy();
    // If `spine/`'s `/model` turn is rendered — the CLI answered it, and the archive kept what
    // it printed — then the whole prompt is this: the answer sits in the head beside the
    // command, ahead of the work, and the `Ended:` line still ends the render.
    assert_eq!(
        render_turn(&turn(&store, SPINE, "5b848af7"), &TURN_BUDGETS),
        "\
# Main turn

## Command
/model [redacted]

## Command result
[redacted]

## Ended: no model response"
    );
    // ...if the record that answered it printed nothing — `/clear` always does — the render
    // says so in its own words...
    assert!(
        render_turn(&turn(&store, MODEL_ONLY, "144af379"), &TURN_BUDGETS)
            .contains("## Command result: the command printed nothing")
    );
    // ...and if nothing archived an answer — `/night-run` is the one recorded turn in that
    // state — the render says that instead. The two read alike and mean opposite things, which
    // is why one test renders both.
    assert!(
        render_turn(&turn(&store, SPINE, "30aad8e5"), &TURN_BUDGETS)
            .contains("## Command result: not recorded")
    );
}

/// A run renders each instruction it was given, in order, with the teammate markup gone.
///
/// Before this the model saw an agent's replies but never what it was asked, for every run its
/// lead came back to.
#[test]
fn a_multi_turn_run_renders_every_instruction_in_sequence() {
    // If the `teammate/` architect was given a second instruction an hour after the first...
    let (_scratch, store) = open_copy();
    let rendered = render_run(&run(&store, TEAMMATE_RUN), &RUN_BUDGETS);
    // ...then the whole prompt is this: the run's type, its task, and then each later
    // instruction with the work it drove, in the order they happened.
    assert_eq!(
        rendered,
        "\
# Agent run: architect

## Task
[redacted]

## Response
- Read (input 27 chars, result 10 chars) {\"file_path\": \"[redacted]\"}

## Instruction
[redacted]

## Response
[redacted]
- Bash (input 54 chars, result 10 chars) {\"command\": \"[redacted]\", \"description\": \
\"[redacted]\"}

## Ended: tool_use"
    );
    // The wrapper the transcript stores an instruction in is markup, and would read as
    // content — the attributes especially, which no other turn opener carries.
    assert!(!rendered.contains("<teammate-message"));
    assert!(!rendered.contains("teammate_id="));
}

/// A run with no prompt of its own renders its work alone, labeled for what it is.
///
/// All 41 zero-turn runs of the corpus are forks whose task lives in the transcript they
/// continue — a render that assumes a task prompt lies about every one of them.
#[test]
fn a_zero_turn_run_renders_as_a_continuation() {
    // If `fork_byref/`'s fork, which holds two api calls and not one turn, is rendered...
    let (_scratch, store) = open_copy();
    let rendered = render_run(&run(&store, BYREF_FORK), &RUN_BUDGETS);
    // ...then the render says where the task went, and carries both calls in order. The first
    // is the fork's own spawning call, recorded inside the fork: a run does not embed itself.
    assert_eq!(
        rendered,
        "\
# Agent run: fork

## Continuation
This run continues a conversation another transcript holds; its task is not here.

## Response
- Agent (input 84 chars, result 10 chars) {\"description\": \"[redacted]\", \
\"subagent_type\": \"[redacted]\", \"prompt\": \"[redacted]\"}

## Response
- Bash (input 54 chars, result 10 chars) {\"command\": \"[redacted]\", \"description\": \
\"[redacted]\"}

## Ended: tool_use"
    );
}

/// A fork's copy of the turn it continues is not that fork's task.
///
/// The renders read the `live_*` views for exactly this: over the base tables the same turn id
/// appears twice, and the copy would hand the fork the auditor's prompt as its own.
#[test]
fn a_replayed_turn_is_not_the_runs_task() {
    // If `fork_origin/`'s auditor and the fork it spawned both hold turn `33438141…` — the
    // auditor because it ran it, the fork because forking replays it...
    let (_scratch, store) = open_copy();
    let auditor = render_run(&run(&store, FORK_ORIGIN_RUN), &RUN_BUDGETS);
    let fork = render_run(&run(&store, FORK_RUN), &RUN_BUDGETS);
    // ...then the run that ran the turn renders it as its task...
    assert!(auditor.starts_with("# Agent run: auditor\n\n## Task\n[redacted]\n"));
    // ...and the fork renders as a continuation, with no task at all...
    assert!(fork.starts_with("# Agent run: fork\n\n## Continuation\n"));
    assert!(!fork.contains("## Task"));
    // ...while still carrying its own work, error tail and unanswered call included, under the
    // line that says how it ended.
    assert!(fork.ends_with(
        "\
## Response
[redacted]
- Agent (input 84 chars, result 10 chars, ERROR) {\"description\": \"[redacted]\", \
\"subagent_type\": \"[redacted]\", \"prompt\": \"[redacted]\"} | error tail: [redacted]
- Agent (input 84 chars, unanswered) {\"description\": \"[redacted]\", \"subagent_type\": \
\"[redacted]\", \"prompt\": \"[redacted]\"}

## Ended: tool_use"
    ));
}

/// A run's children reach its prompt as their descriptions, never as their text.
#[test]
fn a_spawned_run_renders_as_its_description() {
    // If `spine/`'s leaf run has been enriched, and its parent — the run whose `Agent` call
    // spawned it — is rendered...
    let (_scratch, store) = open_copy();
    describe(
        &store,
        &run(&store, SPINE_LEAF),
        "Read one file and reported back.",
    );
    let rendered = render_run(&run(&store, SPINE_RUN), &RUN_BUDGETS);
    // ...then the spawning line carries what the child did, which is how a parent describes
    // work it never saw the text of.
    assert!(rendered.contains(
        "- Agent (input 131 chars, result 10 chars) {\"description\": \"Research 0149 \
multi-instance pg0\", \"subagent_type\": \"Explore\", \"run_in_background\": false, \
[+23 chars] | subagent: Read one file and reported back."
    ));
}

/// An `Agent` call whose run is missing renders as an ordinary tool line.
#[test]
fn a_spawning_call_with_no_run_renders_plainly() {
    // If the same run is rendered with its one enriched child — its other `Agent` call really
    // spawned no run row, the subagent having left no transcript...
    let (_scratch, store) = open_copy();
    describe(
        &store,
        &run(&store, SPINE_LEAF),
        "Read one file and reported back.",
    );
    let rendered = render_run(&run(&store, SPINE_RUN), &RUN_BUDGETS);
    // ...then that call is a tool line like any other, with no description slot and no crash:
    // exactly one of the two `Agent` lines carries a child.
    assert_eq!(rendered.matches(" | subagent: ").count(), 1);
    assert!(rendered.ends_with(
        "- Agent (input 132 chars, result 10 chars) {\"description\": \"Research 0155 \
data-edge semantics\", \"subagent_type\": \"Explore\", \"run_in_background\": false,\
[+24 chars]\n\n## Ended: not recorded"
    ));
}

/// A session renders what it cost and a line per thing it did, in the order it did them.
#[test]
fn a_session_renders_its_metrics_then_what_it_did() {
    // If every main turn of `spine/` has been described...
    let (_scratch, store) = open_copy();
    for item in store.turn_items(None).expect("the turns read") {
        if item.session_id == SPINE {
            describe(&store, &item, &format!("Did thing {}.", item.index));
        }
    }
    let rendered = render_session(&session(&store, SPINE), &SESSION_BUDGETS);
    // ...then the whole prompt is this: the title and branch the session recorded, the time it
    // took, what it spent, and its children as their own descriptions — never their text. The
    // turn recorded a month before the other three comes first: children are in chronological
    // order, which is not the order the store returns them in.
    assert_eq!(
        rendered,
        "\
# Session: fixture-title-2

## Metrics
branch fixture-branch-1
wall 30d 23h, active 3m 39s
tokens 15 in, 5,579 out, 295,615 cache read, 144,797 cache write
cost $3.07

## Work
- Main turn [explore/completed] Did thing 3.
- Main turn [explore/completed] Did thing 0.
- Main turn [explore/completed] Did thing 1.
- Main turn [explore/completed] Did thing 2."
    );
}

/// A session carries its main turns and the runs no turn or run of its own already carries.
///
/// Reading depth-1 runs as the children instead would drop every recorded teammate agent — 43
/// of them — out of every session summary, and embed ten other runs twice.
#[test]
fn a_sessions_children_are_its_turns_and_the_runs_nothing_embeds() {
    let (_scratch, store) = open_copy();
    // If `teammate/` has one main turn and one run that no tool call spawned...
    describe(&store, &run(&store, TEAMMATE_RUN), "Drew up the plan.");
    for item in store.turn_items(None).expect("the turns read") {
        if item.session_id == TEAMMATE {
            describe(&store, &item, "Asked for a plan.");
        }
    }
    // ...then both are children of the session, each once...
    assert!(
        render_session(&session(&store, TEAMMATE), &SESSION_BUDGETS).ends_with(
            "\
## Work
- Main turn [explore/completed] Asked for a plan.
- Agent run (architect) [explore/completed] Drew up the plan."
        )
    );
    // ...while `spine/`'s subagent, which a main turn spawned, is not a child of the session at
    // all: it reaches the session through that turn's own description.
    describe(&store, &run(&store, SPINE_RUN), "Ran the subagent.");
    assert!(
        !render_session(&session(&store, SPINE), &SESSION_BUDGETS).contains("Ran the subagent.")
    );
}

/// A run whose spawning call belongs to no turn is carried by the session itself.
///
/// Planted, but the shape is recorded: 9 runs of the corpus were spawned by a main-transcript
/// call that belongs to no turn, and no turn's render can reach them. A rule keyed on the
/// spawning call's *existence* rather than on what embeds the run would drop all nine.
#[test]
fn a_run_spawned_outside_every_turn_is_still_a_session_child() {
    let (_scratch, store) = open_copy();
    // If the api call that spawned `spine/`'s subagent belongs to no turn...
    store
        .connection()
        .execute(
            "UPDATE api_calls SET turn_id = NULL WHERE id =
             (SELECT api_call_id FROM tool_calls WHERE id = 'toolu_015dP3eMe5GZn7BzFipupZwS')",
            [],
        )
        .expect("the spawning call is orphaned");
    describe(&store, &run(&store, SPINE_RUN), "Ran the subagent.");
    // ...then nothing else embeds it, so the session does.
    assert!(
        render_session(&session(&store, SPINE), &SESSION_BUDGETS)
            .contains("- Agent run (claude) [explore/completed] Ran the subagent.")
    );
}

/// A `Workflow` call carries its run's description, exactly as an `Agent` call does.
#[test]
fn a_workflow_line_embeds_its_spawned_run() {
    // If the run `workflow/`'s main turn started has been described...
    let (_scratch, store) = open_copy();
    describe(
        &store,
        &run(&store, WORKFLOW_AGENT),
        "Researched the question.",
    );
    let rendered = render_turn(
        &turn(&store, DEEP_RESEARCH_SESSION, "cd7adeae"),
        &TURN_BUDGETS,
    );
    // ...then the turn that spawned it reads what it did — the second of the two tools that
    // start a run, and the one a rule keyed on the `Agent` name alone would miss.
    assert!(rendered.ends_with(
        "- Workflow (input 47 chars, result 10 chars) {\"name\": \"deep-research\", \
\"args\": \"[redacted]\"} | subagent: Researched the question.\n\n## Ended: tool_use"
    ));
}
