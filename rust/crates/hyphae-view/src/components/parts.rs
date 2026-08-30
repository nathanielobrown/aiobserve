//! The small pieces a page is built out of, each printed the one way the viewer prints it.

use hypertext::prelude::*;

use crate::components::Markup;
use crate::detail::{Detail, EnrichmentLines};
use crate::enrichment::Enrichment;
use crate::highlight::{self, Syntax};
use crate::knobs::HIGHLIGHT_CHARS;
use crate::labels::label;
use crate::{cuts, format as fmt, render};

/// The mark saying what a thing is: what kind of node a row, a crumb or a heading names.
///
/// `aria-hidden` and no `title`: the mark stands for a word the markup around it already
/// carries, so a reader who cannot see it loses nothing, and a `title` here would be the same
/// word 3,217 times in one page. Written with no space of its own: every caller puts one after
/// it, and a byte here is 3,217 bytes of page (`view/bounds.py`).
pub fn mark(character: &str) -> Markup {
    rsx! { <span class="icon" aria-hidden="true">(character)</span> }.memoize()
}

/// The mark on a title a model helped write, and the space after it.
///
/// Bare wherever a title repeats — a NavTree row, a crumb, a log line — because what a reader
/// wants beside a mark is on the pane, once. The space is inside: a row with no mark owes none.
pub fn glyph(enriched: bool) -> Option<Markup> {
    enriched.then(|| rsx! { <span class=(GLYPH_CLASS)>(GLYPH)</span>" " }.memoize())
}

/// What an enrichment pass wrote, wherever a page marks it as the model's words.
pub const GLYPH: &str = "✨";
pub const GLYPH_CLASS: &str = "glyph";

/// One half of a NavTree row's cost: the number, and the step standing for its share.
///
/// Written once because a row with agent runs under it draws two of them, its own spend and its
/// subtree's, and each takes the step its own share earns. This is the markup the ceiling in
/// `view/bounds.py` is measured over.
pub fn badge(step: &str, field: &str, value: f64) -> Markup {
    let class = format!("badge {step}");
    rsx! { <span class=(class) data-field=(field)>(fmt::money(Some(value)))</span> }.memoize()
}

/// The mark a cost carries when our price table priced none of some calls under it.
///
/// A total missing calls is not what was spent, and the page has to say so. Outside the labelled
/// span either way — a `data-field` carries the number the store holds and nothing else. No
/// calls and no count are the same mark: a window the store summed nothing over priced nothing
/// wrong.
pub fn unpriced(calls: i64) -> Option<Markup> {
    if calls == 0 {
        return None;
    }
    let said = format!(
        "{} call(s) at a model our price table lacks",
        fmt::count(Some(calls))
    );
    Some(rsx! { <sup title=(said)>"*"</sup> }.memoize())
}

/// What a cut list left out, in the one wording every list on a row uses.
pub fn more(cut: i64) -> Option<Markup> {
    if cut == 0 {
        return None;
    }
    Some(rsx! { " and "(fmt::count(Some(cut)))" more" }.memoize())
}

/// One labelled fact of a header.
///
/// The value goes through [`cuts::head`], which is the pane's half of the one-extra-character
/// protocol, and prints the viewer's dash where the store left NULL.
pub fn fact(name: &str, value: Option<&str>) -> Markup {
    pair(name, render::text(&cuts::head(value)))
}

/// One labelled fact whose value the caller composed, for the ones no formatter makes.
///
/// A list and the count of what its query cut, today. The `<dl>` shape is [`fact`]'s, so one
/// place decides what a labelled fact looks like whichever of the two wrote it.
pub fn labelled(name: &str, value: Markup) -> Markup {
    pair(name, value)
}

/// The `<dt>`/`<dd>` pair both mounts write, with the space a reader needs between them.
///
/// Nothing stands between two elements otherwise, so without the `" "` a reader whose stylesheet
/// never arrived meets `Cost$1.48`.
fn pair(name: &str, value: Markup) -> Markup {
    rsx! {
        <div>
            <dt>(label(name))</dt>" "
            <dd data-field=(name)>(value)</dd>
        </div>
    }
    .memoize()
}

