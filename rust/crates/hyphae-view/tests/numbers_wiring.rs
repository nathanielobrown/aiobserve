//! What fetches a popover, what places it, and where the viewer declines to serve one.
//!
//! Ported from the four leaves of `tests/view/test_numbers.py` that read the page around a popover
//! rather than the numbers inside it. Split off from `numbers.rs`, which holds the arithmetic, so
//! neither file runs past the length budget.

use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::{ANCESTOR, MAIN, SPINE};
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::{Kind, NUMBERS_URL};
use regex::Regex;

#[tokio::test]
async fn the_popovers_placement_rides_a_file_the_policy_allows() {
    // Where a popover stands and where the NavTree opens are a script's, and it is a real file.
    //
    // The stylesheet places the popover's left edge and can do nothing about its top, which follows
    // the row a reader is pointing at; nothing in CSS scrolls the selected row into view either.
    // Both are `static/nav-tree.js`, which is a file because `app::CSP` allows no inline script — so
    // what a served page can prove is that the page asks for it, that it arrives, and that no page
    // carries a line of script of its own.
    let served = Served::corpus();
    let answer = served.get("/static/nav-tree.js").await;
    assert!(answer.status().is_success());
    let kind = answer
        .headers()
        .get("content-type")
        .expect("a content type")
        .to_str()
        .expect("an ASCII content type")
        .to_owned();
    assert!(kind.contains("javascript"), "{kind}");
    let (_, page) = served.page(&format!("/session/{SPINE}")).await;
    assert!(
        page.contains("<script src=\"/static/nav-tree.js\""),
        "{page}"
    );
    // Every script on the page is a `src` with an empty body, and no attribute holds a handler.
    let body = Regex::new(r"(?s)<script[^>]*>(.*?)</script>").expect("a pattern");
    for found in body.captures_iter(&page) {
        assert!(
            found[1].trim().is_empty(),
            "an inline script: {}",
            &found[1]
        );
    }
    // The Python spells the same claim as one negative look-ahead, which `regex` does not have:
    // read here off every opening tag instead, which also sees a `<script>` that never closed.
    let opened = Regex::new(r"<script([^>]*)>").expect("a pattern");
    let mut carried = 0;
    for found in opened.captures_iter(&page) {
        carried += 1;
        assert!(
            found[1].contains(" src="),
            "a script with no src: {}",
            &found[1]
        );
    }
    assert!(carried > 0, "the page asks for no script at all");
    let handler = Regex::new(r"\son[a-z]+=").expect("a pattern");
    assert!(
        handler.find(&page).is_none(),
        "an inline handler on the page"
    );
}

#[tokio::test]
async fn a_row_fetches_its_numbers_when_a_pointer_arrives_and_when_a_key_does() {
    // Hover and keyboard reach the same fetch, once per row, and the row's link is untouched.
    //
    // The trigger listens on the row — `focusin` bubbles where `focus` does not, so a trigger on the
    // row hears the link inside it being tabbed to — but it is *carried* by a sibling of that link.
    // htmx inherits its attributes down the NavTree, so the overrides a popover needs would be
    // inherited by the link if they sat on the row itself, and a click would swap a popover's markup
    // where the pane belongs. The last assertion here is that trap.
    let served = Served::corpus();
    let (_, html) = served.page(&format!("/session/{SPINE}")).await;
    let page = Markup::of(&html);
    let key = format!("{}:{SPINE}", Kind::Session);
    let triggers = page.inside("data-nav-tree", &key, "hx-trigger");
    let [trigger] = triggers.as_slice() else {
        panic!("one trigger on the session's row, not {triggers:?}");
    };
    let (pointer, keyboard) = trigger.split_once(", ").expect("two triggers");
    // Heard on the row, once apiece: the popover is markup that stays, and a second fetch would
    // stack another under the first.
    assert!(
        pointer.starts_with("mouseenter from:closest li once"),
        "{pointer}"
    );
    assert_eq!(keyboard, "focusin from:closest li once");
    // Delayed on the pointer alone, so running one down the NavTree does not fetch every row it
    // crossed. A key press is deliberate and waits for nothing.
    assert!(
        Regex::new(r"delay:\d+m?s")
            .expect("a pattern")
            .is_match(pointer),
        "{pointer}"
    );
    let wiring = page.wired("data-nav-tree");
    let fetched: Vec<(String, std::collections::BTreeMap<String, String>)> = wiring
        .iter()
        .filter(|(_, at)| at["hx-get"].starts_with(NUMBERS_URL))
        .cloned()
        .collect();
    let at = &fetched
        .iter()
        .find(|(row, _)| *row == key)
        .unwrap_or_else(|| panic!("the session's row mints no popover URL"))
        .1;
    assert_eq!(at["hx-get"], format!("{NUMBERS_URL}/session/{SPINE}"));
    assert_eq!(at["hx-target"], "this");
    assert_eq!(at["hx-swap"], "beforeend");
    assert_eq!(at["hx-push-url"], "false");
    // A pane's own selectors would take the popover apart, so both are unset.
    assert_eq!(at["hx-select"], "unset");
    assert_eq!(at["hx-select-oob"], "unset");
    // And only the kinds that have numbers carry one — every kind that stands for a row of the
    // store, which is all of them but the two buckets.
    let numbered: Vec<&str> = [
        Kind::Session,
        Kind::Turn,
        Kind::Run,
        Kind::Call,
        Kind::Tool,
        Kind::Compaction,
    ]
    .iter()
    .map(|kind| kind.word())
    .collect();
    for (row, _) in &fetched {
        let kind = row.split(':').next().expect("a kind");
        assert!(numbered.contains(&kind), "{kind} carries a popover");
    }
    // The link a row is still a link: it swaps the pane out of `#nav-tree-rows`'s own wiring, and
    // nothing the popover wrote reached it.
    let link = &wiring
        .iter()
        .find(|(row, at)| *row == key && !at["hx-get"].starts_with(NUMBERS_URL))
        .expect("the session's row is still a link")
        .1;
    assert_eq!(link["hx-target"], "#reading-pane");
    assert_eq!(link["hx-select"], "#reading-pane");
}

