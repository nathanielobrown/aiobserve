//! The stylesheet a browser reads, held to what nothing else can check.
//!
//! Nothing renders CSS: a rule that selects a field no page writes, a comment closed twice, a
//! token retuned until the text on it stops being readable — each leaves a page that still serves
//! and still passes. So the sheet is read off the running app and checked against the pages it
//! paints. What one log's two prose columns owe is `node_rows.rs`.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;

use hyphae_store::Param;
use hyphae_testsupport::html::Markup;
use hyphae_testsupport::landmarks::FORK_ORIGIN;
use hyphae_testsupport::rows;
use hyphae_testsupport::served::Served;

/// The ratio WCAG 2.2 asks of body text against what it is printed on. Both schemes are held to
/// it: a dark page is a page someone reads, not a courtesy.
const READABLE: f64 = 4.5;

/// How much of the accent the one wash a page composes carries — `:target` on a record, and a
/// hovered node — over whatever surface it lands on.
const WASH: f64 = 0.12;

/// The deepest step of the cost badge, which is the most of `--hot` a row's wash ever carries. A
/// shallower step sits between it and the page it is painted on, so holding the deepest one
/// readable holds every step above it: each is a step back toward the paper.
const BADGE: f64 = 0.60;

/// One sRGB channel, linearised — the relative-luminance formula's own step.
fn channel(value: u8) -> f64 {
    let scaled = f64::from(value) / 255.0;
    if scaled <= 0.04045 {
        scaled / 12.92
    } else {
        ((scaled + 0.055) / 1.055).powf(2.4)
    }
}

/// The three channels of a `#rrggbb` token.
fn parts(color: &str) -> [u8; 3] {
    [1, 3, 5].map(|at| {
        u8::from_str_radix(&color[at..at + 2], 16)
            .unwrap_or_else(|_| panic!("a hex colour: {color}"))
    })
}

fn luminance(color: &str) -> f64 {
    let [red, green, blue] = parts(color);
    0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
}

fn contrast(ink: &str, surface: &str) -> f64 {
    let (one, other) = (luminance(ink), luminance(surface));
    let (lit, dark) = if one >= other {
        (one, other)
    } else {
        (other, one)
    };
    (lit + 0.05) / (dark + 0.05)
}

/// `color-mix(in srgb, ink part%, transparent)` painted over an opaque surface.
fn over(ink: &str, surface: &str, part: f64) -> String {
    let (ink, surface) = (parts(ink), parts(surface));
    let mut mixed = String::from("#");
    for at in 0..3 {
        let channel = (f64::from(ink[at]) * part + f64::from(surface[at]) * (1.0 - part)).round();
        mixed.push_str(&format!("{:02x}", channel as u8));
    }
    mixed
}

/// Every `--token: #rrggbb` one block of the sheet declares.
fn read(block: &str) -> BTreeMap<String, String> {
    Regex::new(r"--([a-z]+):\s*(#[0-9a-f]{6})")
        .expect("a pattern")
        .captures_iter(block)
        .map(|found| (found[1].to_owned(), found[2].to_owned()))
        .collect()
}

/// The served stylesheet.
async fn sheet(served: &Served) -> String {
    let (_, text) = served.page("/static/style.css").await;
    text
}

