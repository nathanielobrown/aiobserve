//! Reading a served page back: what a browser would show, and what htmx would do with it.
//!
//! The port of `tests/view/conftest.py`'s reader, and deliberately as thin: it pulls values out
//! of `data-` attributes, which the components carry for exactly this reason. A test that
//! matched rendered prose would fail on a wording change, and one that read the store instead
//! would prove nothing about the page.
//!
//! Two readers, as in Python. Most of this walks a parsed tree, so an attribute htmx inherits
//! from an ancestor reads the way htmx resolves it. The handful that go by pattern — the fat
//! `<pre>` blocks and the NavTree's row scan — go by pattern on purpose: a parser normalizes a
//! `<pre>`'s leading newline away, and what a code block is served as is the thing under test.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use hyphae_extract::pricing::CONTEXT_WINDOWS;
use hyphae_store::queries;
use hyphae_view::nodes::BAR_STEPS;
use regex::Regex;
use scraper::{ElementRef, Html, Node};

/// Attributes htmx reads off the closest ancestor that carries one, so a page can write the
/// half every link shares once and leave each link carrying only what differs. `hx-get` and
/// `href` are not among them: htmx finds the elements to wire by their own `hx-get`.
const INHERITED: &[&str] = &[
    "hx-target",
    "hx-swap",
    "hx-select",
    "hx-select-oob",
    "hx-push-url",
];

/// One served page, parsed once and read many ways.
pub struct Markup {
    served: String,
    dom: Html,
}

impl Markup {
    /// Read one served document.
    pub fn of(served: &str) -> Self {
        Self {
            served: served.to_owned(),
            dom: Html::parse_document(served),
        }
    }

    /// Read one served table row: the fragment a children log's View button swaps in.
    ///
    /// A bare `<tr>` is dropped on the way in — the parser reads a document, and outside a table
    /// there is nowhere to put a row — so the wrapper is what lets a scoped reader see the row and
    /// the cell it spans. What `served` returns is still the bytes the route sent.
    pub fn row(served: &str) -> Self {
        Self {
            served: served.to_owned(),
            dom: Html::parse_document(&format!("<table>{served}</table>")),
        }
    }

    /// The bytes the page was served as, for a leaf that asserts on the document itself.
    pub fn served(&self) -> &str {
        &self.served
    }

    /// One element's labelled fields, keyed by `data-field` and stripped of whitespace.
    ///
    /// Two elements labelled the same inside one scope read as one value, which is what lets a
    /// component print a number in two halves and a leaf assert the number.
    pub fn fields(&self, attribute: &str, value: &str) -> BTreeMap<String, String> {
        let mut found: BTreeMap<String, String> = BTreeMap::new();
        for element in self.labelled(attribute, value) {
            let name = element.attr("data-field").expect("the filter found one");
            found
                .entry(name.to_owned())
                .or_default()
                .push_str(&text_of(element));
        }
        found
            .into_iter()
            .map(|(name, text)| (name, text.trim().to_owned()))
            .collect()
    }

    /// One labelled field of one element, which is what most leaves want.
    pub fn field(&self, attribute: &str, value: &str, name: &str) -> String {
        self.fields(attribute, value)
            .remove(name)
            .unwrap_or_else(|| panic!("no {name} field inside {attribute}={value}"))
    }

    /// The markup inside one labelled field of one element, tags and all.
    ///
    /// What [`Markup::fields`] reads as text — for the one value a page renders rather than
    /// prints, a title written in markdown. Scoped by the element around it, because a title
    /// is printed in a dozen places on a page and only the pane's heading may carry a link.
    pub fn marked_up(&self, attribute: &str, value: &str, name: &str) -> String {
        self.labelled(attribute, value)
            .into_iter()
            .find(|element| element.attr("data-field") == Some(name))
            .unwrap_or_else(|| panic!("no {name} field inside {attribute}={value}"))
            .inner_html()
    }

    /// What a browser shows of one element, its whitespace collapsed the way a browser does.
    ///
    /// The one reader here that can see a space between two values: [`Markup::fields`] strips
    /// each one and [`plain`] keeps the markup's own indentation, so neither can tell
    /// `0 errors` from `0errors`. That is the difference a component's own children make.
    pub fn reads(&self, attribute: &str, value: &str) -> String {
        collapsed(&text_of(self.element(attribute, value)))
    }

