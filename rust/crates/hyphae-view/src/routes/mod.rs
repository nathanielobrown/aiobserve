//! What each route answers with, and the checking every route shares.
//!
//! One module per group of routes, in the order `build_app` merges them, which is the order
//! `docs/viewer.md` prints. A handler here does three things and no more: read the knobs, call
//! the module that builds the markup, and turn a failure into the status it earns. Everything a
//! page decides lives behind that call.

use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Response};
use hyphae_store::schema::SCHEMA_VERSION;

use crate::browse::{Asked, PageError};
use crate::components::pages as error_pages;
use crate::store::ViewError;

pub mod fragments;
pub mod pages;

/// Every path the app mounts, in the order it mounts them.
///
/// A `Router` cannot be asked what it holds, so the two groups each declare their mounting as a
/// list and are folded out of it. This is that list — the one the app is really built from,
/// rather than a second copy a test would have to be kept in step with.
pub fn paths() -> Vec<String> {
    pages::mounted()
        .into_iter()
        .chain(fragments::mounted())
        .map(|(path, _)| path)
        .collect()
}

/// The knobs a node-page URL may carry, each absent when the reader took the default.
#[derive(serde::Deserialize)]
pub struct Knobs {
    nav: Option<String>,
    kin: Option<i64>,
    log: Option<i64>,
    detail: Option<i64>,
    page: Option<i64>,
}

impl Knobs {
    /// What the request asked for, checked — or the 400 the reader earned.
    fn asked(&self) -> Result<Asked, PageError> {
        Ok(Asked::checked(
            self.nav
                .as_deref()
                .unwrap_or(crate::nodes::Preset::Full.word()),
            self.kin.unwrap_or(crate::knobs::KIN.default),
            self.log.unwrap_or(crate::knobs::LOG.default),
            self.detail.unwrap_or(crate::knobs::DETAIL.default),
            self.page.unwrap_or(1),
        )?)
    }
}

/// One rendered page, or the status its failure earns.
///
/// Rendered whole before the response exists, deliberately: a stream would flush a 200 and the
/// markup above a failure before it knew, leaving a reader a page that looks finished.
fn served(rendered: Result<crate::components::Markup, PageError>) -> Response {
    match rendered {
        Ok(markup) => Html(markup.into_inner()).into_response(),
        Err(failure) => answered(failure),
    }
}

/// The two knobs a chunked page carries: where to resume, and how much to serve. Each page owns
/// its own defaults, because a record's cursor starts before the first row and a file's at zero.
#[derive(serde::Deserialize)]
pub struct Chunk {
    after: Option<i64>,
    size: Option<i64>,
}

/// What a path no route claims is answered with, in Starlette's own words: the Python viewer hands
/// its error page `HTTPException.detail`, and an unrouted request there carries this one.
const NOT_FOUND: &str = "Not Found";

/// The fallback: a path no route claims.
pub(crate) async fn not_found() -> Response {
    error(StatusCode::NOT_FOUND, NOT_FOUND)
}

/// What a failed page becomes: the status the failure earns, and a sentence saying why.
///
/// The three store failures are 503 and the reader's own mistakes are 400 or 404 — the split
/// `app.py` makes with one exception handler apiece.
fn answered(failure: PageError) -> Response {
    match failure {
        PageError::Store(ViewError::Store(hyphae_store::StoreError::Locked { .. })) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Another process holds the trace store — an extract or an enrich is running. \
             The page will load once it finishes.",
        ),
        PageError::Store(ViewError::Store(hyphae_store::StoreError::SchemaVersion {
            held,
            ..
        })) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            &format!(
                "The store now holds schema version {held}, and this build reads \
                 {SCHEMA_VERSION}. Restart the viewer."
            ),
        ),
        PageError::Bad(said) => error(StatusCode::BAD_REQUEST, &said.0),
        PageError::Missing(said) => error(StatusCode::NOT_FOUND, &said),
        // Everything else is ours rather than the reader's: a query that stopped answering a
        // column, a store that will not open. Loud, with the cause, rather than a blank 500.
        other => error(StatusCode::INTERNAL_SERVER_ERROR, &other.to_string()),
    }
}

/// The error page, which is what every handler above answers with.
fn error(status: StatusCode, message: &str) -> Response {
    // Never the dev page: `build_app` has no `--dev` yet, so nothing asks for the reload client.
    let page = error_pages::error_page(status.as_u16(), message, false);
    (status, Html(page.into_inner())).into_response()
}
