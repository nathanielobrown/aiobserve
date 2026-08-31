//! What every page, row and mark of the viewer was measured at, and the arithmetic over it.
//!
//! Ported from `tests/view/budgets.py`. A plain module rather than a fixture: each number here is
//! a measurement taken against a rendered page, and the leaves that spend them read them as
//! constants — behind a fixture the arithmetic would be a request rather than a sum you can
//! follow. `hyphae-view/tests/bounds_*.rs` are the callers; `hyphae-view/src/knobs.rs` holds the
//! caps the app itself enforces.
//!
//! Every measured pin was re-taken against *this* viewer's markup rather than carried from the
//! Python: hypertext writes the same elements but not always the same bytes, so a pin lifted
//! across would be a number nothing here measured.

use std::env;

use hyphae_store::queries;
use hyphae_view::knobs::{self, DEPTH, DETAIL, KIN, LOG, PROJECTS, RECORDS, SESSIONS};
use hyphae_view::listing::SHOWN;
use hyphae_view::nodes::Preset;
use hyphae_view::render::escape;
use hyphae_view::store::{Page, Query};

/// What a page may weigh. The list is the page a corpus grows, so [`SESSIONS`]`.ceiling` rows of
/// what one row can hold have to fit under it. Wide enough that the widest forest the corpus
/// records is not put behind a "+N more" nobody can open.
pub const PAGE_BYTES: usize = 500_000;

/// What a node page may weigh, which is its own budget rather than [`PAGE_BYTES`].
///
/// The NavTree is [`DEPTH`] levels of [`KIN`] children, so the window a level opens on prices
/// about four fifths of the page — and it is a window, not a limit: a tail row fetches what it
/// left out. Pinning it here rather than against `PAGE_BYTES` keeps a reader reaching further per
/// click off the list pages, whose ceilings are derived against that number. [`worst_node_bytes`]
/// is the arithmetic under it, and [`node_spare`] is what the rounding leaves.
pub const NODE_BYTES: usize = 6_500_000;

/// What one expansion may weigh: a node's body opened in place, inside someone else's children
/// log.
///
/// Over [`PAGE_BYTES`] and declared here rather than derived against it, for the reason
/// [`knobs::OPENED_RECORD_CHARS`] draws the same line the other way — a reader clicked. What
/// bounds it is the `?log=` cap the reader is already reading under rather than a second cap under
/// that, and naming the number is what stops a [`LOG`] ceiling raised past 100 spending it a row
/// at a time.
pub const EXPANSION_BYTES: usize = 625_000;

/// Set this to read every measured pin below as the measurement itself rather than as a ceiling
/// over it.
///
/// Everyday runs read them one-sidedly on purpose: a change that shrinks a page should not have to
/// re-pin the page. A change that moves bytes deliberately runs the suite under
/// `HYPHAE_PIN_EXACT=1` instead, where a pin a page no longer reaches is a failure naming both
/// numbers — which is what makes a re-pin something a run reds on rather than a step someone
/// remembers.
pub const EXACT_PIN: &str = "HYPHAE_PIN_EXACT";

/// Whether this run reads a measured pin as the measurement itself rather than a ceiling.
pub fn exact_pins() -> bool {
    env::var_os(EXACT_PIN).is_some_and(|set| !set.is_empty())
}

/// Whether a measurement fits its pin — and under [`EXACT_PIN`], whether it *is* its pin.
pub fn fits(measured: usize, budget: usize) -> bool {
    if exact_pins() {
        measured == budget
    } else {
        measured <= budget
    }
}

/// What the markup around one row of the session list costs, with the content the row carries
/// taken off: the stacked cells, the counted lists, the enrichment block and the row around them.
/// Re-measured through the app by `bounds_lists.rs`, every cap full of `&`, at the dearest row the
/// list holds rather than at whichever one sorted second.
pub const MEASURED_SESSION_ROW_MARKUP: usize = 1_200;

/// What the markup around one row's enrichment costs on top of that, with the model's own words
/// taken off — the share of the row above that the block holds rather than a measurement of its
/// own. The list never renders the stale tag, so this is the two tags and the block around them.
pub const MEASURED_LIST_ENRICHMENT_MARKUP: usize = 300;

