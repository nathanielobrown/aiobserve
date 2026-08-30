//! What a node of a session is: its kind, its URL, and what it spent.
//!
//! Ported from `src/hyphae/view/nodes.py`. Everything a session records is a node — the
//! session, its turns, the runs it spawned, the api calls those turns made, the tool calls
//! those calls made, the compactions between them, and the two buckets that hold what
//! attaches to nothing. Each has a page of its own, so each needs one title, one URL and one
//! share of the spend, minted here and nowhere else: a NavTree row, a crumb and a pane all
//! read the same node.
//!
//! [`crate::builders`] turns a store row into one; this module is the vocabulary it builds in.

use std::collections::HashMap;
use std::fmt;

use hyphae_store::queries;

use crate::columns::{CALL_ICON, RUN_ICON, Shape, TOOL_ICON};
use crate::format::cut;
use crate::inline_markdown;
use crate::render::Markup;

/// How a cost badge is drawn: the steps it has, and how many decades of share they cover. A
/// session's cheapest turn and its dearest are three orders of magnitude apart, so the scale
/// is logarithmic — a linear one would paint every row but the dearest alike.
const STEPS: i32 = 10;
const DECADES: f64 = 3.0;

/// The context bar's ladder: how many steps a fill or a tip is drawn in, across the whole of
/// the model's window. Linear, because what the bar says is fullness against a limit and a log
/// scale draws a half-full window as a nearly full one.
const BAR_STEPS: i64 = 20;

/// What stands between a node's lead and its words ([`Node::title`]). A lead that brackets
/// itself says where it ends without a dash, and takes `separator` to a space.
pub const LEAD_SEPARATOR: &str = " — ";

/// What the two buckets are called. Neither is a row of the store: they stand for the rows that
/// attach to nothing, so their titles say what is missing rather than naming a thing.
pub const UNATTRIBUTED_TITLE: &str = "calls under no turn of this thread";
pub const UNATTACHED_TITLE: &str = "runs attached to no turn";

/// What marks an api call's title as the model's own words rather than a description of what the
/// call did (`crate::builders::call_node`). The one glyph a reader can scan a thread for: it says
/// this row is something the model said, whether or not the call went on to run tools.
pub const SPEECH_MARK: &str = "💭";

/// The most of an api call's title the count of its tool calls may take. Half the narrowest width
/// any surface cuts a title to, so the tool the reader picks the row out by keeps the other half.
pub const TALLY_CHARS: usize = queries::HEADER_CHARS / 2;

/// Where a cost is rounded back to. Every one the store hands out is already at four decimals
/// (`view_runs.sql`), so a sum or a difference of them is put back at the same place: a main
/// thread that spent nothing is then exactly nothing rather than a float residue that draws a
/// badge at the bottom step.
pub const COST_PLACES: i32 = 4;

/// What a node is: the segment its URL carries, and the query its children come from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Kind {
    Session,
    Turn,
    Run,
    Call,
    Tool,
    Compaction,
    /// The two buckets. A run or a call the transcript could not attach still happened, so
    /// each thread's unattached rows get a node of their own rather than being dropped or
    /// hidden under something they did not come from.
    Unattributed,
    Unattached,
}

impl Kind {
    /// The word the URL and every `data-` attribute spell this kind with.
    pub fn word(self) -> &'static str {
        match self {
            Self::Session => "session",
            Self::Turn => "turn",
            Self::Run => "run",
            Self::Call => "call",
            Self::Tool => "tool",
            Self::Compaction => "compaction",
            Self::Unattributed => "unattributed",
            Self::Unattached => "unattached",
        }
    }

    /// The kind that word names, or nothing when no kind is spelled that way.
    ///
    /// The inverse of [`Kind::word`]: a URL segment is something a reader can type, so a segment
    /// outside the set is a 404 rather than a page built around a kind that does not exist.
    pub fn spelled(word: &str) -> Option<Self> {
        [
            Self::Session,
            Self::Turn,
            Self::Run,
            Self::Call,
            Self::Tool,
            Self::Compaction,
            Self::Unattributed,
            Self::Unattached,
        ]
        .into_iter()
        .find(|kind| kind.word() == word)
    }

    /// The mark saying what kind of node this is, wherever a page names one — the NavTree row,
    /// the crumb, the pane's own heading, and the browser tab. Eight characters a reader
    /// learns once and then reads a NavTree by without reading a title, which is why the table
    /// is here rather than in a component.
    pub fn icon(self) -> &'static str {
        match self {
            Self::Session => "❖",
            Self::Turn => "❯",
            Self::Run => RUN_ICON,
            Self::Call => CALL_ICON,
            Self::Tool => TOOL_ICON,
            Self::Compaction => "⊟",
            // The mark the two buckets share: each holds what the transcript could not
            // attach, and a reader meets them as one kind of hole rather than two.
            Self::Unattributed | Self::Unattached => "∅",
        }
    }

    /// Whether this kind has numbers behind its row: every kind that stands for a row of the
    /// store. Only the two buckets are absent, because a bucket is a place rather than a node
    /// and there is no row under it to count.
    pub fn numbered(self) -> bool {
        !matches!(self, Self::Unattributed | Self::Unattached)
    }
}