    /// The bare marks inside one element, in document order.
    ///
    /// Read by class rather than by a `data-` key: a mark is not a value the store holds, so
    /// it carries no `data-field` — and a key naming it would be twenty bytes on every one of
    /// a node page's NavTree rows.
    pub fn icons(&self, attribute: &str, value: &str) -> Vec<String> {
        let scope = self.element(attribute, value);
        descendants(scope)
            .filter(|element| {
                classes(*element).contains("icon")
                    && !within_field(*element, scope)
                    && element.attr("data-field").is_none()
            })
            .map(|element| text_of(element))
            .collect()
    }

    /// Every `inner` attribute value found on or inside the element carrying `attribute=value`.
    pub fn inside(&self, attribute: &str, value: &str, inner: &str) -> Vec<String> {
        let scope = self.element(attribute, value);
        std::iter::once(scope)
            .chain(descendants(scope))
            .filter_map(|element| element.attr(inner).map(str::to_owned))
            .collect()
    }

    /// Every labelled value inside one element, keyed by field, with the classes it wears.
    ///
    /// A wash is a class per step of a share, and it rides on the value it washes rather than
    /// on what holds it: a NavTree row draws two of them and a popover four, so the element is
    /// never what says which share a step stands for.
    pub fn washes(&self, attribute: &str, value: &str) -> BTreeMap<String, String> {
        self.labelled(attribute, value)
            .into_iter()
            .map(|element| {
                (
                    element
                        .attr("data-field")
                        .expect("the filter found one")
                        .to_owned(),
                    element.attr("class").unwrap_or_default().to_owned(),
                )
            })
            .collect()
    }

    /// What htmx would do, for every fetching element under a `key` attribute, in page order.
    ///
    /// Inheritance and all: the NavTree writes the swap its rows share on the element it hands
    /// back, so an assertion on a row's own attributes would read a page that works and one
    /// that does not the same way. Each pair is the `key` of the row an element sits in and
    /// its wiring; a row holding two of them — a link and a body toggle — gives two pairs.
    pub fn wired(&self, key: &str) -> Vec<(String, BTreeMap<String, String>)> {
        let mut wiring = Vec::new();
        for element in descendants(self.dom.root_element()) {
            if element.attr("hx-get").is_none() {
                continue;
            }
            // Innermost first, which is the order htmx resolves an inherited attribute in.
            let near: Vec<ElementRef> = std::iter::once(element)
                .chain(element.ancestors().filter_map(ElementRef::wrap))
                .collect();
            let Some(row) = nearest(&near, key) else {
                continue;
            };
            let mut resolved = BTreeMap::new();
            for name in std::iter::once(&"href")
                .chain(std::iter::once(&"hx-get"))
                .chain(INHERITED)
            {
                if let Some(found) = nearest(&near, name) {
                    resolved.insert((*name).to_owned(), found);
                }
            }
            wiring.push((row, resolved));
        }
        wiring
    }

    /// What each column of a children log heads itself with, keyed by the column it heads.
    ///
    /// Whitespace collapsed the way a browser collapses it: the mark, one space, and the word
    /// the label registry gives the column.
    pub fn headings(&self) -> BTreeMap<String, String> {
        static HEADING: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"(?s)<th [^>]*data-column="([^"]*)"[^>]*>(.*?)</th>"#).expect("a pattern")
        });
        HEADING
            .captures_iter(&self.served)
            .map(|found| (found[1].to_owned(), collapsed(&plain(&found[2]))))
            .collect()
    }

    /// The markup inside one `<pre data-field="…">`, whole.
    ///
    /// What [`Markup::fields`] cannot give back: that reader hands back the text a browser
    /// shows, and a block of marked-up code is nothing but nested spans.
    pub fn block(&self, field: &str) -> String {
        let pattern = Regex::new(&format!(
            r#"(?s)<pre data-field="{}"[^>]*>(.*?)</pre>"#,
            regex::escape(field)
        ))
        .expect("a pattern");
        pattern
            .captures(&self.served)
            .unwrap_or_else(|| panic!("no {field} block on the page"))[1]
            .to_owned()
    }