/// What a list page weighs apart from its rows: the filter form, the project suggestions, the
/// table head and the two pagers. Measured through the app by `bounds_lists.rs` with `&` planted
/// in every suggestion and the box at its cap — a worst case rather than a corpus observation,
/// because the box is bound in SQL like everything else. Pinned at what it measured rather than
/// over it: [`EXACT_PIN`] is what re-pins it.
pub const MEASURED_LIST_CHROME: usize = 8_998;

/// What the markup around one row of the landing page costs, with the path it carries taken off:
/// the stacked window cells and the row around them.
pub const MEASURED_PROJECT_ROW_MARKUP: usize = 1_300;

/// And what that page weighs apart from its rows: the table head, and the line saying how many
/// projects it left out. Small because the page carries no form, no pager and no suggestions.
pub const MEASURED_PROJECTS_CHROME: usize = 1_227;

/// The same two for the page that lists where a session failed, whose row is a link to the failed
/// tool call's own page, the thread it ran on and a timestamp.
pub const MEASURED_ERROR_ROW_MARKUP: usize = 400;

/// What that page weighs apart from its rows, small for the same reason the landing page's is.
pub const MEASURED_ERRORS_CHROME: usize = 1_280;

/// What an expansion carries outside the rows it lists: the node's own body, the link to its page,
/// and the queries it cites. The body's facts are read at [`queries::HEADER_CHARS`] rather than at
/// the reader's `?detail=` — an expansion previews no fat value — so this is a fraction of the
/// chrome a page carries. Measured over all three kinds a log opens a body for; an api call's is
/// the dearest, because its body stands above a table and its title is the head of what it said.
pub const MEASURED_EXPANSION_CHROME: usize = 4_664;

/// What a row of the records browser really costs — the preview plus the row's own markup, most of
/// it the `hx-get` that fetches the record whole. Measured against `data/traces.duckdb` over every
/// thread it holds of a hundred records or more, as the difference between a page of a hundred and
/// a page of one. It is the canonical store this needs: the fixture records are redacted to a few
/// characters and project nothing about a real transcript line.
pub const MEASURED_RECORD_BYTES: usize = 800;

/// What the markup around one row of the pane's children log costs, with the strings it carries
/// taken off: a cell per column of the shape's own table, three copies of the node's URL — the
/// link, the `hx-get` behind it, and the mount the View button opens through — the swap the link
/// performs, the numbers that tell two children apart, and the row around them. The dearest row is
/// an api call's: nine columns against a tool row's seven, and the same three strings.
pub const MEASURED_LOG_ROW_MARKUP: usize = 1_500;

/// How many strings one row of a children log prints, each cut to [`queries::LOG_CHARS`] and
/// selected a character past it.
///
/// Three is the widest row there is: an api call's is the model that answered, the head of what it
/// said, and the tools it went on to call; a tool row is the tool's name, the head of what it was
/// asked, and the command that head describes. A turn row prints one and a run two. Listed rather
/// than counted off the column registry, because most of those columns are a number or a stamp;
/// what keeps the number honest is `bounds_node.rs`, which plants every string a row can print
/// past its cut and weighs the row.
pub const LOG_ROW_STRINGS: usize = 3;

/// What the control under a children log costs, with both of its links rendered: the nav around
/// them, the place between them, and two copies of the node's own URL carrying the page's knobs
/// and a page number. Nearly all of it is those two URLs. Measured on logs driven to one row a
/// page and read at a middle page, which is the only page carrying both links.
pub const MEASURED_PAGER_BYTES: usize = 565;

/// What the markup around one crumb of the chain down to the selection costs: the link, the node's
/// key, the mark saying what kind of node the step is, and the glyph saying who named it.
pub const MEASURED_CRUMB_MARKUP: usize = 306;

/// What the markup around one previewed value costs — the heading, the `<pre>` and the line
/// offering the rest of it — with the preview itself taken off.
pub const MEASURED_DETAIL_MARKUP: usize = 550;

