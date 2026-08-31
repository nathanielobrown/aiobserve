//! What each kind of NavTree row is named from, and how an address resolves to a name.
//!
//! A row's title is the whole of what it says, so these leaves read every one back against the
//! column its kind is named by — a turn's prompt, a tool call's own fields, a run's agent type and
//! brief — with the expectations restated from the store in the test's own SQL rather than read
//! off the code that composes them.
//!
//! How a row is laid out and what it carries beside its title is `nav_tree_rows.rs`.

use std::collections::{BTreeMap, BTreeSet};

use duckdb::params;
use hyphae_store::Store;
use hyphae_store::queries::{DETAIL_CHARS, HEADER_CHARS, NAV_CHARS};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::Served;
use hyphae_view::format::cut;
use hyphae_view::nodes::{Kind, LEAD_SEPARATOR};
use hyphae_view::store::{Page, page_rows};
use serde_json::Value as Json;

#[tokio::test]
async fn every_row_is_named_from_the_column_its_kind_is_named_by() {
    // A row's title is the whole of what it says, so it is read back against its own column.
    //
    // A NavTree row carries `NAV_TREE_ROW_BYTES` and no more, so the title is where a kind spends
    // what it has to say — and every kind spends it differently. Every node the store names is
    // read on its own page, where its row is on the open path whichever preset the reader picked:
    // a column that only one recorded row exercises — a slash turn with no arguments after it — is
    // one a sample would step over.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    // Keyed by session as well as by row: two sessions of the corpus record an api call under the
    // same id, and a row carries the id alone.
    //
    // Composed at a NavTree row's width the way the surfaces compose it: the count of an api
    // call's tool calls is taken out of the width first, so the row is cut around it.
    let mut said: BTreeMap<(String, String), String> = BTreeMap::new();
    for session_id in hyphae_testsupport::served::session_ids(&served.db()) {
        for (key, (head, kept)) in titled(levels.store(), &session_id) {
            let width = NAV_CHARS - kept.chars().count();
            let title = format!("{}{kept}", cut(&head, width)).trim().to_owned();
            said.insert((session_id.clone(), key), title);
        }
    }
    let mut read: BTreeSet<(String, String)> = BTreeSet::new();
    for kind in [
        Kind::Tool,
        Kind::Call,
        Kind::Compaction,
        Kind::Run,
        Kind::Turn,
    ] {
        for (session_id, source, node_id) in levels.candidates(kind) {
            // A page holds more than the node it opens, so the ones already read are skipped.
            let own = format!("{kind}:{node_id}");
            if read.contains(&(session_id.clone(), own.clone())) {
                continue;
            }
            let (_, html) = served
                .page(&nav_trees::node_url(kind, &session_id, &source, &node_id))
                .await;
            let page = Markup::of(&html);
            let drawn = page.values("data-nav-tree");
            assert!(drawn.contains(&own), "{node_id}");
            for key in drawn {
                let at = (session_id.clone(), key.clone());
                if let Some(expected) = said.get(&at) {
                    assert_eq!(
                        page.fields("data-nav-tree", &key)["title"],
                        *expected,
                        "{at:?}"
                    );
                    read.insert(at);
                }
            }
        }
    }
    // Every row the store names a title for was reached. A sweep that missed one would pass on a
    // title built from any column at all.
    assert_eq!(read, said.keys().cloned().collect());
}

/// What the planted run of another session was spawned as. Invented, and unlike anything the
/// corpus records, so a page that printed it could only have got it from the plant.
const COLLIDED: &str = "planted-collision";