    /// The class on one `<pre data-field="…">`: the syntax the page marked that value up in.
    ///
    /// Empty where the block carries none, which is a value printed as the characters the
    /// store holds — the fallback every unmarkable value takes.
    pub fn walled(&self, field: &str) -> String {
        let pattern = Regex::new(&format!(
            r#"<pre data-field="{}"(?: class="([^"]*)")?>"#,
            regex::escape(field)
        ))
        .expect("a pattern");
        let found = pattern
            .captures(&self.served)
            .unwrap_or_else(|| panic!("no {field} block on the page"));
        found
            .get(1)
            .map_or(String::new(), |at| at.as_str().to_owned())
    }

    /// The markup inside one `<div class="prose" data-field="…">`, whole.
    ///
    /// The other half of [`Markup::block`]: what a pane renders as the markdown a session
    /// wrote, which is nested elements rather than one run of text.
    pub fn prose(&self, field: &str) -> String {
        let pattern = Regex::new(&format!(
            r#"(?s)<div class="prose" data-field="{}"[^>]*>(.*?)</div>"#,
            regex::escape(field)
        ))
        .expect("a pattern");
        pattern
            .captures(&self.served)
            .unwrap_or_else(|| panic!("no {field} prose on the page"))[1]
            .to_owned()
    }

    /// Every value of one data attribute in the document, in document order.
    pub fn values(&self, attribute: &str) -> Vec<String> {
        let pattern =
            Regex::new(&format!(r#"{}="([^"]*)""#, regex::escape(attribute))).expect("a pattern");
        pattern
            .captures_iter(&self.served)
            .map(|found| found[1].to_owned())
            .collect()
    }

    /// The project paths the list's filter box offers, in the order it offers them.
    ///
    /// Any attribute may sit in front of the value's own: a pattern anchored on the tag's first
    /// attribute reads a box the browser fills as an empty one, and a leaf asserting that
    /// nothing is offered would pass on markup offering everything.
    pub fn suggestions(&self) -> Vec<String> {
        static OPTION: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r#"<option\s[^>]*\bvalue="([^"]*)""#).expect("a pattern"));
        OPTION
            .captures_iter(&self.served)
            .map(|found| found[1].to_owned())
            .collect()
    }

    /// Every NavTree row that stands for a node: its depth beside its key, in document order.
    ///
    /// Read as a pair rather than as two attribute scans because a cap's tail row carries a
    /// depth and no key, so the two lists are not the same length whenever a level was cut.
    pub fn rows(&self) -> Vec<(usize, String)> {
        static ROW: LazyLock<Regex> = LazyLock::new(|| {
            Regex::new(r#"data-depth="(\d+)"[^>]*?\sdata-nav-tree="([^"]*)""#).expect("a pattern")
        });
        ROW.captures_iter(&self.served)
            .map(|found| (found[1].parse().expect("a depth"), found[2].to_owned()))
            .collect()
    }

    /// The rows the NavTree draws directly under one row, as node keys in document order.
    ///
    /// Containment rather than depth: a run renders under its nearest visible ancestor, so a
    /// closed row anywhere on the page stands runs at whatever depth it sits at plus one.
    pub fn under(&self, key: &str) -> Vec<String> {
        let drawn = self.rows();
        let at = drawn
            .iter()
            .position(|(_, drawn_key)| drawn_key == key)
            .unwrap_or_else(|| panic!("no NavTree row for {key}"));
        let depth = drawn[at].0;
        let mut kin = Vec::new();
        for (row_depth, row_key) in &drawn[at + 1..] {
            if *row_depth <= depth {
                break;
            }
            if *row_depth == depth + 1 {
                kin.push(row_key.clone());
            }
        }
        kin
    }

    /// The children the NavTree opened under the selection, as node keys in document order.
    pub fn kin(&self) -> Vec<String> {
        let selected = self.values("data-selected");
        let key = selected.first().expect("the page marks a selection");
        self.under(key)
    }

    /// The steps a NavTree row's context bar is drawn at, read back off its classes.
    ///
    /// Shared, because the bar is where the NavTree's numbers and the popover's have to
    /// disagree: a turn that gave the window back draws no tip and prints a negative delta.
    pub fn bar(&self, key: &str) -> Bar {
        static STEP: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r"^([fpb])(\d+)$").expect("a pattern"));
        let mut steps = BTreeMap::new();
        let drawn = self.inside("data-nav-tree", key, "class");
        for name in drawn
            .first()
            .expect("the row carries a class")
            .split_whitespace()
        {
            if let Some(found) = STEP.captures(name) {
                steps.insert(
                    found[1].to_owned(),
                    found[2].parse::<i64>().expect("a step"),
                );
            }
        }
        Bar {
            fill: steps.get("f").copied(),
            prior: steps.get("p").copied(),
            base: steps.get("b").copied(),
        }
    }

    /// Whether one NavTree row carries a bare class — a mark rather than a step.
    pub fn marked(&self, key: &str, name: &str) -> bool {
        self.inside("data-nav-tree", key, "class")
            .first()
            .expect("the row carries a class")
            .split_whitespace()
            .any(|found| found == name)
    }

    /// A row's cost badge, half by half, keyed by the field each half carries.
    ///
    /// `cost_usd` is what the node's own thread spent and `total_usd` what its whole subtree
    /// did, so a row printing one number answers with one entry. Each half wears its own step
    /// class: a pair drawn at one depth is a pair that took its share against the same number
    /// twice.
    pub fn badges(&self, key: &str) -> BTreeMap<String, Badge> {
        let shown = self.fields("data-nav-tree", key);
        self.washes("data-nav-tree", key)
            .into_iter()
            .filter(|(name, _)| name == "cost_usd" || name == "total_usd")
            .map(|(name, step)| {
                let printed = shown.get(&name).cloned().unwrap_or_default();
                (
                    name,
                    Badge {
                        shown: printed,
                        step,
                    },
                )
            })
            .collect()
    }

    /// The one element carrying `attribute="value"`, which every scoped reader starts from.
    pub fn element(&self, attribute: &str, value: &str) -> ElementRef<'_> {
        descendants(self.dom.root_element())
            .find(|element| element.attr(attribute) == Some(value))
            .unwrap_or_else(|| panic!("no element carrying {attribute}={value}"))
    }