impl fmt::Display for Kind {
    fn fmt(&self, into: &mut fmt::Formatter<'_>) -> fmt::Result {
        into.write_str(self.word())
    }
}

/// Which children a level shows: the value `?nav=` carries, full when it carries none.
///
/// A view of the same session rather than a different session — nothing is dropped, only
/// folded away, and the path the reader is standing on renders whatever the preset hides.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preset {
    Full,
    /// The api calls folded away, so a turn's tool calls stand directly under it.
    NoApi,
    /// Agent runs only, each under the run that spawned it: the session as a spawn tree.
    Agents,
}

impl Preset {
    /// The value `?nav=` carries.
    pub fn word(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::NoApi => "noapi",
            Self::Agents => "agents",
        }
    }

    /// What the control above the NavTree calls this preset, for a reader who never reads the
    /// URL.
    pub fn label(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::NoApi => "no api calls",
            Self::Agents => "agents only",
        }
    }

    /// Every preset, in the order the control offers them.
    pub const ALL: [Self; 3] = [Self::Full, Self::NoApi, Self::Agents];
}

/// Where a node left the model's context window, in tokens (`analyze/macros.py`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Context {
    /// Everything the node's last answering call was billed for: the cache it read, the cache
    /// it wrote, what it sent, and what it said back.
    pub fill: i64,
    /// How much of that fill the node itself put there. `None` where the question does not
    /// arise — a session, which has nothing before it to have added to.
    pub added: Option<i64>,
    /// The window that call's model answers in (`extract/pricing.py::CONTEXT_WINDOWS`).
    pub window: i64,
    /// The context the session opened on: what its first main-thread call sent before a word
    /// had been said. Only a turn carries one, because only a turn's growth is worth reading
    /// against it.
    pub base: Option<i64>,
}

/// A node named by identity alone: enough to find it, not enough to render it.
///
/// What a spend ledger is keyed by, and what path resolution works in. The rendered node comes
/// out of its parent's level, so a ref never carries a title or a cost.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Ref {
    pub kind: Kind,
    /// The thread it was recorded on, `main` or a run's id. `None` where the node is not on
    /// one: the session, and the unattached bucket that spans every thread.
    pub source: Option<String>,
    pub node_id: String,
}

impl Ref {
    pub fn new(kind: Kind, source: Option<&str>, node_id: &str) -> Self {
        Self {
            kind,
            source: source.map(str::to_owned),
            node_id: node_id.to_owned(),
        }
    }

    /// `kind:id` — what a row is marked with, and how a test names the row it means.
    pub fn key(&self) -> String {
        format!("{}:{}", self.kind, self.node_id)
    }
}

/// What one session spent, and what the runs under each of its nodes cost.
///
/// Read once per page and handed to every node built for it: a badge's first half is what the
/// node's own thread spent, its second that plus what `under` holds for the node, and both are
/// washed against `whole`. A node absent from `under` has no run below it and draws one number.
#[derive(Debug, Default, Clone)]
pub struct Ledger {
    /// What the session spent, the basis every share on the page is a share of.
    pub whole: f64,
    /// Run cost by the node it hangs under, keyed by the ref that node mints for itself.
    pub under: HashMap<Ref, f64>,
}

impl Ledger {
    /// What the runs under one node cost, or nothing where none hang there.
    pub fn below(&self, node: &Ref) -> f64 {
        self.under.get(node).copied().unwrap_or(0.0)
    }

