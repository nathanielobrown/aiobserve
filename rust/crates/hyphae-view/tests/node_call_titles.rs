//! What an api call is called when it answered with tool calls rather than words.
//!
//! A call's text is what it is normally named by. A call that ran tools and said nothing has
//! none, so the tools are the record's own answer to which call this was: the first one's title,
//! and how many of each tool followed it. What a tool call is called is `node_titles.rs`.

use std::collections::BTreeSet;

use duckdb::params;

use hyphae_store::{Param, Store, queries};
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;
use hyphae_testsupport::tools;
use hyphae_view::format::{ELLIPSIS, cut};
use hyphae_view::formatters::NAMED;
use hyphae_view::nodes::{LEAD_SEPARATOR, SPEECH_MARK, TALLY_CHARS};

/// The registry's names as a SQL list, for the two halves of the rule: a first tool the registry
/// names leads the call with its glyph, and one it does not leads with the tool's own name.
fn registered() -> String {
    let names = NAMED
        .iter()
        .map(|name| format!("'{name}'"))
        .collect::<Vec<_>>()
        .join(", ");
    format!("({names})")
}

/// Every tool a call ran, in the order it ran them.
fn called(db: &std::path::Path, session_id: &str, source: &str, call_id: &str) -> Vec<String> {
    rows::all(
        db,
        "SELECT name FROM live_tool_calls WHERE session_id = $session AND source = $source \
         AND api_call_id = $call ORDER BY \"index\"",
        &[
            ("session", Param::from(session_id)),
            ("source", Param::from(source)),
            ("call", Param::from(call_id)),
        ],
    )
    .into_iter()
    .map(|row| row.str("name").expect("a tool name").to_owned())
    .collect()
}

