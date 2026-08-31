//! The one name a tool call carries wherever it is printed.
//!
//! A tool call is named once, out of the fields the store ships (`view::formatters`), and every
//! surface that prints it — the pane heading, the NavTree row, the crumb, the parent's log row,
//! the errors list — prints that one string cut to its own width. These leaves read the string
//! back off each of those surfaces and check they agree. What an api call is named by is
//! `node_call_titles.rs`.

use duckdb::params;

use hyphae_store::{Param, Store, macros, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_testsupport::tools;
use hyphae_view::format::{ELLIPSIS, cut};
use hyphae_view::formatters::NAMED;
use hyphae_view::nodes::LEAD_SEPARATOR;

/// The project a planted session says it ran in, long enough that cutting it off the front of a
/// path is what decides whether a reader sees the end of a name.
const PROJECT: &str = "/Users/planted/repos/hyphae";

/// The busiest api call in the corpus and the first tool call under it: one row every surface
/// below has a reason to name.
fn a_call_and_its_first_tool(db: &std::path::Path) -> (String, String, String, String) {
    let call = rows::one(
        db,
        "SELECT session_id, source, api_call_id FROM live_tool_calls \
         GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3 LIMIT 1",
        &[],
    );
    let (session_id, source, call_id) = (
        call.str("session_id").expect("a session id").to_owned(),
        call.str("source").expect("a thread").to_owned(),
        call.str("api_call_id").expect("a call id").to_owned(),
    );
    let tool = rows::one(
        db,
        "SELECT id FROM live_tool_calls WHERE session_id = $session AND source = $source \
         AND api_call_id = $call ORDER BY \"index\" LIMIT 1",
        &[
            ("session", Param::from(&session_id)),
            ("source", Param::from(&source)),
            ("call", Param::from(&call_id)),
        ],
    );
    let tool_id = tool.str("id").expect("a tool id").to_owned();
    (session_id, source, call_id, tool_id)
}

#[tokio::test]
async fn one_tool_call_is_titled_the_same_way_wherever_it_is_named() {
    // The five surfaces that name a tool call agree, because one derivation names it.
    //
    // The pane's own heading, the NavTree row beside it, the crumb chain leading it, the row in
    // its parent's children log, and the session's errors list — a call that answered with tool
    // calls and no words is named by the first of them. They read different queries at four
    // widths, so the agreement is a fact about the derivation rather than about the page: before
    // it was shared, three of these showed the input JSON as stored and the fourth showed the
    // path.
    //
    // Planted because redaction left no recorded input with a path in it, and no failure whose
    // input says what it was asked.
    let corpus = Served::corpus();
    let (session_id, source, call_id, tool_id) = a_call_and_its_first_tool(&corpus.db());
    let at = format!("/session/{session_id}/thread/{source}/tool/{tool_id}");
    let parent_at = format!("/session/{session_id}/thread/{source}/call/{call_id}");
    let errors_at = format!("/session/{session_id}/errors");

    // A failed read of a file inside the session's own project, on a call that said nothing: the
    // errors list only lists what failed, and the api call above it is named by its tools only
    // where it spoke no words itself.
    let read_of = |path: &str, failed: bool| {
        let (session_id, call_id, tool_id) = (session_id.clone(), call_id.clone(), tool_id.clone());
        let input = format!(r#"{{"file_path": "{PROJECT}/{path}"}}"#);
        move |store: &Store| {
            let connection = store.connection();
            connection
                .execute(
                    "UPDATE sessions SET project_dir = ? WHERE id = ?",
                    params![PROJECT, session_id],
                )
                .expect("the session says where it ran");
            connection
                .execute(
                    "UPDATE tool_calls SET name = 'Read', input = ?, is_error = ? WHERE id = ?",
                    params![input, failed, tool_id],
                )
                .expect("the call reads a file");
            connection
                .execute(
                    "UPDATE api_calls SET text = '' WHERE id = ?",
                    params![call_id],
                )
                .expect("the call above it says nothing");
        }
    };

    let served = Served::planted(read_of("src/hyphae/view/nodes.py", true));
    let (_, pane) = served.page(&at).await;
    let (_, parent) = served.page(&parent_at).await;
    let (_, listed) = served.page(&errors_at).await;
    let (pane, parent, listed) = (Markup::of(&pane), Markup::of(&parent), Markup::of(&listed));
    // The title the derivation composes: the glyph that stands for the tool, then what it was
    // asked. Short enough that the narrowest of the surfaces still prints it whole.
    let titled = "📖 src/hyphae/view/nodes.py";
    assert!(titled.chars().count() < queries::CRUMB_CHARS);
    // Its own pane heads it, the NavTree row it stands on carries it, the crumb chain that leads
    // the pane ends on it, and the errors list — which reads a query of its own, over every
    // thread of the session — carries the same string.
    assert_eq!(pane.field("data-body", "tool", "title"), titled);
    assert_eq!(
        pane.field("data-nav-tree", &format!("tool:{tool_id}"), "title"),
        titled
    );
    assert_eq!(
        pane.field("data-crumb", &format!("tool:{tool_id}"), "tool"),
        titled
    );
    assert_eq!(
        listed.field("data-error", &format!("tool:{tool_id}"), "title"),
        titled
    );
    // And so does the children log under the parent call, which prints the words alone in its
    // `Title` column. The glyph is not the tool's name, so nothing is said twice: `Read` stands
    // in the `Tool` column beside it.
    let row = parent.fields("data-child", &format!("tool:{tool_id}"));
    assert_eq!(row["title"], titled);
    assert_eq!(row["name"], "Read");
    // And the api-call row above it, on the same page: a call that spoke no words is named by
    // its first tool call, so the string leads its title with the count of what followed after.
    let above = pane.field("data-nav-tree", &format!("call:{call_id}"), "title");
    assert!(above.starts_with(titled), "{above}");

    // A path long enough that cutting the project directory off it matters. The surfaces have
    // four widths between them, so the same call is shown four lengths — and each one is the
    // head of the *relative* path, marked where it stopped. A derivation that cut the absolute
    // path first would hand every surface the same short string, unmarked and one project
    // directory shorter than the width it was asked for.
    let long_path = format!("src/hyphae/{}.sql", "v".repeat(380));
    let served = Served::planted(read_of(&long_path, true));
    let (_, pane) = served.page(&at).await;
    let (_, parent) = served.page(&parent_at).await;
    let (_, listed) = served.page(&errors_at).await;
    let (pane, parent, listed) = (Markup::of(&pane), Markup::of(&parent), Markup::of(&listed));
    // The glyph is spent out of the width like any other character: it leads the words, so every
    // surface pays two characters for it and cuts the path two characters earlier.
    let whole = format!("📖 {long_path}");
    assert_eq!(
        pane.field("data-body", "tool", "title"),
        cut(&whole, queries::HEADER_CHARS)
    );
    assert_eq!(
        pane.field("data-crumb", &format!("tool:{tool_id}"), "tool"),
        cut(&whole, queries::CRUMB_CHARS)
    );
    assert_eq!(
        pane.field("data-nav-tree", &format!("tool:{tool_id}"), "title"),
        cut(&whole, queries::NAV_CHARS)
    );
    assert_eq!(
        listed.field("data-error", &format!("tool:{tool_id}"), "title"),
        cut(&whole, queries::NAV_CHARS)
    );
    assert_eq!(
        parent.field("data-child", &format!("tool:{tool_id}"), "title"),
        cut(&whole, queries::LOG_CHARS)
    );

    // And a path that fits every width reaches every surface whole, extension and all: the pane
    // has the least room of the four widths that cut a whole title, and 30 characters of project
    // directory is what decides whether a reader sees the end of the name or a cut that says
    // nothing.
    let fits = format!("src/hyphae/{}.sql", "v".repeat(72));
    assert!(format!("📖 {fits}").chars().count() < queries::HEADER_CHARS);
    let served = Served::planted(read_of(&fits, false));
    let (_, pane) = served.page(&at).await;
    assert_eq!(
        Markup::of(&pane).field("data-body", "tool", "title"),
        format!("📖 {fits}")
    );
}

#[tokio::test]
async fn every_registered_tool_the_corpus_records_agrees_across_its_surfaces() {
    // The leaf above pins the widths on a planted row; this one takes the corpus as it is.
    //
    // One recorded call of each name the registry knows and the fixtures hold, read on its own
    // page: the NavTree row, the crumb ending the chain, the pane's heading and the row in the
    // parent call's children log print one string, cut to each surface's own width. Nothing here
    // restates what the string should be — `nav_tree_names.rs` checks that against the store's
    // own columns. What is checked here is that four queries agree on a recorded row.
    //
    // Each row is chosen for a field redaction left intact, shortest first, so the title is a
    // real one and short enough that the widest surface prints it whole. A fixture re-cut that
    // redacts one of these fields again reds this leaf rather than passing on `[redacted]`.
    let served = Served::corpus();
    let db = served.db();
    for (name, glyph, field) in tools::RECORDED {
        let row = rows::one(
            &db,
            "SELECT session_id, source, id, api_call_id FROM live_tool_calls \
             WHERE name = $name AND json_extract_string(input, $field) \
             NOT IN ('[redacted]', '') ORDER BY length(input), id LIMIT 1",
            &[
                ("name", Param::from(name)),
                ("field", Param::from(format!("$.{field}").as_str())),
            ],
        );
        let session_id = row.str("session_id").expect("a session id");
        let source = row.str("source").expect("a thread");
        let tool_id = row.str("id").expect("a tool id");
        let call_id = row.str("api_call_id").expect("a call id");
        let thread = format!("/session/{session_id}/thread/{source}");
        let (_, pane) = served.page(&format!("{thread}/tool/{tool_id}")).await;
        let (_, parent) = served.page(&format!("{thread}/call/{call_id}")).await;
        let (pane, parent) = (Markup::of(&pane), Markup::of(&parent));
        // The NavTree cuts widest, so its row is the whole title when it carries no ellipsis.
        let whole = pane.field("data-nav-tree", &format!("tool:{tool_id}"), "title");
        assert!(
            whole.starts_with(&format!("{glyph} ")) && !whole.ends_with(ELLIPSIS),
            "{name}: {whole}"
        );
        assert_eq!(
            pane.field("data-body", "tool", "title"),
            cut(&whole, queries::HEADER_CHARS),
            "{name}"
        );
        assert_eq!(
            pane.field("data-crumb", &format!("tool:{tool_id}"), "tool"),
            cut(&whole, queries::CRUMB_CHARS),
            "{name}"
        );
        assert_eq!(
            parent.field("data-child", &format!("tool:{tool_id}"), "title"),
            cut(&whole, queries::LOG_CHARS),
            "{name}"
        );
        // A path is the one field read against something outside the tool call: the session's own
        // project directory comes off the front, so the row spends its width on the part that
        // tells two files apart rather than on where the machine keeps the repository.
        if field == "file_path" {
            let session = rows::one(
                &db,
                "SELECT project_dir FROM sessions WHERE id = $id",
                &[("id", Param::from(session_id))],
            );
            let project = session.str("project_dir").expect("a project").to_owned();
            let given = rows::one(
                &db,
                "SELECT input FROM live_tool_calls WHERE id = $id",
                &[("id", Param::from(tool_id))],
            );
            let given = given.str("input").expect("an input").to_owned();
            assert_eq!(project, MYCELIA);
            assert!(given.contains(&format!("{project}/")), "{given}");
            let asked: serde_json::Value =
                serde_json::from_str(&given).expect("a recorded input is JSON");
            let path = asked[field].as_str().expect("a path");
            assert_eq!(whole, format!("{glyph} {}", &path[project.len() + 1..]));
        }
    }
    // Which of the registry's names this corpus records: the six above and no others. The rest
    // are proven by the unit table over inputs no fixture holds — so a fixture that gains a
    // `Grep` call reds this line rather than going unread.
    let recorded: std::collections::BTreeSet<String> = rows::all(
        &db,
        "SELECT DISTINCT name FROM live_tool_calls ORDER BY 1",
        &[],
    )
    .into_iter()
    .map(|row| row.str("name").expect("a tool name").to_owned())
    .collect();
    let named: std::collections::BTreeSet<String> = NAMED.iter().map(|&n| n.to_owned()).collect();
    let held: std::collections::BTreeSet<String> = tools::RECORDED
        .iter()
        .map(|(name, _, _)| (*name).to_owned())
        .collect();
    assert_eq!(&recorded & &named, held);
}

#[tokio::test]
async fn a_tool_the_registry_does_not_name_keeps_the_title_the_store_composed() {
    // Every recorded call of a tool with no registry entry, against the rule it used to take.
    //
    // The shape-driven title was the store's: one `coalesce` over a relativized path, a
    // description, and the head of the input as stored. Composing it in Python — and now here —
    // instead is the move no leaf above can see: those read the tools the registry names, and
    // this rule is what names every other tool there is or ever will be.
    //
    // So the expectation is that `coalesce` itself, run here over the same rows through the two
    // macros that survive, and the sweep is every unnamed call the corpus holds rather than one
    // of them. Each of the three arms is asserted to have fired: a port that dropped one would
    // otherwise pass on the rows taking the arms it kept.
    let served = Served::corpus();
    let store = Store::open_read_only(&served.db()).expect("the store opens read only");
    macros::install(store.connection()).expect("the macros install");
    let known = NAMED
        .iter()
        .map(|name| format!("'{name}'"))
        .collect::<Vec<_>>()
        .join(", ");
    let unnamed = store
        .fetch(
            &format!(
                "SELECT t.session_id, t.source, t.id, t.name, \
                 tool_path(t.input, s.project_dir, $chars) AS path, \
                 tool_asked(t.input, 'description', $chars) AS about, \
                 substr(t.input, 1, $chars + 1) AS head \
                 FROM live_tool_calls t LEFT JOIN sessions s ON s.id = t.session_id \
                 WHERE t.name NOT IN ({known}) \
                 ORDER BY t.session_id, t.source, t.\"index\""
            ),
            &[("chars", Param::Int(queries::HEADER_CHARS as i64))],
        )
        .expect("the store answers");
    assert!(
        !unnamed.is_empty(),
        "the corpus records no call of a tool the registry leaves unnamed"
    );
    let mut took: std::collections::BTreeSet<&str> = std::collections::BTreeSet::new();
    for row in &unnamed {
        // The first arm the record answers, which is what `coalesce` means: an empty string is a
        // value the caller sent and not an absence.
        let (arm, words) = ["path", "about", "head"]
            .into_iter()
            .find_map(|arm| {
                row.opt_str(arm)
                    .expect("a column of text or none")
                    .map(|words| (arm, words))
            })
            .expect("every input answers the last arm");
        took.insert(arm);
        let at = format!(
            "/session/{}/thread/{}/tool/{}",
            row.str("session_id").expect("a session id"),
            row.str("source").expect("a thread"),
            row.str("id").expect("a tool id"),
        );
        let name = row.str("name").expect("a tool name");
        let (_, page) = served.page(&at).await;
        // The tool's own name still leads the row, because no glyph stands in for it.
        assert_eq!(
            Markup::of(&page).field("data-body", "tool", "title"),
            cut(
                &format!("{name}{LEAD_SEPARATOR}{words}"),
                queries::HEADER_CHARS
            ),
            "{at}"
        );
    }
    assert_eq!(took, ["about", "head", "path"].into_iter().collect());
}

#[tokio::test]
async fn a_message_to_a_run_is_titled_by_what_that_run_was_spawned_as() {
    // `SendMessage` is the one title that reads past the row it names.
    //
    // The tool call carries `to`, which holds either an agent run's id or a name the sender
    // typed. An id says nothing to a reader, so a `to` the session holds a run for reads as the
    // definition that run was spawned as — and the word the row prints is then one the record
    // behind it does not contain, which is what pins the lookup here. An implementation that
    // never looked past the tool call would print the id and satisfy every other leaf.
    //
    // Anything else prints as it was sent: a name the sender typed is already the useful word,
    // and a stale id is better shown than guessed at. Planted, because the fixture corpus records
    // `SendMessage` on one session only and every send there addresses a run of it.
    let served = Served::corpus();
    let row = rows::one(
        &served.db(),
        "SELECT t.session_id, t.source, t.id, t.input, a.agent_type FROM live_tool_calls t \
         JOIN live_agent_runs a ON a.session_id = t.session_id \
         AND a.id = json_extract_string(t.input, '$.to') \
         WHERE t.name = 'SendMessage' ORDER BY t.session_id, t.source, t.\"index\" LIMIT 1",
        &[],
    );
    let tool_id = row.str("id").expect("a tool id").to_owned();
    let sent = row.str("input").expect("an input").to_owned();
    let agent_type = row.str("agent_type").expect("an agent type").to_owned();
    let at = format!(
        "/session/{}/thread/{}/tool/{tool_id}",
        row.str("session_id").expect("a session id"),
        row.str("source").expect("a thread"),
    );
    let (_, page) = served.page(&at).await;
    let titled = Markup::of(&page).field("data-body", "tool", "title");
    // What the reader is given is the agent, not the id — and it is nowhere in the record the row
    // stands for. The summary beside it is redacted in the fixture, so only the address is read
    // here; the arm below sends one worth printing.
    assert!(
        titled.starts_with(&format!("📬 to {agent_type}")),
        "{titled}"
    );
    assert!(!sent.contains(&agent_type), "{sent}");
    // An address the session holds no run for is printed as recorded, with what was said.
    let planted = Served::planted(move |store: &Store| {
        store
            .connection()
            .execute(
                "UPDATE tool_calls SET input = ? WHERE id = ?",
                params![
                    r#"{"to": "architect", "summary": "the ladder is restacked"}"#,
                    tool_id
                ],
            )
            .expect("the send addresses a name nothing resolves");
    });
    let (_, page) = planted.page(&at).await;
    assert_eq!(
        Markup::of(&page).field("data-body", "tool", "title"),
        "📬 to architect: the ladder is restacked"
    );
}
