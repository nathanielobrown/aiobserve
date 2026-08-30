//! The pages that are not a node's. Stage 3a serves the one every failure is answered with.

use hypertext::prelude::*;

use crate::components::{Markup, layout};

/// What every failure the app catches is answered with — a status, a sentence, a way back.
///
/// The message never repeats what was asked for: a request is untrusted text like any other.
pub fn error_page(status: u16, message: &str) -> Markup {
    let main = rsx! {
        <section id="error">
            <h1 data-field="status">(status)</h1>
            <p data-field="message">(message)</p>
            <p><a href="/">"Back to the projects"</a></p>
        </section>
    }
    .memoize();
    layout::page(&format!("{status} — hyphae"), None, main, None)
}
