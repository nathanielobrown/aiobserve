//! Reading a session in order: the prev/next controls beside the pane.
//!
//! Neither control descends. A click on a NavTree row is how a reader goes down, so these two go
//! along the level the reader is standing on — the next row, then the next — and at the end of it
//! out to whatever follows the thing that level sits in. Prev is the same level backwards, and
//! from its first row the node that holds it. A step that changes level is marked, because a
//! reader who did not ask to leave the branch should see it coming.
//!
//! These leaves follow the controls themselves rather than calling `view::walk`: what a reader
//! gets is the chain of pages, and only fetching them proves the chain closes. The expectation is
//! read off the NavTree each page was served with — the rows at the selection's own depth are its
//! level, in the order the NavTree drew it — so the reading order is checked against what the
//! reader sees rather than derived from the store a second time.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use axum::http::StatusCode;
use regex::Regex;

use hyphae_store::Param;
use hyphae_testsupport::html::{Markup, plain};
use hyphae_testsupport::landmarks::{FORK_ORIGIN, MAIN, SPINE};
use hyphae_testsupport::rows;
use hyphae_testsupport::selections;
use hyphae_testsupport::served::Served;

/// The arrow each control shows when it stays on the level: it points the way the reader is going.
///
/// Out of the level both point up. This is the half a reader sees — `data-climb` is a hook for
/// these leaves, and the stylesheet reads neither — so the two are checked as one claim below.
const ALONG: [(&str, &str); 2] = [("previous", "\u{2190}"), ("next", "\u{2192}")];
const CLIMB: &str = "\u{2191}";

/// One page the walk stepped on: where it sits, and the NavTree it was served with.
struct Page {
    url: String,
    html: String,
    /// The open path, outermost first and ending at the selection, so the last is this page's own
    /// node and the rest is where it hangs.
    chain: Vec<String>,
}

impl Page {
    fn new(url: &str, html: String) -> Self {
        let chain = Markup::of(&html).values("data-crumb");
        assert!(!chain.is_empty(), "{url} drew no crumb chain");
        Self {
            url: url.to_owned(),
            html,
            chain,
        }
    }

    fn key(&self) -> &str {
        self.chain.last().expect("a crumb chain ends at the node")
    }

    /// Each open level of the NavTree, outermost first — the level each crumb stands in.
    ///
    /// By containment and not by depth: a shut row stands the runs it hides one deeper than
    /// itself, so a depth is no longer a level. What a crumb draws directly under it is the level
    /// its own child sits in. A cap would cut one, which is why the sweep checks no level was cut
    /// before reading a level off a page.
    fn levels(&self) -> Vec<Vec<String>> {
        let markup = Markup::of(&self.html);
        let mut levels = vec![vec![self.chain[0].clone()]];
        levels.extend(
            self.chain[..self.chain.len() - 1]
                .iter()
                .map(|crumb| markup.under(crumb)),
        );
        levels
    }

    /// Where each control should go and whether it climbs, read off this page's own tree.
    fn expected(&self) -> BTreeMap<&'static str, Option<(String, bool)>> {
        let levels = self.levels();
        let last = self.chain.len() - 1;
        let mut after_it = None;
        for depth in (1..=last).rev() {
            let level = &levels[depth];
            let at = place(level, &self.chain[depth]);
            if at + 1 < level.len() {
                after_it = Some((level[at + 1].clone(), depth != last));
                break;
            }
        }
        let mut previous = None;
        if self.chain.len() > 1 {
            let level = &levels[last];
            let at = place(level, &self.chain[last]);
            previous = Some(if at > 0 {
                (level[at - 1].clone(), false)
            } else {
                (self.chain[last - 1].clone(), true)
            });
        }
        BTreeMap::from([("previous", previous), ("next", after_it)])
    }
}

/// Where one key sits in a level.
fn place(level: &[String], key: &str) -> usize {
    level
        .iter()
        .position(|row| row == key)
        .unwrap_or_else(|| panic!("{key} is not in its own level {level:?}"))
}

/// The arrow one control shows: leading on prev, trailing on next, as each points away.
fn arrow(html: &str, named: &str) -> String {
    static BUTTON: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?s)<button[^>]*data-walk="(\w+)"[^>]*>(.*?)</button>"#).expect("a pattern")
    });
    let found = BUTTON
        .captures_iter(html)
        .find(|button| &button[1] == named)
        .unwrap_or_else(|| panic!("no {named} control on the page"));
    let text = plain(&found[2]).trim().chars().collect::<Vec<_>>();
    let at = if named == "previous" {
        text.first()
    } else {
        text.last()
    };
    at.expect("a control shows something").to_string()
}