/// How many fat values one pane previews at once.
///
/// Three is the most any kind shows: a `Bash` call previews the command it ran, the arguments it
/// was passed and what came back, and an api call what it said and what it thought. A fourth would
/// be a kind whose pane the arithmetic below has not priced.
pub const PANE_DETAILS: usize = 3;

/// And how many of those the page marks up rather than printing as the characters the store holds.
///
/// Three, which is a run's pane: the brief it was named by, the prompt it was given and the answer
/// it sent back, all written by a person or a model. A tool's pane asks for a syntax on all three,
/// but a value that parses as none of them prints as stored — so what a tool's pane spends is a
/// question about the call rather than about the kind.
pub const DEAR_PANE_DETAILS: usize = 3;

/// What a node page carries outside its NavTree rows, its log rows and its previews: the crumbs
/// down to the selection, the node's own facts, and what a pass said about it.
///
/// The session is the widest of the eight panes — every string in its header is one a transcript
/// wrote, and its two lists grow with the session — so the allowance is a session header's, cut in
/// SQL. The preset control rides here too, the children log's table head, the two steps above the
/// crumb chain, the enrichment with the fetch each of its lines offers, and on a pane reading a
/// failed tool call the step to the failure before it and the one after. Up to five of its strings
/// are tree titles, so it moves with [`queries::NAV_CHARS`]. Pinned at what it measured:
/// [`EXACT_PIN`] is what re-pins it.
pub const MEASURED_NODE_CHROME: usize = 17_748;

/// The parameter a string's head is cut to in the list's own composition.
pub const LIST_HEAD: &str = "$head_chars";

/// And the one a skill name or an agent type is cut to, which is a member's width.
pub const LIST_ITEM_HEAD: &str = "$item_chars";

/// And the one a kind of work is cut to, which is a tag's head rather than a name's: the
/// categories a pass writes come from a taxonomy, not from a transcript.
pub const LIST_KIND_HEAD: &str = "$kind_chars";

/// The most one character of a transcript's own content can weigh on the page that shows it.
///
/// Content has no shape at all — a tool wrote the file, a model wrote the text — so every bound
/// over it holds for the worst character rather than the measured average. The longest escape
/// [`escape`] writes is five bytes (`&amp;`, `&#34;`, `&#39;`) and the longest UTF-8 encoding is
/// four, so five bytes a character covers both.
pub const ESCAPED_CHAR_BYTES: usize = 5;

/// What the mark on a cut value costs, once per cut column: three bytes of UTF-8 and no escape.
pub const MARK_BYTES: usize = hyphae_view::format::ELLIPSIS.len();

/// And the most one character can weigh where the page marks it up in its own syntax: a
/// `<span class="` of 13, a class of 3, a `">` of 2, a `</span>` of 7, and the character itself
/// escaped to 5.
///
/// A construction bound rather than a measurement, because what a lexer makes a token of is a
/// property of the lexer and a value every character of which is its own token costs the lot. The
/// class is three characters because the class table holds it there. The dearest content the
/// viewer marks up today reaches 26 bytes a character (`&;` repeated, read as `.sql` or `.py`)
/// without any lexer being adversarial, and a preview rendered as the markdown it was written in
/// reaches the same number the same way: a fenced block goes through these lexers, and every other
/// construct markdown has costs its tags once a line.
pub const MARKED_CHAR_BYTES: usize = 30;

/// And the most one character can weigh where a page writes it into a link rather than into text.
/// Percent-encoding spends three bytes on every byte it escapes, and a character is up to four
/// bytes of UTF-8: a project path is a directory someone named, so its link is budgeted at the
/// worst character the same way its cell is.
pub const ENCODED_CHAR_BYTES: usize = 12;

/// How many of a statement's columns are cut to `parameter` — what one of its rows carries.
pub fn heads(sql: &str, parameter: &str) -> usize {
    sql.lines()
        .map(|line| line.split_once("--").map_or(line, |(kept, _)| kept))
        .map(|line| line.matches(parameter).count())
        .sum()
}

