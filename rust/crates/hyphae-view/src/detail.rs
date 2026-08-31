//! A value too fat for the pane it lands in: the head it shows, and the way to the rest.
//!
//! Ported from `src/hyphae/view/detail.py`. Nothing here decides how much to show — the head
//! arrives already cut, in SQL, at the `?detail=` the request asked for. What a [`Detail`] adds is
//! what the pane needs beside the head: how much was left behind, where to fetch it, and how to
//! mark it up. The enrichment lines are the same shape, because a pass writes past the width as
//! readily as a transcript does.

use hyphae_store::queries;

use crate::enrichment::{Enrichment, Level};
use crate::format as fmt;
use crate::highlight::Syntax;
use crate::nodes::{run_url, session_url, thread_url};

/// One fat column of a node as its pane shows it: the head, and the way to the rest.
///
/// A pane never decides how much of a value it shows — the head is cut in SQL at the `?detail=`
/// the request asked for, and `cut` is what that left for the link to offer.
pub struct Detail {
    pub name: String,
    pub head: String,
    pub cut: i64,
    pub url: String,
    /// What the head is marked up as, where the record says what the value is written in — the
    /// shell a `Bash` call ran, the file a `Read` returned.
    pub syntax: Option<Syntax>,
    /// And whether what is left is the markdown someone wrote it in. A person and a model write
    /// markdown; a program writes what it writes, so a tool's arguments and its output are printed
    /// as the store holds them. No value is both, and a syntax the record named wins.
    pub markdown: bool,
}

/// The two lines an enrichment pass wrote about a node, as the pane shows them.
///
/// Each is a [`Detail`] like any other fat value the pane previews: the head the query cut, and
/// the fetch that brings the rest of it back into the block the head stood in.
pub struct EnrichmentLines {
    pub description: Option<Detail>,
    /// None where the model saw no friction, which is most items, and where it wrote an empty
    /// line — the two are the same nothing to a pane.
    pub friction: Option<Detail>,
}

/// One fat column as a pane shows it, or `None` where the store holds nothing under it.
///
/// Nothing is a NULL or an empty string alike: a value with no characters in it has no preview to
/// show and nothing to offer the rest of, whichever of the two the column holds.
///
/// `head` arrives one character past `size`, which is how a value with more behind it is told from
/// one that ends where the pane does; `chars` is the whole length the link offers. `syntax` is what
/// the record says the value is written in, and the default is prose.
///
/// `markdown` says whether that prose is rendered as the markdown it was written in. It takes no
/// default: whether a value came from a person, a model or a program is a fact about the column,
/// and two callers of one route can read the same column either way.
pub fn detail_of(
    name: &str,
    head: Option<&str>,
    chars: Option<i64>,
    url: String,
    size: usize,
    syntax: Option<Syntax>,
    markdown: bool,
) -> Option<Detail> {
    let head = head.filter(|held| !held.is_empty())?;
    let cut = if head.chars().count() > size {
        chars.unwrap_or(0) - size as i64
    } else {
        0
    };
    Some(Detail {
        name: name.to_owned(),
        head: fmt::cut(head, size),
        cut,
        url,
        syntax,
        markdown,
    })
}

/// What a pass wrote about the selection, each line with the way to the rest of it.
///
/// The keys are the level's own: a turn's row is keyed by the thread the page is reading, a run's
/// and a session's by the session. `source` is that thread, which is the same one the descriptions
/// were read for.
pub fn enrichment_lines(
    about: Option<&Enrichment>,
    session_id: &str,
    source: &str,
) -> Option<EnrichmentLines> {
    let about = about?;
    let at = match about.level {
        Level::Turn => format!("{}/turn/{}", thread_url(session_id, source), about.item_id),
        Level::AgentRun => run_url(session_id, &about.item_id),
        Level::Session => session_url(&about.item_id),
    };
    Some(EnrichmentLines {
        description: detail_of(
            "description",
            Some(&about.description),
            Some(about.description_chars),
            format!("/fragment/description{at}"),
            queries::ENRICHMENT_CHARS,
            None,
            false,
        ),
        friction: detail_of(
            "friction",
            about.friction.as_deref(),
            about.friction_chars,
            format!("/fragment/friction{at}"),
            queries::ENRICHMENT_CHARS,
            None,
            false,
        ),
    })
}