/// What one control on a served page points at, and whether it is marked as a climb.
fn control(html: &str, named: &str) -> Option<(String, bool)> {
    let markup = Markup::of(html);
    if !markup.holds("data-walk", named) {
        return None;
    }
    let found = markup.inside("data-walk", named, "data-node");
    let step = found.first()?.clone();
    let climbed = !markup.inside("data-walk", named, "data-climb").is_empty();
    let along = BTreeMap::from(ALONG);
    let expected = if climbed { CLIMB } else { along[named] };
    assert_eq!(arrow(html, named), expected, "{named}");
    Some((step, climbed))
}

/// Every page one control reaches from `start` without leaving the level, `start` first.
///
/// Stops at the row whose control climbs, or where there is no control at all: what this returns
/// is one level, walked. The cap is the corpus's own size with room to spare — a walk that did not
/// close would loop here rather than hang.
async fn follow(served: &Served, start: &str, named: &str) -> Vec<Page> {
    let mut walked: Vec<Page> = Vec::new();
    let mut asked = Some(start.to_owned());
    while let Some(url) = asked {
        let (status, html) = served.page(&url).await;
        assert_eq!(status, StatusCode::OK, "{url}");
        let step = control(&html, named);
        asked = match step {
            Some((_, false)) => {
                Some(Markup::of(&html).inside("data-walk", named, "hx-get")[0].clone())
            }
            _ => None,
        };
        walked.push(Page::new(&url, html));
        assert!(walked.len() < 500, "{start}: the walk did not end");
    }
    walked
}

/// The URL of the first row of the level under one page's selection.
///
/// Where a walk of that level starts: the controls never descend, so a leaf that wants to read a
/// level has to arrive on it the way a reader does, by clicking a NavTree row.
async fn first_child(served: &Served, at: &str) -> String {
    let (_, html) = served.page(at).await;
    let markup = Markup::of(&html);
    let first = markup.kin();
    let href = markup.inside("data-nav-tree", &first[0], "href");
    assert_eq!(href.len(), 1);
    href[0].clone()
}

/// A turn with siblings on both sides and more than one call under it.
///
/// The level below it is what the leaves walk, and the turns beside it are what its own level
/// offers a climb out to — so this one turn exercises stepping along a level and both ways out.
fn deep_turn(db: &std::path::Path) -> String {
    rows::one(
        db,
        "SELECT t.id FROM live_turns t \
         WHERE t.session_id = $session AND t.source = $thread AND t.\"index\" > 0 \
         AND (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id \
              AND c.source = t.source AND c.turn_id = t.id) > 1 \
         AND EXISTS (SELECT 1 FROM live_turns o WHERE o.session_id = t.session_id \
              AND o.source = t.source AND o.\"index\" > t.\"index\") \
         ORDER BY t.\"index\" LIMIT 1",
        &[
            ("session", Param::from(SPINE)),
            ("thread", Param::from(MAIN)),
        ],
    )
    .str("id")
    .expect("a turn id")
    .to_owned()
}

#[tokio::test]
async fn every_control_in_the_corpus_walks_its_own_level_or_climbs_out_of_it() {
    // Neither control ever descends: every page in the corpus, both controls, against its tree.
    //
    // The claim is the whole rule at once — next is the following row of the reader's own level,
    // or, at the end of it, what follows the branch they are in; prev is the row ahead of them, or
    // the node that holds the level. A control that stepped into a node's children would land
    // somewhere no level on the page holds, and fail here.
    let served = Served::corpus();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for url in selections::pages(&served.db()) {
        if !url.starts_with("/session/") {
            continue;
        }
        let (_, html) = served.page(&url).await;
        // The expectation is a level read off the NavTree, so a level the cap cut would make it a
        // different claim. Nothing in this corpus comes near the window.
        assert_eq!(
            Markup::of(&html).values("data-more"),
            Vec::<String>::new(),
            "{url}"
        );
        let page = Page::new(&url, html);
        for (named, expected) in page.expected() {
            assert_eq!(control(&page.html, named), expected, "{url} {named}");
            if let Some((_, climbed)) = expected {
                seen.insert(format!("{named}:{climbed}"));
            }
        }
    }
    // And every arm of the rule was reached: both controls, each stepping along a level and each
    // climbing out of one. An arm the corpus never reaches is an arm nothing above pins.
    assert_eq!(
        seen,
        BTreeSet::from([
            "next:false".to_owned(),
            "next:true".to_owned(),
            "previous:false".to_owned(),
            "previous:true".to_owned(),
        ])
    );
}

