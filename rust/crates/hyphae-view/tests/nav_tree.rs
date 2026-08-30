//! The NavTree row, as bytes.
//!
//! The prototype exists to answer whether ~92 components read better as `rsx!` functions than as
//! htpy calls (`plans/rust-prototype/design.md`), and the NavTree row is the one it is judged on:
//! it is the page's byte budget, and every attribute written in it is written thousands of times.
//! A snapshot is what makes a change to that row a thing a reviewer sees rather than infers.
//!
//! The rows come off a served page rather than off a hand-built node, so what is pinned is what
//! a reader gets: the row's own class list, the popover's trigger, the two cost badges, the
//! context bar's bands. The session is named rather than discovered — a fixture added to the
//! corpus must not silently re-point the exhibit at another session's rows.

mod common;

/// The fixture session the Python tier calls `SPINE`: a main thread with turns, agent runs
/// under them, and a cost on both halves — which is what draws the `$own/$total` pair.
const SPINE: &str = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";

#[tokio::test]
async fn the_nav_tree_rows_of_a_session_page() {
    let served = common::served(|_| {});
    let (status, page) = served.page(&format!("/session/{SPINE}")).await;
    assert_eq!(status, axum::http::StatusCode::OK);
    // One row per line, so a diff on the snapshot names the row that changed.
    let rows = page
        .split_inclusive("</li>")
        .filter(|piece| piece.contains("<li class=\"row"))
        .map(|piece| {
            let at = piece.find("<li class=\"row").expect("the filter found one");
            piece[at..].to_owned()
        })
        .collect::<Vec<_>>()
        .join("\n");
    insta::assert_snapshot!(rows);
}

#[tokio::test]
async fn the_preset_control_of_a_session_page() {
    // The three links above the rows, which is where a reader switches what the tree shows. In
    // the swapped element rather than above it: that is what keeps them pointing at the node a
    // click just landed on.
    let served = common::served(|_| {});
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    let at = page
        .find("<p class=\"presets\"")
        .expect("the control is on the page");
    let end = page[at..].find("</p>").expect("the control closes") + 4;
    insta::assert_snapshot!(page[at..at + end].to_owned());
}
