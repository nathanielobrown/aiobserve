//! Each tool the viewer names its own calls by, one case per rule.
//!
//! A unit table rather than served HTML: every fixture README redacts the strings under a tool
//! `input`, so a served row can prove the registry fired but not what it read. The six names the
//! corpus does record are read off pages in `node_titles.rs`; the rest are here and nowhere else.
//!
//! Ported from `tests/view/test_formatters.py`, whose table this is — every value in it but
//! four is lifted from a recorded session, and the four that are not say so. Nothing was
//! re-picked here: a case invented twice, once per language, is a case neither recording proves.

use hyphae_store::Value;
use hyphae_view::formatters::{Fields, Formatted, NAMED, name_tool};

/// Every field `tool_fields` extracts, all NULL, with `filled` written over it — what the store
/// hands a formatter for a tool call carrying none of them.
///
/// Built whole rather than only where a case fills something in: a member the query ships as
/// NULL and a member it does not ship at all are two different rows, and a rule that reached
/// past one would pass on the other.
fn fields(filled: &[(&str, Value)]) -> Value {
    let extracted = [
        "path",
        "command",
        "description",
        "subagent_type",
        "skill",
        "args",
        "to",
        "addressed",
        "summary",
        "pattern",
        "url",
        "query",
        "message",
        "todos",
        "input_head",
    ];
    let members: Vec<(String, Value)> = extracted
        .iter()
        .map(|name| {
            let held = filled
                .iter()
                .find(|(key, _)| key == name)
                .map_or(Value::Null, |(_, held)| held.clone());
            ((*name).to_owned(), held)
        })
        .collect();
    for (key, _) in filled {
        assert!(
            extracted.contains(key),
            "`{key}` is not a field the store extracts"
        );
    }
    Value::Struct(members.into())
}

