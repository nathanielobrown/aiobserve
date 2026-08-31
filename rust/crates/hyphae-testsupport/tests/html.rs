//! The page reader, held to what a browser and htmx would do with the same markup.
//!
//! The one place in the workspace where invented markup is the right input: every other leaf
//! reads a page the viewer served, and what is under test here is the reading itself. Each
//! fragment below is the shape one reader has to get right, written small enough to see.

use hyphae_testsupport::html::{Markup, counted, money, plain};

#[test]
fn a_labelled_field_reads_as_the_text_a_browser_shows() {
    // Nested markup dropped and escapes undone: a value the page marked up is still one value.
    let markup = Markup::of(
        r#"<li data-nav-tree="turn:1">
             <span data-field="title">a <strong>bold</strong> &amp; brave turn</span>
           </li>"#,
    );
    assert_eq!(
        markup.field("data-nav-tree", "turn:1", "title"),
        "a bold & brave turn"
    );
    // And the markup itself, for the one value a page renders rather than prints.
    assert_eq!(
        markup.marked_up("data-nav-tree", "turn:1", "title"),
        "a <strong>bold</strong> &amp; brave turn"
    );
}

#[test]
fn two_elements_labelled_alike_read_as_one_value() {
    // A component may print a number in two halves — a wash on each — and a leaf asserting the
    // number must not have to know which half carried which digit.
    let markup = Markup::of(
        r#"<li data-nav-tree="run:1">
             <span data-field="cost_usd" class="w3">$0.</span><span data-field="cost_usd">40</span>
           </li>"#,
    );
    assert_eq!(markup.field("data-nav-tree", "run:1", "cost_usd"), "$0.40");
}

