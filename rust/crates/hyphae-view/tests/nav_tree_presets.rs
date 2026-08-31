//! `?nav=` picks which children a level shows, with the path down to the reader left open.
//!
//! The design's kind x preset table is [`Levels::cell`], and these leaves are what spend it: every
//! kind under every preset, the levels an open path keeps whatever the preset filters, the control
//! that offers the presets, and the preset riding every link the page mints so a reader who picked
//! one keeps it.

use std::collections::{BTreeMap, BTreeSet};

use axum::http::StatusCode;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{MAIN, SPINE};
use hyphae_testsupport::nav_trees::{self, Levels};
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::{Kind, Preset};

/// One node of a kind, as the arguments a cell is read with: session, thread, id.
type At = (String, String, String);

#[tokio::test]
async fn every_kind_under_every_preset_opens_the_children_its_cell_defines() {
    // The 24 cells of the design's table, each read off the page that renders it.
    //
    // One case per cell, checked on the node the corpus fills that cell fullest at, so a wrong
    // cell reddens under its own name. `unfilled` is the other half: a cell that renders nothing
    // has to be one the design or the corpus says is empty.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    for preset in Preset::ALL {
        for kind in Kind::ALL {
            let picked = richest(&levels, preset, kind, 1)
                .pop()
                .unwrap_or_else(|| panic!("the corpus records a {kind}"));
            let expected = cell_at(&levels, preset, kind, &picked);
            let (_, html) = served.page(&asked(kind, &picked, preset)).await;
            assert_eq!(
                Markup::of(&html).kin(),
                expected,
                "{kind} {preset:?} {picked:?}"
            );
            assert_eq!(
                !expected.is_empty(),
                !unfilled(kind, preset),
                "{kind} {preset:?} {picked:?}"
            );
            // The fullest node cannot see a level that leaks sideways: children matched on the
            // thread alone would land under every node of the kind on it, and under the fullest
            // one that is the answer the cell wanted anyway. So the same cell is read again at a
            // sibling on the same thread that the corpus leaves empty, where a leak has nothing
            // to hide behind.
            let beside = levels.candidates(kind).into_iter().find(|at| {
                at.0 == picked.0
                    && at.1 == picked.1
                    && *at != picked
                    && cell_at(&levels, preset, kind, at).is_empty()
            });
            if let Some(beside) = beside {
                let (_, empty) = served.page(&asked(kind, &beside, preset)).await;
                assert!(
                    Markup::of(&empty).kin().is_empty(),
                    "{kind} {preset:?} {beside:?}"
                );
            }
        }
    }
}

#[tokio::test]
async fn every_open_level_is_its_own_cell_or_the_full_one_that_holds_the_path() {
    // Every visible node has a visible parent, level by level and not at the selection alone.
    //
    // A preset filters children and never the expanded chain, so a level whose cell would hide the
    // step the path goes through renders in full instead — which is what lets a reader stand on a
    // kind the preset folds away and still see where it sits. Swept over the nodes of every kind
    // the corpus fills best, because a level built under the wrong parent is a shape one selection
    // can hide.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    for preset in Preset::ALL {
        for kind in Kind::ALL {
            for at in richest(&levels, preset, kind, 3) {
                let (_, html) = served.page(&asked(kind, &at, preset)).await;
                let page = Markup::of(&html);
                let chain = page.values("data-crumb");
                let drawn = page.rows();
                assert_eq!(drawn[0], (0, chain[0].clone()), "{kind} {at:?}");
                for (depth, (crumb_kind, arguments)) in sited(&at.0, &chain).iter().enumerate() {
                    // The rows under this step of the path, by containment rather than by depth: a
                    // shut row anywhere on the page stands the runs it hides at its own depth plus
                    // one, and one of those depths is this level's.
                    let below = page.under(&chain[depth]);
                    let mut expected = cell_at(&levels, preset, *crumb_kind, arguments);
                    if depth + 1 < chain.len() && !expected.contains(&chain[depth + 1]) {
                        expected = cell_at(&levels, Preset::Full, *crumb_kind, arguments);
                    }
                    assert_eq!(below, expected, "{at:?}: under {}", chain[depth]);
                }
            }
        }
    }
}