#[tokio::test]
async fn an_address_names_a_run_of_the_sending_session_and_no_other() {
    // A `SendMessage` addresses a run by an id, and an id is one session's word.
    //
    // Claude Code mints a run id per session and nothing makes one unique across a store, so every
    // lookup that turns a `to` into an agent type is scoped to the sending session. No two
    // sessions of the corpus collide — 17 hex characters rarely do — which is why the collision is
    // planted: against the corpus as recorded, a lookup matching on the id alone reads exactly
    // like one that matches on the session too.
    //
    // Four queries resolve an address, one per surface a send is named on, so the plant is read on
    // all four: the NavTree row and the pane's heading on the call's own page, the row in its api
    // call's children log, and the row on the session's errors page — which the second plant
    // reaches by failing the call, the one way onto that page.
    let corpus = Served::corpus();
    let levels = Levels::of(&corpus.db());
    let rows = levels
        .store()
        .fetch(
            "SELECT t.session_id, t.source, t.id AS tool_id, t.api_call_id AS call_id, \
             a.id AS run_id, a.agent_type FROM live_tool_calls t \
             JOIN live_agent_runs a ON a.session_id = t.session_id \
              AND a.id = json_extract_string(t.input, '$.to') \
             WHERE t.name = 'SendMessage' ORDER BY t.session_id, t.source, t.\"index\" LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let sent = rows.first().expect("the corpus records an addressed send");
    let session_id = sent.str("session_id").expect("a session").to_owned();
    let source = sent.str("source").expect("a thread").to_owned();
    let tool_id = sent.str("tool_id").expect("a tool call").to_owned();
    let call_id = sent.str("call_id").expect("its api call").to_owned();
    let run_id = sent.str("run_id").expect("the run addressed").to_owned();
    let agent_type = sent
        .str("agent_type")
        .expect("what it was spawned as")
        .to_owned();
    let elsewhere = levels
        .store()
        .fetch(
            "SELECT id FROM sessions WHERE id <> $session_id ORDER BY id LIMIT 1",
            &[("session_id", session_id.as_str().into())],
        )
        .expect("the store answers")
        .first()
        .expect("the corpus holds a second session")
        .str("id")
        .expect("a session")
        .to_owned();
    let (planted_session, planted_run, planted_tool, planted_at) = (
        session_id.clone(),
        run_id.clone(),
        tool_id.clone(),
        elsewhere.clone(),
    );
    let served = Served::planted(move |store: &Store| {
        let connection = store.connection();
        // The same run id under another session, spawned as something else — the row a lookup
        // that forgot whose id it was reading would find...
        connection
            .execute(
                "INSERT INTO agent_runs (SELECT * REPLACE (? AS session_id, ? AS agent_type) \
                 FROM agent_runs WHERE session_id = ? AND id = ?)",
                params![planted_at, COLLIDED, planted_session, planted_run],
            )
            .expect("the collision lands");
        // ...and the send failed, so the errors page has this call to name.
        connection
            .execute(
                "UPDATE tool_calls SET is_error = true WHERE session_id = ? AND id = ?",
                params![planted_session, planted_tool],
            )
            .expect("the send fails");
    });
    let (_, pane) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/tool/{tool_id}"
        ))
        .await;
    let (_, parent) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/call/{call_id}"
        ))
        .await;
    let (_, failures) = served.page(&format!("/session/{session_id}/errors")).await;
    // Every surface still prints the run this session spawned...
    let named = format!("📬 to {agent_type}");
    let key = format!("tool:{tool_id}");
    assert!(Markup::of(&pane).fields("data-nav-tree", &key)["title"].starts_with(&named));
    assert!(Markup::of(&pane).fields("data-body", "tool")["title"].starts_with(&named));
    assert!(Markup::of(&parent).fields("data-child", &key)["title"].starts_with(&named));
    assert!(Markup::of(&failures).fields("data-error", &key)["title"].starts_with(&named));
    // ...and the other session's word reaches none of them. Read across the whole page rather than
    // off the row: an unscoped join matches twice, and which of the two answers a row — or whether
    // the row is drawn twice — is the database's business and not a contract.
    for page in [&pane, &parent, &failures] {
        assert!(!page.contains(COLLIDED));
    }
    // The heading is the one of the four that reads its query's first row and drops the rest, so a
    // second row it should never have had leaves nothing on the page to see. That query is read as
    // rows instead: one call, one header.
    let reading = Store::open_read_only(&served.db()).expect("the planted store opens");
    let header = page_rows(
        &reading,
        Page::ToolHeader,
        &[
            ("session_id", session_id.as_str().into()),
            ("source", source.as_str().into()),
            ("tool_call_id", tool_id.as_str().into()),
            (
                "head_chars",
                i64::try_from(HEADER_CHARS).expect("a width").into(),
            ),
            (
                "detail_chars",
                i64::try_from(DETAIL_CHARS).expect("a width").into(),
            ),
        ],
    )
    .expect("the header query answers");
    assert_eq!(header.len(), 1);
}