    /// What a surface with no page to roll up hands a node: a crumb, a pane heading, an error
    /// list. Each draws no badge, so a node built for one has an empty ledger rather than a
    /// share of something it never read.
    pub fn none() -> Self {
        Self::default()
    }
}

/// The two halves of a node's cost badge, and the share each is washed at.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Spend {
    /// What the node's own thread spent. `None` where it has no spend of its own — a tool call
    /// that asked for nothing, a compaction.
    pub own: Option<f64>,
    /// That plus every run under it, or `None` where no run hangs there: a second half
    /// repeating the first says the same thing twice, and a reader reads that as two
    /// measurements.
    pub total: Option<f64>,
    pub share: Option<f64>,
    pub total_share: Option<f64>,
}

/// One node of a session, wherever it is read — a NavTree row, a crumb, or the pane itself.
#[derive(Debug, Clone)]
pub struct Node {
    pub kind: Kind,
    pub session_id: String,
    pub source: Option<String>,
    pub node_id: String,
    /// What the node is called, before any surface cuts it: the model's description where a
    /// pass wrote one, else what the session called it. Every query that composes it comes
    /// back one character past the width it was cut to, so a name that fills a row is one the
    /// reader can tell was stopped (`crate::format::cut`).
    pub words: String,
    /// What it cost and what everything under it did, with the share each is washed at, beside
    /// how many calls under it our price table could not price: a total missing calls is not
    /// what the node cost, so the two always travel together.
    pub spend: Spend,
    pub unpriced_api_calls: i64,
    /// A word that goes before the words wherever nothing else says it: the agent type a run
    /// ran under, the tool a call invoked. Empty for every kind whose words stand alone. It
    /// leads `title` but not `log_title`, because a children log heads it in a column of its
    /// own.
    pub lead: String,
    /// What goes between the lead and the words. A dash by default; a lead already closed by a
    /// bracket takes a space.
    pub separator: &'static str,
    /// Whether any of the words are the model's rather than the session's, which is what the
    /// glyph beside the title marks. Three kinds can be: a session, a turn and a run.
    pub enriched: bool,
    /// Whether the tool call came back an error. Only ever true for [`Kind::Tool`].
    pub is_error: bool,
    /// What every cut of the title keeps, printed after the words: how many of each tool an
    /// api call went on to invoke after the first. Empty for every other kind.
    pub tail: String,
    /// Where the node left the model's context window, or `None` for a node that ends on no
    /// window at all.
    pub context: Option<Context>,
    /// Whether the node's own thread ran its window out. Only ever true for a [`Kind::Run`].
    pub maxed: bool,
    /// How often it happened. Zero for every other kind, and zero draws no badge at all: a
    /// main-thread compaction is already a ⊟ row of the tree, so a run's row is the one place
    /// the count is the only way to see it.
    pub compactions: i64,
}

impl Node {
    /// A node with nothing said about it: the fields every builder overwrites, defaulted.
    ///
    /// Rust has no keyword arguments, so this stands in for Python's defaulted dataclass
    /// fields — a builder writes what its kind knows and takes the rest from here.
    pub fn bare(kind: Kind, session_id: &str, source: Option<&str>, node_id: &str) -> Self {
        Self {
            kind,
            session_id: session_id.to_owned(),
            source: source.map(str::to_owned),
            node_id: node_id.to_owned(),
            words: String::new(),
            spend: Spend::default(),
            unpriced_api_calls: 0,
            lead: String::new(),
            separator: LEAD_SEPARATOR,
            enriched: false,
            is_error: false,
            tail: String::new(),
            context: None,
            maxed: false,
            compactions: 0,
        }
    }