#[tokio::test]
async fn the_nav_tree_offers_every_preset_at_the_node_the_reader_stands_on() {
    // A preset is a control above the NavTree, not a query string a reader has to know to type.
    //
    // One link per preset, each pointing at the *same* node under a different preset, and the
    // preset in force marked. Read on a node of every kind because every kind's page carries the
    // NavTree, and read with a knob turned down because a link that dropped `?kin=` would quietly
    // serve a wider page than the one the reader is standing on.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    for preset in Preset::ALL {
        for kind in Kind::ALL {
            let picked = richest(&levels, preset, kind, 1).pop().expect("a node");
            let at = nav_trees::node_url(kind, &picked.0, &picked.1, &picked.2);
            let (_, html) = served
                .page(&format!("{at}?nav={}&kin=2", preset.word()))
                .await;
            let page = Markup::of(&html);
            // Every preset is offered, in the order the enum declares them, and the control rides
            // the rows: it sits inside the element a tree click swaps out of band, so the links
            // follow the reader to the node they land on instead of pointing back at the one they
            // left.
            assert_eq!(
                page.inside("id", "nav-tree-rows", "data-nav"),
                Preset::ALL.map(|choice| choice.word().to_owned()),
                "{kind} {preset:?}"
            );
            for choice in Preset::ALL {
                let offered = page.inside("data-nav", choice.word(), "href");
                assert_eq!(offered.len(), 1, "{kind} {choice:?}");
                let href = html_escape::decode_html_entities(&offered[0]).into_owned();
                let (path, query) = split(&href);
                // The same node under a different preset, carrying the knobs the reader arrived
                // with.
                assert_eq!(path, at, "{kind} {choice:?}");
                let mut wanted = BTreeMap::from([("kin".to_owned(), "2".to_owned())]);
                if choice != Preset::Full {
                    wanted.insert("nav".to_owned(), choice.word().to_owned());
                }
                assert_eq!(query, wanted, "{kind} {choice:?}");
                // And the preset in force is the marked one, so the control says where the reader
                // is.
                let marked = page.inside("data-nav", choice.word(), "aria-current");
                let expected: Vec<String> = if choice == preset {
                    vec!["true".to_owned()]
                } else {
                    vec![]
                };
                assert_eq!(marked, expected, "{kind} {choice:?}");
            }
        }
    }
}

#[tokio::test]
async fn a_preset_hides_a_kind_without_hiding_the_path_down_to_one() {
    // A node a preset filters out still renders when the reader is standing on it.
    //
    // `agents` hides turns and `noapi` hides api calls, and either one selected is a reading
    // position rather than a contradiction: the node is on the NavTree, it is the row the pane is
    // about, and its whole chain renders above it with nothing missing in between.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let turn = levels.open_turn();
    let call_id = levels
        .store()
        .fetch(
            "SELECT id FROM live_api_calls WHERE session_id = $session_id AND source = $source \
             AND turn_id = $turn_id ORDER BY \"index\" LIMIT 1",
            &[
                ("session_id", SPINE.into()),
                ("source", MAIN.into()),
                ("turn_id", turn.as_str().into()),
            ],
        )
        .expect("the store answers")
        .first()
        .expect("the open turn was answered")
        .str("id")
        .expect("a call id")
        .to_owned();
    let hidden = [
        (
            Preset::Agents,
            nav_trees::url(&turn),
            format!("turn:{turn}"),
        ),
        (
            Preset::NoApi,
            format!("/session/{SPINE}/thread/{MAIN}/call/{call_id}"),
            format!("call:{call_id}"),
        ),
    ];
    for (preset, at, key) in hidden {
        let (status, html) = served.page(&format!("{at}?nav={}", preset.word())).await;
        assert_eq!(status, StatusCode::OK, "{preset:?}");
        let page = Markup::of(&html);
        assert!(page.values("data-nav-tree").contains(&key), "{preset:?}");
        assert_eq!(
            page.values("data-selected"),
            std::slice::from_ref(&key),
            "{preset:?}"
        );
        let chain = page.values("data-crumb");
        assert_eq!(chain[0], format!("session:{SPINE}"), "{preset:?}");
        assert_eq!(*chain.last().expect("a chain"), key, "{preset:?}");
    }
}