#[tokio::test]
async fn both_schemes_print_every_color_of_text_readably() {
    // Every color the stylesheet sets text in clears 4.5:1 over every surface it lands on.
    //
    // Read off the served stylesheet rather than written down, so a token retuned for one scheme
    // cannot quietly darken the other. The surfaces are the page itself and the one wash the sheet
    // composes rather than names — 12% of the accent, which a targeted record and a hovered node
    // are both painted with, and which is where `--dim` comes closest to failing. A chip's outline
    // is its own text color (`currentColor`), so it clears whatever this does.
    //
    // The cost badge is read apart from the rest: it is a surface only the dollar value is printed
    // on, so it is held to `--ink` alone rather than to every role.
    let style = sheet(&Served::corpus()).await;
    // Tokens are declared in exactly two places, and dark restates only what it changes.
    let (head, tail) = style
        .split_once("prefers-color-scheme: dark")
        .expect("the sheet declares a dark scheme");
    let light = read(head);
    let mut dark = light.clone();
    dark.extend(read(tail));
    // Two rosters, closed: the colours text is printed in, and the surfaces under it — the badge's
    // warm ground and the three bands the context bar draws (`view/static/style.css`). A surface
    // carries no text of its own, so what holds it is the eye on the gallery
    // (`.claude/rules/viewer-ui.md`) and the ramp below, not a contrast ratio.
    let declared: BTreeSet<&str> = light.keys().map(String::as_str).collect();
    assert_eq!(
        declared,
        BTreeSet::from([
            "ink", "dim", "line", "paper", "mark", "bad", "hot", "faint", "agent", "free",
        ])
    );
    for (scheme, tokens) in [("light", &light), ("dark", &dark)] {
        let surfaces = [
            ("the page", tokens["paper"].clone()),
            ("the wash", over(&tokens["mark"], &tokens["paper"], WASH)),
        ];
        for role in ["ink", "dim", "mark", "bad"] {
            for (where_, surface) in &surfaces {
                let ratio = contrast(&tokens[role], surface);
                assert!(
                    ratio >= READABLE,
                    "{scheme} --{role} on {where_}: {ratio:.2}:1"
                );
            }
        }
        // The badge composes over both of them, because the row under it may be the hovered one.
        for (where_, under) in &surfaces {
            let ratio = contrast(&tokens["ink"], &over(&tokens["hot"], under, BADGE));
            assert!(
                ratio >= READABLE,
                "{scheme} --ink on the badge over {where_}: {ratio:.2}:1"
            );
        }
        // And the context bar's three grounds are a ramp: the track palest, the base band a step
        // in from it, the conversation over that. Each scheme runs the ramp its own way — a light
        // page darkens toward the reader, a dark one lightens — so what is held is the order and
        // not the direction. Two bands a reader cannot tell apart is one band.
        let ramp: Vec<f64> = ["line", "faint", "dim"]
            .iter()
            .map(|role| luminance(&tokens[*role]))
            .collect();
        let ordered = if scheme == "light" {
            ramp[0] > ramp[1] && ramp[1] > ramp[2]
        } else {
            ramp[0] < ramp[1] && ramp[1] < ramp[2]
        };
        assert!(ordered, "{scheme} {ramp:?}");
    }
}

#[tokio::test]
async fn the_stylesheet_a_browser_reads_carries_no_prose_outside_a_comment() {
    // Nothing in the served stylesheet sits between a comment's end and the rule below it.
    //
    // A comment closed twice is the one CSS mistake nothing else here can see: the browser reads
    // the stray prose as the start of a selector, swallows the rule under it, and paints one fewer
    // thing than the file says — silently, because a stylesheet has no syntax error a server or a
    // test suite reports. Every comment this sheet opens is closed once, so a `*/` left over after
    // the comments come out is prose a browser is about to read as a selector.
    let style = sheet(&Served::corpus()).await;
    let bare = Regex::new(r"(?s)/\*.*?\*/")
        .expect("a pattern")
        .replace_all(&style, "");
    assert!(!bare.contains("*/"));
}

#[tokio::test]
async fn the_stylesheet_paints_only_fields_a_page_carries() {
    // Every `data-field` the stylesheet selects is a field a page writes, read off both.
    //
    // A `data-field` is what a test reads a page through, and the stylesheet reads pages through
    // the same names — but nothing renders CSS, so a field renamed in a template leaves the rule
    // behind, valid and matching nothing. One page carries all of them: a failed tool call's,
    // whose tree names each node and marks the failure, whose walk names the kind either side, and
    // which counts the session's failures under the pane.
    //
    // The `data-field` rules only. The depth ladder beside them runs to the NavTree's hard limit
    // of 16 levels and the deepest chain the corpus records is 14, so no page can show that the
    // top of that ladder is live.
    let served = Served::corpus();
    // The one failure this session recorded, which is the node whose page carries all four.
    let failed = rows::one(
        &served.db(),
        "SELECT source, id FROM live_tool_calls WHERE session_id = $session AND is_error",
        &[("session", Param::from(FORK_ORIGIN))],
    );
    let source = failed.str("source").expect("a thread");
    let tool_id = failed.str("id").expect("a tool id");
    let (_, page) = served
        .page(&format!(
            "/session/{FORK_ORIGIN}/thread/{source}/tool/{tool_id}"
        ))
        .await;
    let named = Regex::new(r#"data-field="([a-z_]+)""#).expect("a pattern");
    let painted: BTreeSet<String> = named
        .captures_iter(&sheet(&served).await)
        .map(|found| found[1].to_owned())
        .collect();
    assert!(
        !painted.is_empty(),
        "the stylesheet paints no field by name"
    );
    let written: BTreeSet<String> = named
        .captures_iter(&page)
        .map(|found| found[1].to_owned())
        .collect();
    let unpainted: Vec<&String> = painted.difference(&written).collect();
    assert!(
        unpainted.is_empty(),
        "painted but never written: {unpainted:?}"
    );
    // Read the same page through the markup reader, so the sweep above is over a page that really
    // rendered its fields rather than a string that happens to hold the attribute.
    assert!(!Markup::of(&page).fields("data-body", "tool").is_empty());
}
