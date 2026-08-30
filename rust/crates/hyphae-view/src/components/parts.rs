//! The small pieces a page is built out of, each printed the one way the viewer prints it.

use hypertext::prelude::*;

use crate::components::Markup;
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