#[tokio::test]
async fn the_two_controls_walk_one_level_and_mark_the_way_out_of_it() {
    // A reader who keeps pressing next reads the level they are on, in the NavTree's order.
    //
    // Followed as a reader follows it — each page fetched, the next control read off what came
    // back — so the leaf proves the chain closes rather than that one page's markup is right.
    // Both ways out of the level are read at the ends: the row after the last is the turn's own
    // next sibling, and the row before the first is the turn.
    let served = Served::corpus();
    let turn = deep_turn(&served.db());
    let at = format!("/session/{SPINE}/thread/{MAIN}/turn/{turn}");
    let (_, standing) = served.page(&at).await;
    let level = Markup::of(&standing).kin();
    assert!(level.len() > 1, "a level with more than one row to walk");
    let start = first_child(&served, &at).await;
    let forward = follow(&served, &start, "next").await;
    assert_eq!(
        forward.iter().map(|page| page.key()).collect::<Vec<_>>(),
        level
    );
    // The end of the level climbs out of it, to the row after the turn the level hangs under.
    let turns = Page::new(&at, standing).levels()[1].clone();
    let after = place(&turns, &format!("turn:{turn}")) + 1;
    assert_eq!(
        control(&forward[forward.len() - 1].html, "next"),
        Some((turns[after].clone(), true))
    );
    // And the same level backwards, out through the turn itself.
    let back = follow(&served, &forward[forward.len() - 1].url, "previous").await;
    let mut reversed = level.clone();
    reversed.reverse();
    assert_eq!(
        back.iter().map(|page| page.key()).collect::<Vec<_>>(),
        reversed
    );
    assert_eq!(
        control(&back[back.len() - 1].html, "previous"),
        Some((format!("turn:{turn}"), true))
    );
}

#[tokio::test]
async fn a_session_is_read_from_its_nav_tree_and_not_from_the_controls() {
    // A session page offers no step in either direction: it is the only node at its level.
    //
    // Which is the shape of the whole design — the controls read one level, and going down into
    // the session is what the NavTree is for. `FORK_ORIGIN` is here for the nesting: a session
    // that spawned runs that spawned runs still offers nothing, because depth is not what they
    // walk.
    let served = Served::corpus();
    for session_id in [SPINE, FORK_ORIGIN] {
        let (_, html) = served.page(&format!("/session/{session_id}")).await;
        let markup = Markup::of(&html);
        assert_eq!(
            markup.values("data-walk"),
            Vec::<String>::new(),
            "{session_id}"
        );
        // And the NavTree it was served with does hold the level a reader goes down into, so the
        // absence above is the controls' rule and not an empty page.
        assert!(!markup.kin().is_empty(), "{session_id}");
    }
}

#[tokio::test]
async fn a_control_says_what_the_neighbour_is_and_what_it_was() {
    // A control names the neighbour's kind and its title — the same title its NavTree row carries.
    //
    // A reader deciding whether to step has the node's own words, not the word "next". The kind is
    // printed rather than left in an attribute: a step can climb out of the level, and a reader
    // who cannot see that has no warning before it.
    let served = Served::corpus();
    // The thread's own level, which is the longest the corpus offers: four turns in a row.
    let start = first_child(&served, &format!("/session/{SPINE}")).await;
    let walked = follow(&served, &start, "next").await;
    let step = &walked[1];
    for (named, neighbour) in [("previous", &walked[0]), ("next", &walked[2])] {
        assert_eq!(
            control(&step.html, named),
            Some((neighbour.key().to_owned(), false))
        );
        // Both halves are text on the page: what the neighbour is, and what it is called. The
        // title is the one the neighbour's own NavTree row carries — one node, one name, wherever
        // it is read.
        let kind = neighbour.key().split(':').next().expect("a node kind");
        let title =
            Markup::of(&neighbour.html).fields("data-selected", neighbour.key())["title"].clone();
        assert_eq!(
            Markup::of(&step.html).fields("data-walk", named),
            BTreeMap::from([
                ("kind".to_owned(), kind.to_owned()),
                ("title".to_owned(), title),
            ])
        );
    }
}

#[tokio::test]
async fn the_walk_is_the_same_however_the_nav_tree_is_capped() {
    // `?kin=` cuts the NavTree, never the reading order: the walk reads the store, not the rows.
    //
    // The cap is dropped to one child a level, which is the smallest the knob goes, so the NavTree
    // beside the pane loses everything but the open path — and the controls do not move.
    let served = Served::corpus();
    let at = format!(
        "/session/{SPINE}/thread/{MAIN}/turn/{}",
        deep_turn(&served.db())
    );
    for page in follow(&served, &at, "next").await {
        let (status, capped) = served.page(&format!("{}?kin=1", page.url)).await;
        assert_eq!(status, StatusCode::OK, "{}", page.url);
        for named in ["previous", "next"] {
            assert_eq!(
                control(&capped, named),
                control(&page.html, named),
                "{} {named}",
                page.key()
            );
        }
    }
}