#[tokio::test]
async fn a_kind_with_no_numbers_is_a_route_that_answers_nothing() {
    // A bucket has nothing to print, so the route 404s rather than serving an empty popover.
    //
    // A bucket is a place rather than a node — it stands for no row of the store — so there is
    // nothing to count under it. Every kind that does stand for a row now carries a popover, the
    // compaction included: what it shows is `numbers_compaction.rs`.
    let served = Served::corpus();
    for path in [
        format!(
            "/session/{ANCESTOR}/thread/{MAIN}/{}/{MAIN}",
            Kind::Unattributed
        ),
        format!(
            "/session/{ANCESTOR}/thread/{MAIN}/{}/{ANCESTOR}",
            Kind::Unattached
        ),
    ] {
        let (status, _) = served.page(&format!("{NUMBERS_URL}{path}")).await;
        assert_eq!(status, 404, "{path}");
    }
}

#[tokio::test]
async fn a_popover_is_hidden_until_its_row_is_pointed_at_or_tabbed_into() {
    // One stylesheet rule shows it, and it covers the keyboard as well as the pointer.
    //
    // `:focus-within` rather than `:focus`, because the row itself is not focusable — and it is also
    // what holds the popover open while a reader selects the numbers out of it, which is the copy
    // affordance a pin would otherwise have to be built for.
    let (_, style) = Served::corpus().page("/static/style.css").await;
    for declared in [
        r"\.popover\s*\{[^{}]*display: none",
        r"\.popover\s*\{[^{}]*position: fixed",
        r"#nav-tree-grip\s*\{[^{}]*width: var\(--grip-width\)",
    ] {
        // Fixed rather than absolute: `#nav-tree` scrolls under `overflow: auto`, which clips
        // anything positioned inside it — and a popover of numbers is wider than the NavTree.
        assert!(
            Regex::new(declared).expect("a pattern").is_match(&style),
            "{declared} is not in the stylesheet"
        );
    }
    // And it stands where the reading pane does: the NavTree's width, the grip between the columns,
    // and the gutter on either side of it. Measured from the same `--grip-width` the grip is drawn
    // at, so a popover cannot come to rest on top of the handle a reader drags.
    let left = Regex::new(r"\.popover\s*\{[^{}]*left:([^;]*);")
        .expect("a pattern")
        .captures(&style)
        .expect("the popover names no left edge");
    let edge = left[1].to_owned();
    assert!(edge.contains("--nav-tree-width"), "{edge}");
    assert!(edge.contains("--grip-width"), "{edge}");
    let rules = Regex::new(r"([^{}]*)\{([^{}]*)\}").expect("a pattern");
    let shown: Vec<String> = rules
        .captures_iter(&style)
        .filter(|rule| rule[1].contains(".popover") && rule[2].contains("display: block"))
        .map(|rule| rule[1].to_owned())
        .collect();
    assert!(!shown.is_empty(), "nothing shows the popover");
    for selector in &shown {
        assert!(selector.contains(":hover"), "{selector}");
        assert!(selector.contains(":focus-within"), "{selector}");
    }
}