    /// The mark saying what kind of node this is, for every surface that names it.
    pub fn icon(&self) -> &'static str {
        self.kind.icon()
    }

    /// What this node is called: the whole of it, before any surface cuts it.
    ///
    /// The concept every surface reads and none of them owns — lead, words and tail joined, in
    /// the markdown whoever wrote it typed. The cuts below are this title at the width of the
    /// surface reading it, and they are the only cuts of it: a page that composed its own would
    /// be a second answer to "what is this node called" (`docs/viewer-titles.md`).
    pub fn title(&self) -> String {
        self.joined(&[&self.lead, &self.words]) + &self.tail
    }

    /// The title at the width of a NavTree row, a walk control, or an errors-list row.
    pub fn nav_tree_title(&self) -> Markup {
        self.at(
            queries::NAV_CHARS,
            &[&self.lead, &self.words],
            false,
            self.cut_at(queries::NAV_CHARS),
        )
    }

    /// The title at the width of a children log's own column.
    ///
    /// Wider than a NavTree row's because the log is a table and the column is the width of the
    /// pane: a description cut to a NavTree row's width is the reason a reader opens a node to
    /// find out what it was. The words alone — a log that leads a column with a word heads that
    /// column with it too (`lead`).
    pub fn log_title(&self) -> Markup {
        self.at(
            queries::LOG_CHARS,
            &[&self.words],
            false,
            self.cut_at(queries::LOG_CHARS),
        )
    }

    /// The title at a row's width with its markup gone, for the browser tab.
    ///
    /// A `<title>` element prints an element as characters rather than acting on it, so the one
    /// surface with nowhere to put markup takes the text under it, cut where the row beside it
    /// stops.
    pub fn tab_title(&self) -> String {
        self.plain(queries::NAV_CHARS, &[&self.lead, &self.words])
    }

    /// The title at the width of one crumb of the chain above the pane.
    ///
    /// The narrowest, and the only one that is not the whole of what its surface could show: a
    /// chain is many nodes on one line, and the node it ends at is open underneath it. Cut here
    /// rather than in SQL — the query behind a crumb is the NavTree's, and a second query for a
    /// narrower copy of the same string would be a page cost paid for nothing.
    pub fn crumb_title(&self) -> Markup {
        // Cut to a crumb's width against a NavTree row's cap, because the row's query is where
        // the words came from: what stopped them is that cut, not the narrower one here.
        self.at(
            queries::CRUMB_CHARS,
            &[&self.lead, &self.words],
            false,
            self.cut_at(queries::NAV_CHARS),
        )
    }

    /// The title at the head of the node's own pane, where nothing repeats it.
    ///
    /// The widest, because a pane heads one node. The one surface a link in a title becomes an
    /// `<a>` on: every other prints its title inside a link already, and an `<a>` inside an
    /// `<a>` is markup a browser undoes.
    pub fn pane_title(&self) -> Markup {
        // The cap is a preview's rather than this width, because a header query returns its
        // strings at this width *or wider*: the pane cannot tell where such a string was cut,
        // so its own budget is the only cut it may mark.
        self.at(
            queries::HEADER_CHARS,
            &[&self.lead, &self.words],
            true,
            self.cut_at(queries::DETAIL_CHARS),
        )
    }

    /// The parts of a title a width is spent on, in reading order.
    fn joined(&self, parts: &[&str]) -> String {
        parts
            .iter()
            .filter(|part| !part.is_empty())
            .copied()
            .collect::<Vec<_>>()
            .join(self.separator)
    }

    /// `parts` rendered at `chars`, with the tail taken out of the width, not cut off it.
    ///
    /// The width is spent on what a reader sees: a description written in markdown is rendered
    /// rather than printed, so its syntax costs the surface nothing. Which is why `source_cap`
    /// comes too — the width the query cut the words at is then the only thing that knows a
    /// line with room to spare was still stopped.
    fn at(&self, chars: usize, parts: &[&str], links: bool, source_cap: usize) -> Markup {
        let joined = self.joined(parts);
        // The cap is the query's, and the query cut the words: whatever the join puts in front
        // of them was composed here and is whole, so it is room the cap has to allow for.
        let cap = source_cap + joined.chars().count() - self.words.chars().count();
        let rendered = inline_markdown::cut(
            Some(&joined),
            chars.saturating_sub(self.tail.chars().count()),
            links,
            cap,
        );
        crate::render::joined([rendered, crate::render::text(&self.tail)])
    }

    /// The width the query behind the words cut them at, for a surface that reads `chars`.
    ///
    /// Every query composing a title cuts it to the width of the surface it was read for —
    /// except a description, which a pass wrote and `view_enrichment` cuts at a width of its
    /// own, wherever it is printed.
    fn cut_at(&self, chars: usize) -> usize {
        if self.enriched {
            queries::ENRICHMENT_CHARS
        } else {
            chars
        }
    }

    /// The same cut, as the text under it: for the surfaces that cannot carry markup.
    fn plain(&self, chars: usize, parts: &[&str]) -> String {
        let stripped = inline_markdown::strip(Some(&self.joined(parts)));
        cut(&stripped, chars.saturating_sub(self.tail.chars().count())) + &self.tail
    }

    /// The identity half, for the ledger and the path resolution that work in refs.
    pub fn node_ref(&self) -> Ref {
        Ref::new(self.kind, self.source.as_deref(), &self.node_id)
    }

    /// `kind:id` — what a row is marked with, and how a test names the row it means.
    pub fn key(&self) -> String {
        format!("{}:{}", self.kind, self.node_id)
    }

    /// Where this node's thread begins, for the paths that hang off it.
    ///
    /// Only a node recorded on one has this. The session and the unattached bucket span every
    /// thread and say so by carrying none; every builder of any other kind reads the column, so
    /// a node here without one is a query that dropped it rather than a node with nowhere to sit.
    pub fn thread(&self) -> String {
        let source = self.source.as_deref().unwrap_or_else(|| {
            panic!(
                "a {} node was built with no thread: {}",
                self.kind, self.node_id
            )
        });
        thread_url(&self.session_id, source)
    }

    /// Where the node reads: the link a row carries, and the URL a click fetches.
    pub fn url(&self) -> String {
        match self.kind {
            Kind::Session => session_url(&self.session_id),
            // The unattached bucket hangs off the session, and both buckets are named by what
            // they hold rather than by an id of their own — so their paths end on the word.
            Kind::Unattached => format!("{}/unattached", session_url(&self.session_id)),
            Kind::Unattributed => format!("{}/unattributed", self.thread()),
            // A run's id is also the thread its own rows carry, so one key answers both
            // questions and the URL says it once.
            Kind::Run => run_url(&self.session_id, &self.node_id),
            kind => format!("{}/{}/{}", self.thread(), kind, self.node_id),
        }
    }

    /// Where the node's body alone is fetched — the mount a log row opens it through.
    pub fn expansion(&self) -> String {
        format!("{BODY_URL}{}", self.url())
    }

    /// Where the numbers behind this row are fetched, or nothing when it has none.
    ///
    /// What the row's bar and its badge stand for, written out. Empty for a kind that stands
    /// for no store row, which is how the component knows not to wire a fetch.
    pub fn numbers(&self) -> String {
        if self.kind.numbered() {
            format!("{NUMBERS_URL}{}", self.url())
        } else {
            String::new()
        }
    }

    /// Where the children this node's window left out are fetched, for a tail row to open.
    ///
    /// Not the node's own path under a prefix like [`Node::expansion`]: what the route resolves
    /// is a level rather than a node, so a kind whose page needs no id still names itself and
    /// its id here.
    pub fn rest(&self) -> String {
        let head = match &self.source {
            None => session_url(&self.session_id),
            Some(_) => self.thread(),
        };
        format!("{KIN_URL}{head}/{}/{}", self.kind, self.node_id)
    }

    /// What this node's own thread spent — the first half of its badge, and every log's.
    pub fn cost_usd(&self) -> Option<f64> {
        self.spend.own
    }

    /// What its whole subtree spent, or `None` where no run hangs under it.
    pub fn total_usd(&self) -> Option<f64> {
        self.spend.total
    }

    /// The step class the first half of this node's cost badge is drawn with.
    pub fn meter(&self) -> String {
        match self.spend.own {
            Some(_) => meter(self.spend.share),
            None => String::new(),
        }
    }

    /// The step class the second half is drawn with, or nothing where there is no second.
    ///
    /// Its own share and not the first's: two halves of one badge are two shares of what the
    /// session spent, and drawing them at one depth would say a subtree cost what its root did.
    pub fn total_meter(&self) -> String {
        match self.spend.total {
            Some(_) => meter(self.spend.total_share),
            None => String::new(),
        }
    }

    /// The classes this node's context bar is drawn with, or nothing where it has none.
    ///
    /// Up to three edges, each a prefix of the one outside it: where the window stood when the
    /// node ended, where the node's own share of it begins, and — on a turn — where the
    /// conversation begins. The nesting is arithmetic here rather than paint order in the
    /// stylesheet, so a reader of the markup and a reader of the page see the same bar.
    ///
    /// `maxed` rides beside them, and alone where a run's own thread compacted without leaving
    /// a window to draw against: what it says is that the run ran out, which is a fact about the
    /// thread rather than a share of anything.
    pub fn bar(&self) -> String {
        let Some(context) = self.context else {
            return if self.maxed {
                "maxed".to_owned()
            } else {
                String::new()
            };
        };
        let window = context.window;
        let fill = bar_step(context.fill, window);
        let mut drawn = vec![format!("f{fill}")];
        let base = context
            .base
            .filter(|base| *base != 0)
            .map(|base| bar_step(base, window).min(fill));
        if let Some(added) = context.added {
            // A turn's base runs past its prior wherever the conversation is younger than the
            // prompt it opened on — the session's first turn, every time — and holding the
            // inner edge at the outer one is what draws that turn as the prompt it mostly is.
            let stood = (context.fill - added).max(0);
            let prior = bar_step(stood, window).min(fill);
            drawn.push(format!("p{}", prior.max(base.unwrap_or(0))));
        }
        if let Some(base) = base {
            drawn.push(format!("b{base}"));
        }
        if self.maxed {
            drawn.push("maxed".to_owned());
        }
        drawn.join(" ")
    }
}