#[tokio::test]
async fn a_preset_rides_every_node_link_the_page_mints() {
    // `?nav=` travels with the reader: every node link on the page carries it.
    //
    // The NavTree's rows, the tail a cap left, the crumbs, the pane's children log and the two
    // walk controls are all node URLs, and a reader who picked a view keeps it through any of
    // them. Both the `href` a reader can paste and the `hx-get` a click follows, because the walk
    // controls are buttons and mint only the second. The presets above the NavTree are the one
    // exception, and the only one: their whole job is to change the preset, so their three links
    // are excluded here and checked on their own leaf. Read with `?kin=1` so the tail row is on
    // the page to check too, and with `?log=1` so the children log runs past one page: the
    // corpus's widest level is five children, so at the production page size no pager is ever
    // minted.
    //
    // The body a log row expands is the same node under the same view, so the preset rides the
    // mount as well — and rides out again on the links the fragment itself mints, which are the
    // reader's way on from inside a parent's page. Every kind of body is opened, because the two
    // fragment routes mint their suffix apart and only a tool's body mints a link of its own.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let (_, html) = served
        .page(&format!(
            "{}?nav=agents&kin=1&log=1",
            nav_trees::url(&levels.open_turn())
        ))
        .await;
    let page = Markup::of(&html);
    let switching: BTreeSet<String> = page
        .inside("class", "presets", "href")
        .into_iter()
        .map(|href| html_escape::decode_html_entities(&href).into_owned())
        .collect();
    assert_eq!(
        switching.len(),
        Preset::ALL.len(),
        "the control's own links, which change the preset"
    );
    // `values` reads the markup and `inside` reads it parsed, so an href with two knobs on it
    // arrives `&amp;`-escaped from one and bare from the other.
    let links: Vec<String> = ["href", "hx-get"]
        .into_iter()
        .flat_map(|attribute| page.values(attribute))
        .filter(|href| {
            nav_trees::node_link(href)
                && !switching.contains(html_escape::decode_html_entities(href).as_ref())
        })
        .collect();
    assert!(links.len() > 5, "the page mints node links to check");
    assert!(
        !page.values("data-walk").is_empty(),
        "the walk controls are among them"
    );
    for href in &links {
        assert_eq!(knob(href, "nav"), Some("agents".to_owned()), "{href}");
    }
    let mut opened: BTreeSet<String> = BTreeSet::new();
    let mut led = 0;
    for at in mounting(&levels) {
        let (_, body) = served.page(&format!("{at}?nav=agents&kin=1")).await;
        let found = nav_trees::mounts(&Markup::of(&body));
        assert!(!found.is_empty(), "the log rows on {at} mount an expansion");
        for mount in found {
            // The mount a log row opens its child's body through carries the preset...
            assert_eq!(knob(&mount, "nav"), Some("agents".to_owned()), "{mount}");
            let (status, fragment) = served.page(&mount).await;
            assert_eq!(status, StatusCode::OK, "{mount}");
            // ...and the body it serves links on under that preset rather than dropping it.
            let fragment = Markup::of(&fragment);
            let onward: Vec<String> = fragment
                .values("href")
                .into_iter()
                .filter(|href| nav_trees::node_link(href))
                .collect();
            assert!(
                !onward.is_empty(),
                "the fragment offers the way to its own node: {mount}"
            );
            for href in onward {
                let href = html_escape::decode_html_entities(&href).into_owned();
                assert_eq!(knob(&href, "nav"), Some("agents".to_owned()), "{href}");
            }
            opened.insert(mounted_kind(&mount));
            led += fragment.values("data-spawned").len();
        }
    }
    // And the rows a tail row fetches are minted by the fragment and not by the page, so the
    // preset rides the fetch out and comes back on every row it answers with.
    let spilling = nav_trees::spilled(&page);
    assert!(
        !spilling.is_empty(),
        "the window left a tail row on the page"
    );
    for fetch in spilling {
        assert_eq!(knob(&fetch, "nav"), Some("agents".to_owned()), "{fetch}");
        let (status, rows) = served.page(&fetch).await;
        assert_eq!(status, StatusCode::OK, "{fetch}");
        let onward = Markup::of(&rows).values("href");
        assert!(
            !onward.is_empty(),
            "the level it left out has rows in it: {fetch}"
        );
        for href in onward {
            let href = html_escape::decode_html_entities(&href).into_owned();
            assert_eq!(knob(&href, "nav"), Some("agents".to_owned()), "{href}");
        }
    }
    // The three kinds of body one route serves, and the run the other one does.
    assert_eq!(
        opened,
        BTreeSet::from(["turn", "call", "tool", "run"].map(str::to_owned)),
        "{opened:?}"
    );
    // One of those tool bodies led with the run it started. That link is the only one the pane
    // builder mints inside a fragment, so without it the suffix the pane is handed goes unread.
    assert!(led > 0, "a tool body that leads with its run");
}

#[tokio::test]
async fn a_preset_the_viewer_does_not_have_is_refused() {
    // `?nav=` names one of the three views or the request is a 400, not a quiet full tree.
    //
    // Asked of a fragment as well as a page: the preset rides the mount an expansion opens, so a
    // preset the viewer does not have has to be refused there too rather than written into every
    // link the fragment serves.
    let served = Served::corpus();
    let levels = Levels::of(&served.db());
    let at = nav_trees::url(&levels.open_turn());
    let (_, html) = served.page(&at).await;
    let mount = nav_trees::mounts(&Markup::of(&html))
        .into_iter()
        .next()
        .expect("the turn's log mounts a body");
    for asked in [at.clone(), mount] {
        let (bare, _) = split(&asked);
        let (status, _) = served.page(&format!("{bare}?nav=everything")).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{asked}");
        for preset in Preset::ALL {
            let (status, _) = served.page(&format!("{bare}?nav={}", preset.word())).await;
            assert_eq!(status, StatusCode::OK, "{asked} {preset:?}");
        }
    }
}