/// Every row of one session whose title the store composes, keyed the way a row is.
///
/// Read off the columns the design names a node from, not off the page: a tool call named by its
/// input alone, or a run named by the definition it ran where its own brief was recorded, is a row
/// pointing at a node the reader did not ask for.
///
/// Each title comes back in two halves: what a surface cuts, and the part that survives the cut —
/// an api call's tool count, which the width is budgeted around rather than spent on.
fn titled(store: &Store, session_id: &str) -> BTreeMap<String, (String, String)> {
    let bound = [("session_id", session_id.into())];
    let mut said: BTreeMap<String, (String, String)> = BTreeMap::new();
    // The one lookup a title reaches outside its own row for: a `SendMessage` addressed by an
    // agent run's id reads as that run's type.
    for row in store
        .fetch(
            "SELECT t.id, t.name, t.input, s.project_dir, a.agent_type FROM live_tool_calls t \
             LEFT JOIN sessions s ON s.id = t.session_id \
             LEFT JOIN live_agent_runs a ON a.session_id = t.session_id \
              AND a.id = json_extract_string(t.input, '$.to') \
             WHERE t.session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        let tool_id = row.str("id").expect("a tool call");
        let name = row.str("name").expect("a tool name");
        let given = row.opt_str("input").expect("an input or none");
        let project = row.opt_str("project_dir").expect("a project or none");
        let addressed = row.opt_str("agent_type").expect("a type or none");
        // A tool the viewer knows names its own calls, under the glyph that stands for it and with
        // no name beside it — the glyph is what a reader picks the call out of a tree by. Any
        // other tool leads with its name, and after it the title that tells two `Read` rows apart.
        let title = match named(name, given, project, addressed) {
            Some(named) => named,
            None => leading(name, &shaped(given, project)),
        };
        said.insert(format!("tool:{tool_id}"), (title, String::new()));
    }
    // The tool calls a call went on to make, in the order it made them: their names, and the input
    // of the first, which is the only one whose own title is shown — with the same address lookup
    // the tool rows above take, because the first call names itself here exactly as it names
    // itself on its own row.
    for row in store
        .fetch(
            "SELECT c.id, c.text, c.model, \
             list(t.name ORDER BY t.\"index\") FILTER (t.id IS NOT NULL) AS tools, \
             min_by(t.input, t.\"index\") AS input, any_value(s.project_dir) AS project_dir, \
             min_by(a.agent_type, t.\"index\") AS agent_type \
             FROM live_api_calls c \
             LEFT JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.api_call_id = c.id \
             LEFT JOIN sessions s ON s.id = c.session_id \
             LEFT JOIN live_agent_runs a ON a.session_id = t.session_id \
              AND a.id = json_extract_string(t.input, '$.to') \
             WHERE c.session_id = $session_id GROUP BY c.id, c.text, c.model",
            &bound,
        )
        .expect("the store answers")
    {
        let call_id = row.str("id").expect("an api call");
        let spoken = row
            .opt_str("text")
            .expect("text or none")
            .unwrap_or_default();
        let tools = row.strings("tools").expect("a list of tool names");
        let key = format!("call:{call_id}");
        // What the call said. Where it said nothing, what it did instead: the tool it called first
        // and that call's own title, then how many of each tool followed. A call that neither
        // spoke nor called a tool is named by the model that was asked.
        if !spoken.is_empty() {
            // Marked as the model's own words, which is the one thing on the row the rest of the
            // page does not say — and marked whether or not the call also ran tools.
            said.insert(key, (format!("💭 {spoken}"), String::new()));
        } else if let Some((first, rest)) = tools.split_first() {
            // Named by the same two rules the tool row under it takes, in the same order: the
            // tool's own rule under its glyph, else its name leading the shape of its input. One
            // derivation for both rows is the point — a reader following a call into the tool it
            // called must not meet a different name at the bottom.
            let given = row.opt_str("input").expect("an input or none");
            let project = row.opt_str("project_dir").expect("a project or none");
            let addressed = row.opt_str("agent_type").expect("a type or none");
            let title = match named(first, given, project, addressed) {
                Some(named) => named,
                None => leading(first, &shaped(given, project)),
            };
            said.insert(key, (title, tallied(rest)));
        } else {
            let model = row.str("model").expect("the model asked").to_owned();
            said.insert(key, (model, String::new()));
        }
    }
    for row in store
        .fetch(
            "SELECT id, trigger FROM live_compactions WHERE session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        let id = row.str("id").expect("a compaction");
        let trigger = row.str("trigger").expect("what triggered it");
        said.insert(
            format!("compaction:{id}"),
            (format!("compaction · {trigger}"), String::new()),
        );
    }
    for row in store
        .fetch(
            "SELECT id, prompt, command_name, command_args FROM live_turns \
             WHERE session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        let id = row.str("id").expect("a turn");
        // The command a turn ran and what followed it, else the prompt as the reader typed it.
        let title = match row.opt_str("command_name").expect("a command or none") {
            Some(command) => format!(
                "{command} {}",
                row.opt_str("command_args")
                    .expect("arguments or none")
                    .unwrap_or_default()
            )
            .trim()
            .to_owned(),
            None => row.str("prompt").expect("a prompt").to_owned(),
        };
        said.insert(format!("turn:{id}"), (title, String::new()));
    }
    for row in store
        .fetch(
            "SELECT id, brief, agent_type FROM live_agent_runs WHERE session_id = $session_id",
            &bound,
        )
        .expect("the store answers")
    {
        let id = row.str("id").expect("a run");
        let kind = row.str("agent_type").expect("what it was spawned as");
        // The definition it ran, always first and in brackets — which agent this was is what a
        // reader picks a run out of a tree by — and after it the brief it was given, where one was
        // recorded. The brackets close the lead, so no dash stands between the two.
        let title = match row.opt_str("brief").expect("a brief or none") {
            Some(brief) => format!("[{kind}] {brief}"),
            None => format!("[{kind}]"),
        };
        said.insert(format!("run:{id}"), (title, String::new()));
    }
    said
}