/// The step class a share's cost badge is drawn with, or `s0` for nothing to draw.
pub fn meter(share: Option<f64>) -> String {
    let Some(share) = share.filter(|share| *share != 0.0) else {
        return "s0".to_owned();
    };
    let step = (f64::from(STEPS) * (1.0 + share.log10() / DECADES)).ceil() as i32;
    format!("s{}", step.clamp(1, STEPS))
}

/// Which step of the bar a token count lands on, held at the top where it runs past one.
///
/// A request can ask for a larger window than the model's own, and the reply names the model
/// either way — so a fill above the window is drawn full rather than given a scale the table
/// cannot see.
fn bar_step(tokens: i64, window: i64) -> i64 {
    // Python rounds half to even; a fill landing exactly on a half-step is the only input the
    // two spellings could differ on, and the step it lands on is within one of either way.
    let steps = (tokens as f64 / window as f64 * BAR_STEPS as f64).round() as i64;
    steps.min(BAR_STEPS)
}

// Every path the viewer serves is built from the three below, and they obey one rule: an id is
// never written next to another id — a word saying what kind of id it is always comes first.
/// Where a session reads, and the head of every path about something inside it.
pub fn session_url(session_id: &str) -> String {
    format!("/session/{session_id}")
}

/// Where one thread of a session begins: `main`, or the id of a run that ran on its own.
///
/// Nothing reads at this path itself — a thread is a place things were recorded rather than a
/// node — so what it mints is the segment a turn, a call, a tool call, a compaction, a bucket
/// and a raw transcript all hang off.
pub fn thread_url(session_id: &str, source: &str) -> String {
    format!("{}/thread/{source}", session_url(session_id))
}

