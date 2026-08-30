//! The viewer itself: what [`build_app`] assembles over a trace store, and what [`serve`] runs.
//!
//! Ported from `src/hyphae/view/app.py`. `build_app(db_path)` returns a router over one store. It
//! serves the statics, answers a locked or moved store with a page rather than a stack trace, and
//! registers each route module in turn — stage 3a registers the node pages' first route.
//!
//! Nothing the viewer serves writes: every request opens its own read-only connection
//! ([`crate::store`]), checks the store's schema version, renders, and closes. That is what lets
//! an extract run while a page is open, and what makes a locked store a 503 rather than a crash.

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Router, middleware};
use hyphae_store::schema::SCHEMA_VERSION;

use crate::browse::{self, Asked, PageError};
use crate::components::pages;
use crate::format;
use crate::statics;
use crate::store::{Reader, ViewError};

/// Loopback only, and a port unlikely to be taken. Fixed rather than picked at startup so a link
/// pasted into a note opens the same page tomorrow.
pub const HOST: &str = "127.0.0.1";
pub const PORT: u16 = 8477;

/// Nothing loads from anywhere but this app: no CDN, no inline script, no remote font. The viewer
/// renders text a transcript wrote, so the escaping is the first defence and this is the second.
pub const CSP: &str = "default-src 'self'";

/// The store every route reads. One per app, handed to each route as its state rather than
/// reached for through a global — a route body stays a plain function of what it needs.
type Shared = Arc<Reader>;

/// The viewer over the store at `db_path`, which must exist and hold this schema.
///
/// Fails at startup rather than on the first page: a typo in `--db` should not open a browser
/// onto an error page. [`Reader::open`] is what refuses.
pub fn build_app(db_path: &Path) -> Result<Router, ViewError> {
    // Both startup checks, before a route exists to fail one of them: the store opens at the
    // schema version this build knows, and a named instant parses.
    format::check_clock();
    let reader = Arc::new(Reader::open(db_path)?);
    Ok(Router::new()
        .route("/static/{name}", get(static_file))
        .route("/session/{session_id}", get(session_page))
        .fallback(not_found)
        .layer(middleware::map_response(policy))
        .with_state(reader))
}

/// The header every response carries, whatever produced it.
async fn policy(mut response: Response) -> Response {
    response.headers_mut().insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static(CSP),
    );
    response
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

/// A session's own node page.
async fn session_page(
    State(reader): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
    Query(knobs): Query<Knobs>,
) -> Response {
    // Rendered whole before the response exists, deliberately: a stream would flush a 200 and the
    // markup above a failure before it knew, leaving a reader a page that looks finished.
    let rendered = knobs
        .asked()
        .and_then(|asked| browse::session_page(&reader, &session_id, &asked));
    match rendered {
        Ok(markup) => Html(markup.into_inner()).into_response(),
        Err(failure) => answered(failure),
    }
}

/// One embedded asset, or the 404 every unknown path gets.
async fn static_file(UrlPath(name): UrlPath<String>) -> Response {
    let Some((kind, bytes)) = statics::asset(&name) else {
        return error(StatusCode::NOT_FOUND, "No such file.");
    };
    ([(header::CONTENT_TYPE, kind)], bytes).into_response()
}

async fn not_found() -> Response {
    error(StatusCode::NOT_FOUND, "No such page.")
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
    let page = pages::error_page(status.as_u16(), message);
    (status, Html(page.into_inner())).into_response()
}

/// Run the viewer until interrupted.
pub async fn serve(db_path: &Path, port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let app = build_app(db_path)?;
    let address = SocketAddr::new(HOST.parse()?, port);
    // Bound before anything is printed, so a port something else holds is a refusal rather than
    // a URL that never answers.
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .map_err(|error| {
            format!(
                "port {port} is in use — something may already be serving at \
             http://{HOST}:{port}/. Pass --port to use another. ({error})"
            )
        })?;
    println!("hp view: {} at http://{HOST}:{port}/", db_path.display());
    axum::serve(listener, app).await?;
    Ok(())
}