/// One value in the syntax it was written in — a tool's arguments, a record, a query file.
///
/// A value past the ceiling prints as stored and says so: past a point that is a page nobody can
/// read rather than a page that reads better.
pub fn code(value: &str, syntax: Syntax, field: &str) -> Markup {
    let shown = highlight::lit(Some(value), syntax);
    let classes = shown
        .syntax
        .map(|syntax| format!("code {syntax}"))
        .unwrap_or_else(|| "code".to_owned());
    rsx! {
        @if shown.over > 0 {
            <p class="plain" data-plain=(field)>
                "Printed as stored: "
                <span data-field="over">(fmt::count(Some(shown.over)))</span>
                " characters is past the "
                (fmt::count(Some(HIGHLIGHT_CHARS as i64)))
                " this viewer marks up."
            </p>
        }
        <pre data-field=(field) class=(classes)>(hypertext::Raw::dangerously_create(shown.html.as_str()))</pre>
    }
    .memoize()
}

/// One name a session's row counts, and how often it counted it.
///
/// Built at the route from whichever column the query counted — runs for an agent type, turns for
/// a kind of work — so the component prints a count without knowing what was counted.
pub struct Count {
    pub name: String,
    pub count: i64,
}

/// One two-line cell: the value a reader scans a column for, and the texture under it.
///
/// Both halves are labelled, so a test reads either without matching prose, and the unit word sits
/// outside the labelled span — what a reader sees is a number and a word, what a `data-field`
/// carries is the value the store holds.
///
/// A mark hangs off whichever line owns what it qualifies, which is why there are two slots: the
/// session list stacks output tokens under a cost and marks the cost, the projects landing stacks a
/// cost under a count and marks the cost again.
pub struct Stacked<'a> {
    pub field: &'a str,
    pub primary: &'a str,
    pub secondary_field: &'a str,
    pub secondary: &'a str,
    pub unit: Option<&'a str>,
    pub primary_mark: Option<Markup>,
    pub secondary_mark: Option<Markup>,
}

pub fn stacked(cell: Stacked<'_>) -> Markup {
    rsx! {
        <span data-field=(cell.field) class="primary">(cell.primary)</span>
        (cell.primary_mark)
        <span class="secondary">
            <span data-field=(cell.secondary_field)>(cell.secondary)</span>
            (cell.secondary_mark)
            // The space before the unit is written here: nothing stands between two elements
            // otherwise, and this one is the difference between `0 errors` and `0errors`.
            @if let Some(unit) = cell.unit { " "(unit) }
        </span>
    }
    .memoize()
}

/// A counted list of names — the agent types a session spawned, the kinds of work it did.
///
/// Every integer goes through [`fmt::count`], like every other one a page prints, and every name
/// through [`cuts::item`], which marks the ones the query stopped. `mark_cuts` is how a caller opts
/// out, for a list whose vocabulary is closed: a taxonomy value is cut at a width its own words
/// cannot reach, so a mark on one would say a name went on when nothing was left behind.
pub fn counted(entries: &[Count], mark_cuts: bool) -> Markup {
    rsx! {
        @for (at, entry) in entries.iter().enumerate() {
            @if at > 0 { ", " }
            @if mark_cuts { (cuts::item(&entry.name)) } @else { (&entry.name) }
            (format!(" ×{}", fmt::count(Some(entry.count))))
        }
    }
    .memoize()
}

/// What an enrichment pass said an item was and how it went.
///
/// The vocabularies are closed (`enrich/taxonomy.py`); `stale` says the row was written under a
/// prompt or taxonomy version this build has moved past, which is a reason to re-run a pass and not
/// a reason to distrust the words.
pub fn tags(category: &str, outcome: &str, stale: bool) -> Markup {
    // A space between pills. Their margins hold the boxes apart for a reader who sees the row;
    // this is what holds the words apart for one who hears it.
    rsx! {
        <span class="tag" data-field="category">(category)</span>
        " "<span class="tag" data-field="outcome">(outcome)</span>
        @if stale { " "<span class="tag stale" data-field="stale">"stale"</span> }
    }
    .memoize()
}