/// The tools the fixture corpus records that the viewer names by their own field, restated from
/// the design rather than read off the viewer's own formatter table.
const MARKS: [(&str, &str); 6] = [
    ("Read", "📖"),
    ("Bash", "⚡"),
    ("Agent", "👉"),
    ("SendMessage", "📬"),
    ("ToolSearch", "🧰"),
    ("PushNotification", "🔔"),
];

/// What a tool that names its own calls is called, or nothing where it carried nothing to name it
/// by and falls back to the shape rule.
fn named(
    name: &str,
    given: Option<&str>,
    project: Option<&str>,
    addressed: Option<&str>,
) -> Option<String> {
    let fields = asked(given);
    let head = |key: &str| head_of(&fields, key, 0);
    let words = match name {
        // A path, read against the project the way the shape rule reads one.
        "Read" => {
            if head("file_path").is_empty() {
                String::new()
            } else {
                shaped(given, project)
            }
        }
        // What ran, and only its first line: the row is one line and a heredoc is a screenful.
        "Bash" => head("command")
            .split('\n')
            .next()
            .expect("a split yields one piece")
            .to_owned(),
        // The definition the run was spawned as, in brackets, then the brief it was given.
        "Agent" => {
            let (kind, said) = (head("subagent_type"), head("description"));
            if kind.is_empty() {
                said
            } else {
                format!("[{kind}] {said}").trim().to_owned()
            }
        }
        // Who it went to and what it said. `to` holds a run id or a name the caller typed, and
        // `addressed` is the agent type the id resolved to where the session holds that run.
        "SendMessage" => {
            let resolved = clipped(addressed.unwrap_or_default(), NAV_CHARS + 1);
            let who = if resolved.is_empty() {
                head("to")
            } else {
                resolved
            };
            let summary = head("summary");
            match (who.is_empty(), summary.is_empty()) {
                (true, _) => String::new(),
                (false, true) => format!("to {who}"),
                (false, false) => format!("to {who}: {summary}"),
            }
        }
        // What was searched for, and what the notification said: the one field each carries.
        "ToolSearch" => head("query"),
        "PushNotification" => head("message"),
        _ => return None,
    };
    if words.is_empty() {
        return None;
    }
    let mark = MARKS
        .iter()
        .find(|(held, _)| *held == name)
        .expect("every named rule has a mark")
        .1;
    Some(format!("{mark} {words}"))
}

