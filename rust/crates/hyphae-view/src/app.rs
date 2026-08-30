//! The viewer itself: what [`build_app`] assembles over a trace store, and what [`serve`] runs.
//!
//! Ported from `src/hyphae/view/app.py`. `build_app(db_path)` returns a router over one store. It
//! serves the statics, answers a locked or moved store with a page rather than a stack trace, and
//! registers each route module in turn — stage 3a registers the node pages' first route.
//!
//! Nothing the viewer serves writes: every request opens its own read-only connection
//! ([`crate::store`]), checks the store's schema version, renders, and closes. That is what lets
//! an extract run while a page is open, and what makes a locked store a 503 rather than a crash.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Router, middleware};
use hyphae_store::schema::SCHEMA_VERSION;

use crate::browse::{Asked, PageError};
use crate::components::pages;
use crate::store::{Reader, ViewError};
use crate::viewer::Viewer;
use crate::{format, listing, node_pages, statics};

/// Loopback only, and a port unlikely to be taken. Fixed rather than picked at startup so a link
/// pasted into a note opens the same page tomorrow.
pub const HOST: &str = "127.0.0.1";
pub const PORT: u16 = 8477;

/// Nothing loads from anywhere but this app: no CDN, no inline script, no remote font. The viewer
/// renders text a transcript wrote, so the escaping is the first defence and this is the second.
pub const CSP: &str = "default-src 'self'";

/// The viewer every route reads through. One per app, handed to each route as its state rather
/// than reached for through a global — a route body stays a plain function of what it needs.
type Shared = Arc<Viewer>;

/// The viewer over the store at `db_path`, which must exist and hold this schema.
///
/// Fails at startup rather than on the first page: a typo in `--db` should not open a browser
/// onto an error page. [`Reader::open`] is what refuses.
pub fn build_app(db_path: &Path) -> Result<Router, ViewError> {
    // Both startup checks, before a route exists to fail one of them: the store opens at the
    // schema version this build knows, and a named instant parses.
    format::check_clock();
    let viewer = Arc::new(Viewer {
        reader: Reader::open(db_path)?,
        dev: false,
    });
    // Route order is a contract: `tools/gen_routes.py` reads the registration order into the table
    // in `docs/viewer.md`, which is the order each route module binds its own.
    Ok(Router::new()
        .route("/static/{name}", get(static_file))
        .route("/", get(projects_page))
        .route(listing::LIST_URL, get(session_list))
        .route("/session/{session_id}", get(session_page))
        .route(
            "/session/{session_id}/thread/{source}/turn/{turn_id}",
            get(turn_page),
        )
        .route("/session/{session_id}/run/{run_id}", get(run_page))
        .route(
            "/session/{session_id}/thread/{source}/call/{api_call_id}",
            get(call_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/tool/{tool_call_id}",
            get(tool_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/compaction/{compaction_id}",
            get(compaction_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/unattributed",
            get(unattributed_page),
        )
        .route("/session/{session_id}/unattached", get(unattached_page))
        .fallback(not_found)
        .layer(middleware::map_response(policy))
        .with_state(viewer))
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

/// Every project the store holds sessions for.
async fn projects_page(State(viewer): State<Shared>) -> Response {
    served(listing::projects_page(&viewer))
}

/// One page of sessions, under the filter, sort and size the URL carries.
///
/// Every query-string key arrives rather than the four the page declares: what the list takes is a
/// closed set, and a key outside it is a 400 rather than a filter that silently did nothing.
async fn session_list(
    State(viewer): State<Shared>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    served(listing::session_list(&viewer, &params))
}

/// A session's own node page.
async fn session_page(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::session_page(&viewer, &session_id, &asked)),
    )
}

/// One turn, on the thread the URL names.
async fn turn_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs.asked().and_then(|asked| {
            node_pages::turn_page(&viewer, &session_id, &source, &turn_id, &asked)
        }),
    )
}

/// One agent run, which is its own thread.
async fn run_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::run_page(&viewer, &session_id, &run_id, &asked)),
    )
}

/// One api call.
async fn call_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, api_call_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::call_page(&viewer, &session_id, &source, &api_call_id, &asked)
    }))
}

/// One tool call.
async fn tool_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, tool_call_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::tool_page(&viewer, &session_id, &source, &tool_call_id, &asked)
    }))
}

/// One compaction of a thread.
async fn compaction_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, compaction_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::compaction_page(&viewer, &session_id, &source, &compaction_id, &asked)
    }))
}

/// One thread's api calls that answer no turn.
async fn unattributed_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source)): UrlPath<(String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::unattributed_page(&viewer, &session_id, &source, &asked)),
    )
}

/// The session's agent runs no spawning call resolved.
async fn unattached_page(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::unattached_page(&viewer, &session_id, &asked)),
    )
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
    // Never the dev page: `build_app` has no `--dev` yet, so nothing asks for the reload client.
    let page = pages::error_page(status.as_u16(), message, false);
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