#[tokio::test]
async fn an_api_call_that_answered_with_tool_calls_is_named_by_what_it_called() {
    // A call that said nothing is named by what it did instead, wherever the viewer names it.
    //
    // Its text is what a call is normally called, and a call that answered with tool calls and no
    // words has none — so the row read as the model that answered, and a turn of them read as a
    // column of one repeated string. The tools it called are the record's own answer to which
    // call this was: the first one's title, and how many of each tool followed it.
    //
    // A call that *did* speak is named by its words instead, under 💭 — the one glyph on a thread
    // that says a row is the model talking rather than the viewer describing what it did. Both
    // halves are read here because the mark hangs off the words and not off the absence of tools:
    // a call that spoke and then ran four tools is marked too.
    //
    // Read off the store rather than pinned, like every other selection here. What is pinned is
    // the agreement: the pane's heading, the NavTree row beside it and the browser tab print one
    // string, because one derivation composes it from two queries at two widths.
    let served = Served::corpus();
    let db = served.db();
    let row = rows::one(
        &db,
        &format!(
            "SELECT c.session_id, c.source, c.id, c.turn_id, c.model FROM live_api_calls c \
             JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source \
             AND t.api_call_id = c.id \
             WHERE (c.text IS NULL OR c.text = '') AND c.turn_id IS NOT NULL \
             GROUP BY 1, 2, 3, 4, 5 HAVING min_by(t.name, t.\"index\") NOT IN {} \
             ORDER BY count(*) DESC, c.id LIMIT 1",
            registered()
        ),
        &[],
    );
    let session_id = row.str("session_id").expect("a session id");
    let source = row.str("source").expect("a thread");
    let call_id = row.str("id").expect("a call id");
    let turn_id = row.str("turn_id").expect("a turn id");
    let model = row.str("model").expect("a model");
    let names = called(&db, session_id, source, call_id);
    let (_, page) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/call/{call_id}"
        ))
        .await;
    let markup = Markup::of(&page);
    let titled = markup.field("data-body", "call", "title");
    // The tool it called first leads, the way a run's agent type leads: which tool this was is
    // what a reader picks the call out of a tree by. After it, that call's own title. The call is
    // chosen for a first tool the registry does not name, which is what leaves the name in the
    // lead — the arm below is the other one.
    assert!(
        titled.starts_with(&format!("{}{LEAD_SEPARATOR}", names[0])),
        "{titled}"
    );
    // And after that, the tools it went on to call, counted once per tool.
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    let tally: String = names[1..]
        .iter()
        .filter(|name| seen.insert(name.as_str()))
        .map(|name| format!(" +1({name})"))
        .collect();
    assert!(titled.ends_with(&tally), "{titled}");
    // The three surfaces that name the node agree, at three widths and off two queries.
    assert_eq!(
        markup.field("data-nav-tree", &format!("call:{call_id}"), "title"),
        titled
    );
    assert!(page.contains(&format!("<title>⇄ {titled} ·")), "{titled}");
    // The one documented exception stands: the children log under the turn names its api-call
    // rows by the model that answered, with what each said in a column of its own beside it.
    let (_, log) = served
        .page(&format!(
            "/session/{session_id}/thread/{source}/turn/{turn_id}"
        ))
        .await;
    assert_eq!(
        Markup::of(&log).field("data-child", &format!("call:{call_id}"), "model"),
        model
    );

    // And where the registry does name that first tool, its glyph leads the call's title in place
    // of the tool's name — the api call is named by the derivation that names the tool row under
    // it, so the mark a reader picks a `Read` out of a tree by survives the hop up one level. The
    // shortest recorded input, so both surfaces print the title whole.
    let glyphed = rows::one(
        &db,
        &format!(
            "SELECT c.session_id, c.source, c.id, min_by(t.id, t.\"index\") AS first_tool, \
             min_by(t.name, t.\"index\") AS first_name FROM live_api_calls c \
             JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source \
             AND t.api_call_id = c.id \
             WHERE (c.text IS NULL OR c.text = '') \
             GROUP BY 1, 2, 3 HAVING min_by(t.name, t.\"index\") IN {} \
             ORDER BY length(min_by(t.input, t.\"index\")), c.id LIMIT 1",
            registered()
        ),
        &[],
    );
    let thread = format!(
        "/session/{}/thread/{}",
        glyphed.str("session_id").expect("a session id"),
        glyphed.str("source").expect("a thread"),
    );
    let first_name = glyphed.str("first_name").expect("a tool name");
    let (_, tool_page) = served
        .page(&format!(
            "{thread}/tool/{}",
            glyphed.str("first_tool").expect("a tool id")
        ))
        .await;
    let (_, call_page) = served
        .page(&format!(
            "{thread}/call/{}",
            glyphed.str("id").expect("a call id")
        ))
        .await;
    let named = Markup::of(&tool_page).field("data-body", "tool", "title");
    let leading = Markup::of(&call_page).field("data-body", "call", "title");
    assert!(!named.ends_with(ELLIPSIS), "{named}");
    // The tool's own title, whole, then whatever the call went on to do after it...
    assert!(leading.starts_with(&named), "{named} / {leading}");
    // ...and what leads both is the glyph, not the name the row above spells out.
    assert!(
        named.starts_with(&format!("{} ", tools::glyph(first_name))),
        "{named}"
    );
    assert!(!leading.starts_with(first_name), "{leading}");

    // The other half of the rule, on a call the corpus records rather than a planted one: a call
    // whose answer was words carries the mark that says the row is the model speaking. It is
    // picked from the calls that *also* ran tools, which is the case a mark hung off "this call
    // did nothing else" would miss — and the silent call above carries no mark.
    assert!(!titled.contains(SPEECH_MARK), "{titled}");
    let spoke = rows::one(
        &db,
        "SELECT c.session_id, c.source, c.id, c.text FROM live_api_calls c \
         JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source \
         AND t.api_call_id = c.id \
         WHERE c.text IS NOT NULL AND c.text <> '' \
         GROUP BY 1, 2, 3, 4 ORDER BY count(*) DESC, c.id LIMIT 1",
        &[],
    );
    let spoke_call = spoke.str("id").expect("a call id");
    let said = format!("{SPEECH_MARK} {}", spoke.str("text").expect("the words"));
    let (_, page) = served
        .page(&format!(
            "/session/{}/thread/{}/call/{spoke_call}",
            spoke.str("session_id").expect("a session id"),
            spoke.str("source").expect("a thread"),
        ))
        .await;
    let markup = Markup::of(&page);
    assert_eq!(
        markup.field("data-body", "call", "title"),
        cut(&said, queries::HEADER_CHARS)
    );
    assert_eq!(
        markup.field("data-nav-tree", &format!("call:{spoke_call}"), "title"),
        cut(&said, queries::NAV_CHARS)
    );
}