/// The nodes of one kind whose cell holds the most, fullest first.
///
/// No recorded session holds every kind, and a cell read on an empty node passes by agreeing that
/// nothing is there — so a cell is checked wherever the corpus fills it best.
fn richest(levels: &Levels, preset: Preset, kind: Kind, count: usize) -> Vec<At> {
    let mut ordered = levels.candidates(kind);
    ordered.sort_by_key(|at| {
        (
            std::cmp::Reverse(cell_at(levels, preset, kind, at).len()),
            at.clone(),
        )
    });
    ordered.truncate(count);
    ordered
}

fn cell_at(levels: &Levels, preset: Preset, kind: Kind, at: &At) -> Vec<String> {
    levels.cell(preset, kind, &at.0, &at.1, &at.2)
}

/// One node's page under one preset.
fn asked(kind: Kind, at: &At, preset: Preset) -> String {
    format!(
        "{}?nav={}",
        nav_trees::node_url(kind, &at.0, &at.1, &at.2),
        preset.word()
    )
}

/// Each step of an open path as the arguments its own cell is read with.
///
/// A key carries a kind and an id but not a thread, and the path is what supplies it: a node sits
/// on `main` until the path passes through a run, and on that run's thread after it.
fn sited(session_id: &str, chain: &[String]) -> Vec<(Kind, At)> {
    let mut placed = Vec::new();
    let mut source = MAIN.to_owned();
    for key in chain {
        let (word, node_id) = key.split_once(':').expect("a key is kind:id");
        let kind = Kind::spelled(word).unwrap_or_else(|| panic!("{word} names a kind"));
        placed.push((
            kind,
            (session_id.to_owned(), source.clone(), node_id.to_owned()),
        ));
        if kind == Kind::Run {
            source = node_id.to_owned();
        }
    }
    placed
}

/// The cells no recorded session fills, which is not the same claim as an empty cell.
///
/// A compaction is a leaf by the table; the bucket's `agents` cell is one the corpus happens not
/// to reach.
fn unfilled(kind: Kind, preset: Preset) -> bool {
    kind == Kind::Compaction || (kind == Kind::Unattributed && preset == Preset::Agents)
}

/// One page per kind of body a children log can mount.
///
/// A session's log mounts turns, a turn's mounts api calls, a call's mounts tool calls, and the
/// unattached bucket's mounts runs — the only page whose rows reach the run fragment, the second
/// of the two fragment routes. The call is one that spawned a run, because a tool body leads with
/// the run it started and that link is the only node link a pane inside a fragment mints.
fn mounting(levels: &Levels) -> Vec<String> {
    // An api call that made a `Task` call: the one call whose tool log mounts a body with a run
    // link in it, read from the tool's side of the join the run view makes.
    let rows = levels
        .store()
        .fetch(
            "SELECT c.session_id, c.source, c.id FROM live_api_calls c \
             JOIN live_tool_calls tc ON tc.session_id = c.session_id AND tc.source = c.source \
              AND tc.api_call_id = c.id \
             JOIN live_agent_runs a ON a.session_id = tc.session_id AND a.tool_use_id = tc.id \
              AND tc.source <> a.id \
             ORDER BY c.id LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("a recorded call spawned a run");
    let session_id = row.str("session_id").expect("a session").to_owned();
    let source = row.str("source").expect("a thread").to_owned();
    let call_id = row.str("id").expect("a call").to_owned();
    let loose = levels.candidates(Kind::Unattached)[0].clone();
    vec![
        nav_trees::node_url(Kind::Session, &session_id, MAIN, &session_id),
        nav_trees::url(&levels.open_turn()),
        nav_trees::node_url(Kind::Call, &session_id, &source, &call_id),
        nav_trees::node_url(Kind::Unattached, &loose.0, MAIN, &loose.0),
    ]
}

/// The kind of node a body mount opens, which its URL says just before the id.
fn mounted_kind(mount: &str) -> String {
    let (path, _) = split(mount);
    let parts: Vec<&str> = path.rsplit('/').collect();
    parts[1].to_owned()
}

/// A URL split into its path and its query, read as a name to one value.
fn split(url: &str) -> (String, BTreeMap<String, String>) {
    let (path, query) = url.split_once('?').unwrap_or((url, ""));
    let carried = query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (name, value) = pair.split_once('=').unwrap_or((pair, ""));
            (name.to_owned(), value.to_owned())
        })
        .collect();
    (path.to_owned(), carried)
}

/// What one knob of a URL carries, or nothing where the URL does not name it.
fn knob(url: &str, name: &str) -> Option<String> {
    split(url).1.get(name).cloned()
}