/// Where an agent run reads.
pub fn run_url(session_id: &str, run_id: &str) -> String {
    format!("{}/run/{run_id}", session_url(session_id))
}

/// Where a node's body alone is served from, written once.
/// Which shape of log lists a kind, and nothing for a kind no log lists.
///
/// For the one reader that knows a child and needs its parent's table: an expansion arrives as a
/// row of the log it opens under, and that row spans the log's columns. A kind lists in one shape
/// of log wherever it lists at all, which is what makes the width answerable from the child alone.
pub fn listed(kind: Kind) -> Option<Shape> {
    match kind {
        Kind::Turn => Some(Shape::Turns),
        Kind::Call => Some(Shape::Calls),
        Kind::Tool => Some(Shape::Tools),
        Kind::Run => Some(Shape::Runs),
        _ => None,
    }
}

/// How many columns the log listing a node of `kind` has, for a row that spans them.
pub fn spanned(kind: Kind) -> usize {
    listed(kind)
        .unwrap_or_else(|| panic!("no children log lists a {kind}"))
        .columns()
        .len()
}

pub const BODY_URL: &str = "/fragment/body";
/// And where the children one level's window left out are served from, which is what a tail row
/// fetches ([`Node::rest`]).
pub const KIN_URL: &str = "/fragment/kin";
/// And where a row's numbers are served from, for the popover a reader opens by pointing at one.
pub const NUMBERS_URL: &str = "/fragment/numbers";