    /// Whether the page carries an element with this attribute at all.
    pub fn holds(&self, attribute: &str, value: &str) -> bool {
        descendants(self.dom.root_element()).any(|element| element.attr(attribute) == Some(value))
    }

    /// Every labelled element inside one scope, in document order.
    fn labelled(&self, attribute: &str, value: &str) -> Vec<ElementRef<'_>> {
        descendants(self.element(attribute, value))
            .filter(|element| element.attr("data-field").is_some())
            .collect()
    }
}

/// Where a row's context bar draws each of its bands, as steps of the window.
///
/// Three cumulative edges rather than three widths: the bar is a set of nested prefixes, so a
/// band is the ground between two of them and the last one is the whole of the fill. A row that
/// draws no band of a kind answers `None` for it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Bar {
    /// How full the window was when the node ended, which is where the bar ends.
    pub fill: Option<i64>,
    /// Where what the node itself added begins: everything left of it was already there.
    pub prior: Option<i64>,
    /// Where the conversation begins — the context the session opened on.
    pub base: Option<i64>,
}

/// One half of a row's cost badge: what it printed, and the step its wash is drawn at.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Badge {
    pub shown: String,
    pub step: String,
}

/// What a browser shows of a run of markup: the tags dropped, the escapes undone.
///
/// For the two places a value is marked up rather than printed — highlighted code, and the
/// spans a cut leaves behind — where reading the text back is how a leaf proves the markup
/// added nothing and lost nothing.
pub fn plain(html: &str) -> String {
    static TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]*>").expect("a pattern"));
    html_escape::decode_html_entities(&TAG.replace_all(html, "")).into_owned()
}