#[tokio::test]
async fn the_count_of_a_calls_tools_survives_every_width_the_title_is_cut_to() {
    // The count is budgeted out of the width first, so a long first title cannot push it off.
    //
    // Cut the other way round — title first, count into whatever is left — and the rows that most
    // need the count are the rows that lose it: a call whose first tool call has plenty to say is
    // a call that made several. What a reader would see is a title that stops, with no sign the
    // call did anything after it.
    //
    // Planted on a recorded call that called `Bash` once and `Read` twice, by emptying the one
    // column that decides which name the derivation falls through to — the store forbids a NULL
    // there, and a call that answered with tools alone is recorded with an empty string.
    // Redaction left the corpus no call that both said nothing and called one tool twice.
    let corpus = Served::corpus();
    let row = rows::one(
        &corpus.db(),
        "SELECT c.session_id, c.source, c.id, min_by(t.id, t.\"index\") AS first_tool \
         FROM live_api_calls c \
         JOIN live_tool_calls t ON t.session_id = c.session_id AND t.source = c.source \
         AND t.api_call_id = c.id \
         GROUP BY 1, 2, 3 \
         HAVING count(*) > count(DISTINCT t.name) AND count(DISTINCT t.name) > 1 \
         ORDER BY count(*) DESC, c.id LIMIT 1",
        &[],
    );
    let session_id = row.str("session_id").expect("a session id").to_owned();
    let source = row.str("source").expect("a thread").to_owned();
    let call_id = row.str("id").expect("a call id").to_owned();
    let tool_id = row.str("first_tool").expect("a tool id").to_owned();
    let url = format!("/session/{session_id}/thread/{source}/call/{call_id}");

    // Every plant starts by silencing the call, which is what makes it named by its tools; the
    // second statement is what each arm below is about.
    let silenced = |also: Option<(&'static str, Vec<String>)>| {
        let call_id = call_id.clone();
        move |store: &Store| {
            let connection = store.connection();
            connection
                .execute(
                    "UPDATE api_calls SET text = '' WHERE id = ?",
                    params![call_id],
                )
                .expect("the call says nothing");
            if let Some((sql, bound)) = &also {
                connection
                    .execute(sql, duckdb::params_from_iter(bound))
                    .expect("the plant lands");
            }
        }
    };

    let served = Served::planted(silenced(None));
    let (_, page) = served.page(&url).await;
    // Two `Read` calls after the `Bash` that leads, counted as one group rather than listed.
    assert!(
        Markup::of(&page)
            .field("data-body", "call", "title")
            .ends_with(" +2(Read)"),
        "{url}"
    );

    // The same call with a first tool call that fills a title on its own — a command long enough
    // to run past every width. Each of those widths is spent on the command less the count, so
    // both ends survive: what the call did first, marked where it was stopped, and how many
    // followed.
    let asked = "w".repeat(queries::NAV_CHARS * 2);
    let described = format!(r#"{{"command": "{asked}", "description": "Run the long one"}}"#);
    let served = Served::planted(silenced(Some((
        "UPDATE tool_calls SET name = 'Bash', input = ? WHERE id = ?",
        vec![described, tool_id.clone()],
    ))));
    let (_, page) = served.page(&url).await;
    let markup = Markup::of(&page);
    let tally = " +2(Read)";
    for (where_, chars) in [
        ("data-body", queries::HEADER_CHARS),
        ("data-nav-tree", queries::NAV_CHARS),
    ] {
        let key = if where_ == "data-body" {
            "call".to_owned()
        } else {
            format!("call:{call_id}")
        };
        let shown = markup.field(where_, &key, "title");
        let head: String = format!("⚡ {asked}")
            .chars()
            .take(chars - tally.chars().count())
            .collect();
        assert_eq!(shown, format!("{head}{ELLIPSIS}{tally}"), "{where_}");
    }

    // The cap is a fit, not a ceiling to stay under: a tally that lands exactly on it keeps every
    // group. Two tools named at half the cap each is the boundary the drop is decided at, and one
    // character either side of it decides differently.
    let wide = TALLY_CHARS / 2 - " +1()".chars().count();
    let mut stem = "mcp__fits_the_cap_".to_owned();
    while stem.chars().count() < wide - 1 {
        stem.push('_');
    }
    let stem: String = stem.chars().take(wide - 1).collect();
    let served = Served::planted(silenced(Some((
        "UPDATE tool_calls SET name = ? || \"index\" \
         WHERE session_id = ? AND api_call_id = ?",
        vec![stem.clone(), session_id.clone(), call_id.clone()],
    ))));
    let (_, page) = served.page(&url).await;
    let exactly: String = (1..=2).map(|index| format!(" +1({stem}{index})")).collect();
    assert_eq!(
        exactly.chars().count(),
        TALLY_CHARS,
        "the plant does not land on the cap"
    );
    assert!(
        Markup::of(&page)
            .field("data-body", "call", "title")
            .ends_with(&exactly)
    );

    // The count is bounded in its turn, because it is the half no width cuts. A call that invoked
    // a handful of tools with names as long as an MCP tool's would otherwise spend a whole NavTree
    // row on counts. Whole groups go rather than half a name: `+1(mcp__…` counts calls of a tool
    // the reader cannot identify.
    let served = Served::planted(silenced(Some((
        "UPDATE tool_calls SET name = 'mcp__a_long_server_name__tool_' || \"index\" \
         WHERE session_id = ? AND api_call_id = ?",
        vec![session_id.clone(), call_id.clone()],
    ))));
    let (_, page) = served.page(&url).await;
    let counted = Markup::of(&page).field("data-body", "call", "title");
    // The `Bash` that leads is now the first of three long names, and one of the two after it
    // fits under the cap. The other is gone, and the mark says a count was left behind.
    let kept = "mcp__a_long_server_name__tool_1";
    let dropped = "mcp__a_long_server_name__tool_2";
    assert!(
        counted.starts_with(&format!("mcp__a_long_server_name__tool_0{LEAD_SEPARATOR}")),
        "{counted}"
    );
    assert!(
        counted.ends_with(&format!(" +1({kept}){ELLIPSIS}")) && !counted.contains(dropped),
        "{counted}"
    );
    assert!(
        format!(" +1({kept}) +1({dropped})").chars().count() > TALLY_CHARS,
        "the plant did not overflow"
    );
}