/// What one row of the session list can weigh: its markup, and every head it shows all `&`.
///
/// The heads are counted off the composition rather than listed, so a column added to what a row
/// shows lands in the arithmetic instead of quietly spending the ceiling [`SESSIONS`]`.ceiling`
/// times over. A described row is what this budgets — the enrichment the list joins is a column of
/// the row like the rest — which is why the description takes a row's head and not the page's
/// larger one.
pub fn worst_session_row_bytes() -> usize {
    let said = queries::load(Page::DescribedSessions.stem());
    let shown = heads(SHOWN, LIST_HEAD);
    let written = heads(said, LIST_HEAD);
    let strings = shown * queries::LIST_CHARS;
    // The skill names are cut in the composition and the agent types in the query itself — a type
    // is grouped after its cut, so the cut has to be where the grouping can see it.
    let listed =
        heads(SHOWN, LIST_ITEM_HEAD) + heads(queries::load(Page::Sessions.stem()), LIST_ITEM_HEAD);
    let members = listed * queries::LIST_ITEMS;
    let names = members * queries::LIST_ITEM_CHARS;
    let described = written * queries::LIST_CHARS;
    let kinds = heads(said, LIST_KIND_HEAD) * queries::LIST_CATEGORIES * queries::TAG_CHARS;
    MEASURED_SESSION_ROW_MARKUP
        + (strings + names + described + kinds) * ESCAPED_CHAR_BYTES
        // Every value a transcript or a pass wrote is marked where it was cut — the two heads a
        // row shows, each member of its two lists, and the pass's own line — one mark per cut,
        // outside the escape, since an ellipsis is three bytes of UTF-8 and nothing escapes it.
        // The kinds of work are the one cut column with no mark: their vocabulary is closed and
        // its longest member is short of the cut, so that cut is a bound this arithmetic needs
        // rather than one a value reaches.
        + (shown + members + written) * MARK_BYTES
        + MEASURED_LIST_ENRICHMENT_MARKUP
        + worst_tag_bytes()
}

/// What one row of the landing page can weigh: its markup, and the path it carries twice.
///
/// A project path is a directory someone chose, so both copies are counted at the worst character
/// — once escaped into the cell, once percent-encoded into the link that narrows the list to it.
/// Everything else in the row is the store's own arithmetic: two counts, three costs and a
/// timestamp, each as long as its type allows and no longer.
pub fn worst_project_row_bytes() -> usize {
    MEASURED_PROJECT_ROW_MARKUP + queries::LIST_CHARS * (ESCAPED_CHAR_BYTES + ENCODED_CHAR_BYTES)
}

/// What one row of a session's errors list can weigh: its markup, and a title of `&`.
///
/// A row is a link to the failed tool call, named the way a NavTree row names it, beside the
/// thread it ran on and the clock. The thread is an agent id the store minted and the timestamp is
/// as long as its type allows; only the title is text a transcript wrote.
pub fn worst_error_row_bytes() -> usize {
    MEASURED_ERROR_ROW_MARKUP + queries::NAV_CHARS * ESCAPED_CHAR_BYTES
}

/// What the taxonomy tags beside a described item can weigh, all `&`. Two of them — category and
/// outcome — and the third says the row is stale, which is words of ours rather than of the
/// store's and rides in the markup measured above.
pub fn worst_tag_bytes() -> usize {
    2 * queries::TAG_CHARS * ESCAPED_CHAR_BYTES
}

/// What the sizes a URL carries add to one link on the page it serves.
///
/// Every link a node page writes repeats the knobs the request was made with, so a reader who
/// narrows a page pays for the query string on every row of it. The longest one takes the longest
/// preset name beside the widest size that is not a default in each of the three — one under the
/// ceiling, which is where a size stops being silent and starts being written. Escaped, because
/// the `&` between two of them is written into an attribute.
///
/// `?kin=` is priced here rather than left at its default, which is a byte a link cheaper but a
/// whole level of rows dearer. The arithmetic prices the dearest row any size produces against the
/// most rows any size produces — one size cannot do both, and the gap is an allowance kept whole.
pub fn worst_knob_bytes() -> usize {
    let widest = Preset::ALL
        .into_iter()
        .max_by_key(|preset| preset.word().len())
        .expect("the viewer offers presets");
    let marks = knobs::knobs(widest, KIN.ceiling - 1, LOG.ceiling - 1, DETAIL.ceiling - 1);
    escape(&marks).len()
}