/// What a pass said about the item whose page this is, beside the header counting what it did.
///
/// Model-written from a private transcript, so it is text like any other the viewer renders.
pub fn summary(about: &Enrichment, lines: &EnrichmentLines) -> Markup {
    rsx! {
        <section class="enrichment" data-enrichment=(&about.item_id)>
            <p>
                <span class=(GLYPH_CLASS) data-field="enriched" title=(about.provenance())>
                    (GLYPH)
                </span>" "
                (enrichment_line(lines.description.as_ref()))
            </p>
            <p class="tags">(tags(&about.category, &about.outcome, about.stale()))</p>
            @if lines.friction.is_some() {
                <p class="friction">(enrichment_line(lines.friction.as_ref()))</p>
            }
        </section>
    }
    .memoize()
}

/// One line a pass wrote as the pane shows it: the head, and the fetch that brings the rest.
///
/// A pass answers in paragraphs, so nearly every line it writes about a run runs past the width —
/// the mark alone would say there is more and offer no way to it.
///
/// The link sits outside the labelled span and inside the block it swaps, which is what makes
/// `closest .enrichment-line` land: the glyph and the provenance hanging off it stay put.
pub fn enrichment_line(item: Option<&Detail>) -> Option<Markup> {
    let item = item?;
    Some(
        rsx! {
            <span class="enrichment-line" data-enrichment-line=(&item.name)>
                <span data-field=(&item.name)>(&item.head)</span>
                @if item.cut > 0 { " "(whole(item, "closest .enrichment-line", "more")) }
            </span>
        }
        .memoize(),
    )
}

/// The link that fetches the rest of a cut value into the block its head stood in.
fn whole(item: &Detail, target: &str, classes: &str) -> Markup {
    rsx! {
        <a
            class=[(!classes.is_empty()).then_some(classes)]
            data-whole=(&item.name)
            href=(&item.url)
            hx-get=(&item.url)
            hx-target=(target)
            hx-swap="outerHTML"
        >"+"<span data-field="cut">(fmt::count(Some(item.cut)))</span>" more character(s)"</a>
    }
    .memoize()
}

/// One value as the markdown a session wrote it in.
///
/// Rendered rather than printed because that is what it is: a prompt, a model's answer and the
/// brief a run was given are written in markdown by whoever typed them. [`crate::render`] owns the
/// escaping, and nothing here may hand a value on that did not come through it.
///
/// Written once for the two mounts that show one value: the head a pane previews, and the whole of
/// it the fetch swaps into that same block.
pub fn prose(field: &str, value: Option<&str>) -> Markup {
    rsx! { <div class="prose" data-field=(field)>(render::markdown(value))</div> }.memoize()
}

/// One of a node's own values as the pane shows it: the head, and the way to the rest.
///
/// The link fetches the whole value and replaces this block, which is the one place a fat column
/// crosses the wire whole. A head whose row said what it was written in is marked up in that
/// syntax; the rest is prose.
///
/// Prose is walled as the quotation it is — someone's words inside our page — and a payload is not:
/// a border on every value would say "this is a value" rather than "somebody wrote this". The same
/// flag decides both, so a value cannot render as markdown and read as a payload.
pub fn detail(item: &Detail) -> Markup {
    let shown = if let Some(syntax) = item.syntax {
        code(&item.head, syntax, &item.name)
    } else if item.markdown {
        prose(&item.name, Some(&item.head))
    } else {
        rsx! { <pre data-field=(&item.name)>(&item.head)</pre> }.memoize()
    };
    rsx! {
        <section
            class=(if item.markdown { "detail quoted" } else { "detail" })
            data-detail=(&item.name)
        >
            <h3>(label(&item.name))</h3>
            (shown)
            @if item.cut > 0 { <p class="more">(whole(item, "closest .detail", ""))</p> }
        </section>
    }
    .memoize()
}