/// Every class the highlighter wrote into one run of markup.
///
/// Split on whitespace: an element may carry more than one class, and a reader that took the
/// attribute whole would silently skip exactly the tokens a highlighter has no short name for.
pub fn classed(html: &str) -> BTreeSet<String> {
    static CLASS: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"class="([^"]*)""#).expect("a pattern"));
    CLASS
        .captures_iter(html)
        .flat_map(|found| {
            found[1]
                .split_whitespace()
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .collect()
}

/// What every list citation says about the display cut, which the viewer composes around the
/// query the same way it composes the paging: re-running the file alone answers whole values.
pub fn cut() -> String {
    format!(
        "head_chars={} item_chars={} head_items={}",
        queries::LIST_CHARS,
        queries::LIST_ITEM_CHARS,
        queries::LIST_ITEMS
    )
}

/// A cost as the pages print it.
pub fn money(amount: f64) -> String {
    format!("${amount:.2}")
}

/// Which step of the bar a token count lands on, in the model's own window.
///
/// The ladder restated rather than imported: `nodes` owns how a share becomes a class, and an
/// oracle reading that would agree with it whatever it said. A fill past the window is held at the
/// top — the window a request asked for is not a `message.model` our table can key on, so a call
/// above it is drawn full rather than given a scale of its own.
pub fn step(tokens: Option<i64>, model: &str) -> Option<i64> {
    let tokens = tokens?;
    // A model outside the table is a scale the oracle cannot invent, so it is a failure rather
    // than a `None` that would agree with a bar the viewer declined to draw.
    let (_, window) = CONTEXT_WINDOWS
        .iter()
        .find(|(name, _)| *name == model)
        .unwrap_or_else(|| panic!("no window recorded for {model}"));
    Some(((tokens as f64 / *window as f64 * BAR_STEPS as f64).round() as i64).min(BAR_STEPS))
}

/// The three edges a bar draws, from the tokens each band stands for.
///
/// The oracle every bar leaf is written against, and the one place the nesting rule is restated:
/// a band is a prefix of the one that holds it, so an edge is held at the fill above it and at the
/// base below it. Written here rather than derived from the viewer, so an implementation that let
/// a band run past its holder has nothing to agree with.
pub fn bands(fill: i64, prior: i64, base: Option<i64>, model: &str) -> Bar {
    let top = step(Some(fill), model).expect("a fill lands on a step");
    let grounded = base.map(|tokens| step(Some(tokens), model).unwrap_or(0).min(top));
    Bar {
        fill: Some(top),
        prior: Some(
            step(Some(prior), model)
                .unwrap_or(0)
                .min(top)
                .max(grounded.unwrap_or(0)),
        ),
        base: grounded,
    }
}

/// A count as the pages print it: thousands separated.
pub fn counted(value: i64) -> String {
    let digits = value.abs().to_string();
    let mut grouped = String::new();
    for (at, digit) in digits.chars().enumerate() {
        if at > 0 && (digits.len() - at).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(digit);
    }
    if value < 0 {
        format!("-{grouped}")
    } else {
        grouped
    }
}

/// One run of text with its whitespace collapsed the way a browser collapses it.
fn collapsed(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Every element under one, in document order, the element itself excluded.
fn descendants(scope: ElementRef<'_>) -> impl Iterator<Item = ElementRef<'_>> {
    let at = scope.id();
    scope
        .descendants()
        .filter(move |node| node.id() != at)
        .filter_map(ElementRef::wrap)
}

/// The text a browser shows of one element, its own escapes undone by the parser already.
fn text_of(element: ElementRef<'_>) -> String {
    element
        .descendants()
        .filter_map(|node| match node.value() {
            Node::Text(text) => Some(text.to_string()),
            _ => None,
        })
        .collect()
}

/// The classes one element wears.
fn classes(element: ElementRef<'_>) -> BTreeSet<&str> {
    element
        .attr("class")
        .unwrap_or_default()
        .split_whitespace()
        .collect()
}

/// Whether a labelled field stands between this element and the scope it was found in.
fn within_field(element: ElementRef<'_>, scope: ElementRef<'_>) -> bool {
    element
        .ancestors()
        .filter_map(ElementRef::wrap)
        .take_while(|held| held.id() != scope.id())
        .any(|held| held.attr("data-field").is_some())
}

/// The first of a run of elements to carry `name`, which is how htmx resolves an inherited one.
fn nearest(near: &[ElementRef<'_>], name: &str) -> Option<String> {
    near.iter()
        .find_map(|element| element.attr(name))
        .map(str::to_owned)
}