/// What one row of the pane's children log can weigh: its markup and the strings it prints.
///
/// A log row is a link, the numbers that tell two children apart, and the strings the store wrote
/// — a turn's title, the model a call ran on, the tool a call called and the head of what it was
/// asked. Every one of them is cut to a log column's width in the query that selects it, a
/// character past the cut so a row that fills its column says so.
pub fn worst_log_row_bytes() -> usize {
    MEASURED_LOG_ROW_MARKUP
        + LOG_ROW_STRINGS * (queries::LOG_CHARS + 1) * ESCAPED_CHAR_BYTES
        // A row links where it fetches and mounts where it expands, so it carries the knobs three
        // times.
        + 3 * worst_knob_bytes()
}

/// What one crumb of the chain above a node can weigh: its markup, a title of `&`, and the knobs
/// its link carries once. A crumb's own width and not a row's: a chain is many nodes on one line
/// and cuts narrower than anything else that names one.
pub fn worst_crumb_bytes() -> usize {
    MEASURED_CRUMB_MARKUP + queries::CRUMB_CHARS * ESCAPED_CHAR_BYTES + worst_knob_bytes()
}

/// What one previewed value printed as stored can weigh: its markup, and a preview of `&`.
pub fn worst_stored_detail_bytes() -> usize {
    MEASURED_DETAIL_MARKUP + DETAIL.ceiling as usize * ESCAPED_CHAR_BYTES
}

/// What one previewed value the page marks up can weigh: its markup, and a preview whose every
/// character costs an element.
///
/// One price for the two ways a preview is marked up. A value in the syntax the record named is a
/// span a token; a value rendered as the markdown it was written in reaches the same lexers
/// through a fenced block, and every other construct markdown has costs its tags once a line
/// rather than once a character.
pub fn worst_rendered_detail_bytes() -> usize {
    MEASURED_DETAIL_MARKUP + DETAIL.ceiling as usize * MARKED_CHAR_BYTES
}

/// How many rows the NavTree of one node page holds: the root, and every level at its window.
///
/// [`KIN`] children per level is the whole of it: the window keeps the child the path descends
/// through inside it rather than past it. A rescue that added a row would put a level at
/// `KIN + 1` and the page below what it prices.
pub fn nav_tree_rows() -> usize {
    1 + DEPTH * (KIN.ceiling as usize + 1)
}

/// What the NavTree of one node page can weigh — four fifths of the page it opens.
pub fn worst_nav_tree_bytes() -> usize {
    nav_tree_rows() * knobs::NAV_TREE_ROW_BYTES
}

/// What the chain above the reading pane can weigh: a crumb a level, all the way down.
pub fn worst_crumbs_bytes() -> usize {
    DEPTH * worst_crumb_bytes()
}

/// What one page of a children log can weigh, on a node page or inside an expansion.
pub fn worst_log_bytes() -> usize {
    LOG.ceiling as usize * worst_log_row_bytes()
}

/// What the values one reading pane previews can weigh, each priced by how it is printed.
pub fn worst_details_bytes() -> usize {
    (PANE_DETAILS - DEAR_PANE_DETAILS) * worst_stored_detail_bytes()
        + DEAR_PANE_DETAILS * worst_rendered_detail_bytes()
}

/// The largest node page any sizes a URL can carry produce.
///
/// A page is its chrome, the crumbs down to the selection, the NavTree beside it, the values the
/// pane previews, and the log under it. The NavTree is the part that multiplies: every level of
/// the open path admits [`KIN`] children and a tail row saying what the cap left out, and the path
/// runs [`DEPTH`] levels deep — so [`knobs::NAV_TREE_ROW_BYTES`] is most of the ceiling, and the
/// row is pinned rather than budgeted.
///
/// The sizes' own defaults spend it, and each of the three knobs only goes down from there — but a
/// knob a reader turns down writes itself into every link on the page, so the rows are priced with
/// the longest query string one can carry rather than with none.
pub fn worst_node_bytes() -> usize {
    MEASURED_NODE_CHROME
        + worst_crumbs_bytes()
        + worst_nav_tree_bytes()
        + worst_log_bytes()
        + MEASURED_PAGER_BYTES
        + worst_details_bytes()
}