#[test]
fn a_space_written_as_a_child_is_a_space_the_reader_can_see() {
    // `fields` strips each value, so it cannot tell `0 errors` from `0errors`. That difference
    // is what a component's own children make, and only this reader is asked about it.
    let markup =
        Markup::of(r#"<p data-body="session"><span data-field="errors">0</span> errors</p>"#);
    assert_eq!(markup.reads("data-body", "session"), "0 errors");
}

#[test]
fn a_mark_inside_a_labelled_field_is_not_a_mark_of_its_own() {
    // A mark carries no `data-field` — it is not a value the store holds — so it is read by
    // class. One a session wrote *into* a value is part of that value, not a mark of the row's.
    let markup = Markup::of(
        r#"<li data-nav-tree="tool:1">
             <span class="icon">&#9881;</span>
             <span data-field="title">see <span class="icon">x</span> here</span>
           </li>"#,
    );
    assert_eq!(markup.icons("data-nav-tree", "tool:1"), ["\u{2699}"]);
}

#[test]
fn htmx_wiring_is_resolved_off_the_nearest_ancestor_that_carries_it() {
    // The NavTree writes the swap its rows share on the element it hands back, so a reader that
    // took a row's own attributes would see a page that works and one that does not alike.
    let markup = Markup::of(
        r##"<ul hx-target="#pane" hx-swap="innerHTML">
             <li data-nav-tree="turn:1" hx-swap="outerHTML">
               <a href="/session/s/turn/1" hx-get="/session/s/turn/1">go</a>
               <button hx-get="/session/s/turn/1/body">open</button>
             </li>
           </ul>"##,
    );
    let wiring = markup.wired("data-nav-tree");
    assert_eq!(wiring.len(), 2, "a row holding two fetches gives two pairs");
    let (row, link) = &wiring[0];
    assert_eq!(row, "turn:1");
    assert_eq!(link["href"], "/session/s/turn/1");
    assert_eq!(link["hx-get"], "/session/s/turn/1");
    assert_eq!(link["hx-target"], "#pane", "inherited from the list");
    assert_eq!(link["hx-swap"], "outerHTML", "the nearer one wins");
    // The button carries no href of its own and inherits none: `href` is not htmx's to inherit.
    let (_, button) = &wiring[1];
    assert!(!button.contains_key("href"), "{button:?}");
}

#[test]
fn a_block_keeps_the_leading_newline_a_parser_would_drop() {
    // The reason the fat values are read by pattern: HTML says a newline straight after `<pre>`
    // is not content, so a parsed tree cannot answer what the page was served as — and what a
    // code block is served as is the thing under test.
    let markup = Markup::of("<pre data-field=\"input\" class=\"language-py\">\nx = 1\n</pre>");
    assert_eq!(markup.block("input"), "\nx = 1\n");
    assert_eq!(markup.walled("input"), "language-py");
    // A block with no class is a value printed as the characters the store holds.
    let bare = Markup::of("<pre data-field=\"input\">x = 1</pre>");
    assert_eq!(bare.walled("input"), "");
}

#[test]
fn a_nav_tree_row_pairs_a_depth_with_the_key_beside_it() {
    // A cap's tail row carries a depth and no key, so the two attributes cannot be scanned
    // apart: a tail row's depth would pair with the next row's key and every row after it
    // would read one place off.
    let markup = Markup::of(
        r#"<ul>
             <li data-depth="0" data-nav-tree="session:s" data-selected="session:s"></li>
             <li data-depth="1" data-nav-tree="turn:1"></li>
             <li data-depth="2" data-nav-tree="call:1"></li>
             <li data-depth="2" class="tail">+3 more</li>
             <li data-depth="1" data-nav-tree="turn:2"></li>
           </ul>"#,
    );
    assert_eq!(
        markup.rows(),
        [
            (0, "session:s".to_owned()),
            (1, "turn:1".to_owned()),
            (2, "call:1".to_owned()),
            (1, "turn:2".to_owned()),
        ]
    );
    // What belongs to a row is the run of rows deeper than it, up to the next at its own depth.
    assert_eq!(markup.under("session:s"), ["turn:1", "turn:2"]);
    assert_eq!(markup.under("turn:1"), ["call:1"]);
    assert_eq!(markup.kin(), ["turn:1", "turn:2"], "under the selection");
    // Whatever the tag's layout: how a row is laid out belongs to the component that writes it,
    // which today names these attributes in this order and puts nothing between two of them. A
    // third attribute standing between them is invented for exactly that reason, standing for a
    // layout a row is free to grow into.
    let apart = Markup::of(
        r#"<li class="row node" data-depth="2" data-selected="turn:a" data-nav-tree="turn:a"></li>"#,
    );
    assert_eq!(apart.rows(), [(2, "turn:a".to_owned())]);
    // A tail row's depth, and the next tag's key: two tags, so nothing to pair. On one line, so
    // the `>` is the only thing that can separate them — a newline between the tags would part
    // them on its own, whatever the pattern says about tag boundaries.
    let tail = Markup::of(
        r#"<li class="row more" data-depth="1" data-more="session:s"><a data-nav-tree="turn:b">"#,
    );
    assert!(tail.rows().is_empty(), "{:?}", tail.rows());
}

#[test]
fn a_rows_bands_and_badges_are_read_off_its_classes() {
    // The bar is a set of nested prefixes drawn as classes, and the badge is a wash per step.
    // A row that draws no band of a kind answers nothing for it rather than zero.
    let markup = Markup::of(
        r#"<li data-nav-tree="turn:1" class="row f42 p30 b12 compacted">
             <span data-field="cost_usd" class="w2">$0.40</span>
             <span data-field="total_usd" class="w5">$1.20</span>
             <span data-field="turns">3</span>
           </li>"#,
    );
    let bar = markup.bar("turn:1");
    assert_eq!(
        (bar.fill, bar.prior, bar.base),
        (Some(42), Some(30), Some(12))
    );
    assert!(markup.marked("turn:1", "compacted"));
    assert!(!markup.marked("turn:1", "row f42"), "a class, not the list");
    let badges = markup.badges("turn:1");
    assert_eq!(badges.keys().collect::<Vec<_>>(), ["cost_usd", "total_usd"]);
    assert_eq!(badges["cost_usd"].shown, "$0.40");
    assert_eq!(badges["total_usd"].step, "w5");

    let bare = Markup::of(r#"<li data-nav-tree="session:s" class="row f9"></li>"#);
    let none = bare.bar("session:s");
    assert_eq!(
        (none.prior, none.base),
        (None, None),
        "nothing stood before"
    );
}

#[test]
fn a_filter_box_offers_what_it_lists_whatever_attribute_comes_first() {
    // A pattern anchored on the tag's first attribute reads a box the browser fills as an empty
    // one, so a leaf asserting that nothing is offered would pass on markup offering everything.
    let markup = Markup::of(
        r#"<datalist id="projects">
             <option data-field="project_dir" value="/repo/one">
             <option value="/repo/two">
           </datalist>"#,
    );
    assert_eq!(markup.suggestions(), ["/repo/one", "/repo/two"]);
}

#[test]
fn a_log_heads_each_column_with_the_word_the_registry_gives_it() {
    // Whitespace collapsed the way a browser collapses it: the mark, one space, and the label.
    let markup = Markup::of(
        r#"<table><tr>
             <th scope="col" data-column="cost_usd"><span class="icon">$</span>
                Cost</th>
             <th data-column="turns">Turns</th>
           </tr></table>"#,
    );
    let headings = markup.headings();
    assert_eq!(headings["cost_usd"], "$ Cost");
    assert_eq!(headings["turns"], "Turns");
}

#[test]
fn what_a_browser_shows_of_a_run_of_markup_is_the_text_in_it() {
    assert_eq!(
        plain("<span class=\"k\">def</span> f&lt;T&gt;()"),
        "def f<T>()"
    );
}

#[test]
fn a_number_reads_back_the_way_the_pages_print_it() {
    // The two printed forms every expectation is written against.
    assert_eq!(money(0.4), "$0.40");
    assert_eq!(money(12.005), "$12.01");
    assert_eq!(counted(1), "1");
    assert_eq!(counted(999), "999");
    assert_eq!(counted(1_000), "1,000");
    assert_eq!(counted(1_234_567), "1,234,567");
    assert_eq!(counted(0), "0");
    assert_eq!(counted(-4_200), "-4,200");
}