/// One row of the named-tool table: the tool, what its record carried, the glyph, the words.
type Case = (
    &'static str,
    Vec<(&'static str, Value)>,
    &'static str,
    &'static str,
);

/// One field as the store holds it.
fn said(words: &str) -> Value {
    Value::Text(words.to_owned())
}

#[test]
fn a_named_tool_is_titled_by_the_field_the_design_gives_it() {
    // Each tool the registry names reads its own field, marked with its own glyph. `spine`'s
    // three main-thread reads come first, relativized against the session's project already:
    // what the formatter gets is the path the macro cut, not the path the record held.
    let cases: Vec<Case> = vec![
        (
            "Read",
            vec![("path", said("docs/handoffs.md"))],
            "📖",
            "docs/handoffs.md",
        ),
        (
            "Write",
            vec![("path", said("data/migrate_project_rename.py"))],
            "✏️",
            "data/migrate_project_rename.py",
        ),
        (
            "Edit",
            vec![("path", said("tests/enrich/test_prompts.py"))],
            "📝",
            "tests/enrich/test_prompts.py",
        ),
        // A `Bash` call carries both, and the row shows what ran rather than what it was called:
        // a column of descriptions is a column of an agent's own summaries of itself.
        (
            "Bash",
            vec![
                ("command", said("date; ls /Users/nob/repos/mycelia/issues/")),
                ("description", said("List issues")),
            ],
            "⚡",
            "date; ls /Users/nob/repos/mycelia/issues/",
        ),
        // And only its first line: a heredoc or an `&&` chain is a screenful, and the row has one.
        (
            "Bash",
            vec![(
                "command",
                said("python3 - <<'PY'\nimport json\nprint(1)\nPY"),
            )],
            "⚡",
            "python3 - <<'PY'",
        ),
        // `spine`'s two delegations, which is the shape the brackets were chosen for: a tree of
        // `Agent` rows reads as a column of types with a task line beside each.
        (
            "Agent",
            vec![
                ("subagent_type", said("Explore")),
                ("description", said("Research 0149 multi-instance pg0")),
            ],
            "👉",
            "[Explore] Research 0149 multi-instance pg0",
        ),
        // A delegation that named no type — Claude Code writes `subagent_type` only where the
        // caller picked one — is the task line alone rather than an empty bracket.
        (
            "Agent",
            vec![("description", said("Grill doc: needs-design pair"))],
            "👉",
            "Grill doc: needs-design pair",
        ),
        // A skill invoked bare, and one invoked with arguments, which ride after the name.
        ("Skill", vec![("skill", said("design"))], "📕", "design"),
        (
            "Skill",
            vec![
                ("skill", said("writing")),
                ("args", said("PR body for the viewer node-browser branch")),
            ],
            "📕",
            "writing PR body for the viewer node-browser branch",
        ),
        // The one formatter that reads beyond its own tool call: `to` holds either a run id or a
        // name already fit to print, and `addressed` is the agent type the id resolved to.
        (
            "SendMessage",
            vec![
                ("to", said("aa52d3fe48cec7f58")),
                ("addressed", said("auditor")),
                ("summary", said("Request the doc-sync")),
            ],
            "📬",
            "to auditor: Request the doc-sync",
        ),
        // Nothing resolved, so the row prints what was recorded — the teammate-name population.
        (
            "SendMessage",
            vec![
                ("to", said("architect")),
                ("summary", said("Grill the plan")),
            ],
            "📬",
            "to architect: Grill the plan",
        ),
        // A send with no summary is the address alone, rather than a dangling colon.
        (
            "SendMessage",
            vec![("to", said("team-lead"))],
            "📬",
            "to team-lead",
        ),
        // The two search tools, invented: both document one `pattern`, and neither has a row
        // anywhere in this project's own store.
        (
            "Grep",
            vec![("pattern", said("def tool_node"))],
            "🔎",
            "def tool_node",
        ),
        ("Glob", vec![("pattern", said("**/*.sql"))], "🗂", "**/*.sql"),
        (
            "WebFetch",
            vec![(
                "url",
                said("https://mise.jdx.dev/tasks/task-arguments.html"),
            )],
            "🌐",
            "https://mise.jdx.dev/tasks/task-arguments.html",
        ),
        (
            "WebSearch",
            vec![(
                "query",
                said("mutmut 3 pyproject.toml config paths_to_mutate 2026"),
            )],
            "🔍",
            "mutmut 3 pyproject.toml config paths_to_mutate 2026",
        ),
        // A tool search reads the query it ran, not the `max_results` beside it: what tells two
        // searches apart is what was searched for.
        (
            "ToolSearch",
            vec![("query", said("select:PushNotification"))],
            "🧰",
            "select:PushNotification",
        ),
        // A notification reads the message it sent — the only thing it carries besides a status.
        // What the session recorded is an agent's prose about private work, replaced in the
        // fixture by an invented sentence of the same shape.
        (
            "PushNotification",
            vec![(
                "message",
                said("Invented for this fixture: the run finished and the report is written up"),
            )],
            "🔔",
            "Invented for this fixture: the run finished and the report is written up",
        ),
        // A todo list is the one row named by a count: the items are the model's own plan, and a
        // row of the first one says less than how many there are. Invented, like the two above.
        (
            "TodoWrite",
            vec![("todos", Value::BigInt(3))],
            "☑️",
            "3 todos",
        ),
        (
            "TodoWrite",
            vec![("todos", Value::BigInt(1))],
            "☑️",
            "1 todo",
        ),
    ];
    for (name, filled, mark, words) in &cases {
        let held = fields(filled);
        assert_eq!(
            name_tool(name, Fields::of(Some(&held))),
            Formatted {
                mark,
                words: (*words).to_owned()
            },
            "{name} {filled:?}"
        );
    }
}

#[test]
fn a_tool_the_registry_does_not_name_is_named_by_the_shape_of_its_input() {
    // What a call the registry has no rule for is named by: the shape of its input, checked in
    // order — a path, else a description, else the head of the input as stored — and the glyph
    // is empty, because a shape says which tool ran to nobody.
    let cases: Vec<(Vec<(&str, Value)>, &str)> = vec![
        // A path wins, and it reaches here relativized and cut already.
        (
            vec![
                ("path", said("docs/viewer.md")),
                ("description", said("Read the doc")),
            ],
            "docs/viewer.md",
        ),
        // Else what the caller said the call was for.
        (
            vec![("description", said("Run the deep research"))],
            "Run the deep research",
        ),
        // Else the head of the input as the store holds it, which is JSON for every tool we have
        // seen. A call carrying none of the names above still names its own row.
        (
            vec![(
                "input_head",
                said(r#"{"schema": "Findings", "strict": true}"#),
            )],
            r#"{"schema": "Findings", "strict": true}"#,
        ),
    ];
    for (filled, words) in &cases {
        let held = fields(filled);
        assert_eq!(
            name_tool("StructuredOutput", Fields::of(Some(&held))),
            Formatted {
                mark: "",
                words: (*words).to_owned()
            },
            "{filled:?}"
        );
    }
}

#[test]
fn an_empty_field_is_a_value_the_record_carried_and_not_an_absence() {
    // The arms fall through on NULL, the way the SQL this ports from coalesces. A description
    // recorded as an empty string is a description: the row it names is blank, and a rule that
    // skipped it would print the input JSON under a tool whose caller said the call was for
    // nothing.
    let held = fields(&[
        ("description", said("")),
        ("input_head", said(r#"{"description": ""}"#)),
    ]);
    assert_eq!(
        name_tool("StructuredOutput", Fields::of(Some(&held))),
        Formatted {
            mark: "",
            words: String::new()
        }
    );
}

#[test]
fn a_named_tool_whose_field_the_record_lacks_falls_through_too() {
    // A registered tool is only formatted where the record carried what its lead reads. A
    // malformed input, or one whose fields are all named something else, is a row the page still
    // has to draw — and the shape-driven title says more about it than a bare glyph.
    let head = r#"{"unexpected": 1}"#;
    let held = fields(&[("input_head", said(head))]);
    assert!(!NAMED.is_empty(), "the registry names no tool at all");
    for name in NAMED {
        assert_eq!(
            name_tool(name, Fields::of(Some(&held))),
            Formatted {
                mark: "",
                words: head.to_owned()
            },
            "{name}"
        );
    }
}