/// What a tool call the viewer knows no rule for is called, restated from its input.
///
/// Restated rather than imported: an oracle sharing the implementation would agree with itself
/// whatever it said. Each field is cut before it is chosen, the way the query cuts it, so a path
/// longer than the column loses its repository prefix off an already-bounded head.
///
/// Every input the corpus holds is a JSON object of strings; a fixture holding anything else reads
/// as no title here and goes red rather than passing quietly.
fn shaped(given: Option<&str>, project: Option<&str>) -> String {
    let fields = asked(given);
    // A path is read with the project directory on top of the width, because the prefix comes off
    // before the cut: what the column shows is a whole width of the part that tells two paths
    // apart. A path the project does not contain takes the plain width instead.
    let room = project.map_or(0, |at| at.chars().count() + 1);
    let path = head_of(&fields, "file_path", room);
    if !path.is_empty() {
        if let Some(under) = project.and_then(|at| path.strip_prefix(&format!("{at}/"))) {
            return under.to_owned();
        }
        return clipped(&path, NAV_CHARS + 1);
    }
    let described = head_of(&fields, "description", 0);
    if !described.is_empty() {
        return described;
    }
    clipped(given.unwrap_or_default(), NAV_CHARS + 1)
}

/// A tool's recorded input as an object of strings, empty where it is anything else.
fn asked(given: Option<&str>) -> BTreeMap<String, String> {
    let Some(Json::Object(held)) = given.and_then(|text| serde_json::from_str::<Json>(text).ok())
    else {
        return BTreeMap::new();
    };
    held.into_iter()
        .filter_map(|(key, value)| match value {
            Json::String(text) => Some((key, text)),
            _ => None,
        })
        .collect()
}

/// One field of an input at the width the query reads it, with `room` on top.
fn head_of(fields: &BTreeMap<String, String>, key: &str, room: usize) -> String {
    fields
        .get(key)
        .map(|value| clipped(value, NAV_CHARS + 1 + room))
        .unwrap_or_default()
}

/// The leading `size` characters of a value — what a cut query selects, before any mark.
fn clipped(value: &str, size: usize) -> String {
    value.chars().take(size).collect()
}

/// A tool name leading the shape of its input, or standing alone where the shape said nothing.
fn leading(name: &str, shape: &str) -> String {
    if shape.is_empty() {
        name.to_owned()
    } else {
        format!("{name}{LEAD_SEPARATOR}{shape}")
    }
}

/// The count of each tool after the first, restated from the tool names the store holds.
///
/// In the order each tool first appears among them, which is the order the calls were made. No
/// cut: no recorded call invokes enough distinct tools to reach the tally's own width, and a
/// corpus that grew one would go red here rather than pass on a shortened count.
fn tallied(names: &[&str]) -> String {
    let mut counted: Vec<(&str, usize)> = Vec::new();
    for name in names {
        match counted.iter_mut().find(|(held, _)| held == name) {
            Some((_, made)) => *made += 1,
            None => counted.push((name, 1)),
        }
    }
    counted
        .into_iter()
        .map(|(name, made)| format!(" +{made}({name})"))
        .collect()
}