/// What one expansion opened in a children log can weigh.
///
/// A body where the page has its NavTree and its crumbs, and under it the level the node's own
/// page lists — the same log, at the same `?log=` cap and one column narrower, because no row
/// inside an expansion opens another. [`EXPANSION_BYTES`] is what this is checked against.
pub fn worst_expansion_bytes() -> usize {
    MEASURED_EXPANSION_CHROME + worst_log_bytes()
}

/// What a full page of the session list can weigh: its chrome, and a ceiling of dear rows.
pub fn worst_session_list_bytes() -> usize {
    MEASURED_LIST_CHROME + SESSIONS.ceiling as usize * worst_session_row_bytes()
}

/// The same for the landing page, whose rows are paths someone named.
pub fn worst_projects_page_bytes() -> usize {
    MEASURED_PROJECTS_CHROME + PROJECTS.ceiling as usize * worst_project_row_bytes()
}

/// The same for a session's errors, which grows with how often its tools failed.
pub fn worst_errors_page_bytes() -> usize {
    MEASURED_ERRORS_CHROME + knobs::ERRORS.ceiling as usize * worst_error_row_bytes()
}

/// The same for the records browser, which is rows of previews and nothing else.
pub fn worst_records_page_bytes() -> usize {
    RECORDS.ceiling as usize * worst_record_bytes()
}

/// What the record that page opens for a reader who did not click it can weigh.
///
/// Priced as a page rather than as the per-value fetch it goes to: every character its own
/// token, plus the indentation a JSON record gains, which is whitespace and written out bare.
pub fn worst_opened_record_bytes() -> usize {
    knobs::OPENED_RECORD_CHARS * MARKED_CHAR_BYTES + knobs::INDENT_CHARS
}

/// What one row of the records browser can weigh: its markup, and a preview of `&`.
pub fn worst_record_bytes() -> usize {
    MEASURED_RECORD_BYTES - queries::RECORD_PREVIEW + queries::RECORD_PREVIEW * ESCAPED_CHAR_BYTES
}

/// What [`NODE_BYTES`] leaves over the arithmetic: the rounding every ceiling here carries.
pub fn node_spare() -> usize {
    NODE_BYTES - worst_node_bytes()
}

/// Describe every item of a store at every cap: a row per turn, per run and per session, each
/// field full of `&` and each stamped under version 0 so the stale tag renders too.
///
/// The shared enriched store describes most of its items and no plant can reach the rest, so the
/// rows go in wholesale — a marginal cost measured between a described row and an undescribed one
/// is not one. Each string goes in a character past its cap, so the page pays for the mark as well
/// as the width: the words a pass writes are the one field here that routinely runs past what a
/// pane prints.
pub fn describe_at_every_cap(store: &hyphae_store::Store) {
    let connection = store.connection();
    let said = "&".repeat(queries::ENRICHMENT_CHARS + 1);
    let tag = "&".repeat(queries::TAG_CHARS);
    let stamp = "'planted', 0, 0, 'planted', '1970-01-01T00:00:00Z'";
    for (table, keys, source) in [
        (
            "turn_enrichments",
            "t.session_id, t.source, t.id",
            "live_turns t",
        ),
        (
            "agent_run_enrichments",
            "r.session_id, r.id",
            "live_agent_runs r",
        ),
        ("session_enrichments", "s.id", "sessions s"),
    ] {
        connection
            .execute(&format!("DELETE FROM {table}"), [])
            .expect("the table empties");
        connection
            .execute(
                &format!("INSERT INTO {table} SELECT {keys}, ?, ?, ?, ?, {stamp} FROM {source}"),
                duckdb::params![said, tag, tag, said],
            )
            .expect("every item is described");
    }
}
