//! The viewer itself: what [`build_app`] assembles over a trace store, and what [`serve`] runs.
//!
//! Ported from `src/hyphae/view/app.py`. `build_app(db_path)` returns a router over one store. It
//! serves the statics, answers a locked or moved store with a page rather than a stack trace, and
//! merges each route module in turn ([`crate::routes`]), which is where the handlers live.
//!
//! Nothing the viewer serves writes: every request opens its own read-only connection
//! ([`crate::store`]), checks the store's schema version, renders, and closes. That is what lets
//! an extract run while a page is open, and what makes a locked store a 503 rather than a crash.

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use axum::http::{HeaderValue, header};
use axum::response::Response;
use axum::{Router, middleware};

use crate::format;
use crate::routes;
use crate::store::{Reader, ViewError};
use crate::viewer::Viewer;

/// Loopback only, and a port unlikely to be taken. Fixed rather than picked at startup so a link
/// pasted into a note opens the same page tomorrow.
pub const HOST: &str = "127.0.0.1";
pub const PORT: u16 = 8477;

/// Nothing loads from anywhere but this app: no CDN, no inline script, no remote font. The viewer
/// renders text a transcript wrote, so the escaping is the first defence and this is the second.
pub const CSP: &str = "default-src 'self'";

/// The viewer every route reads through. One per app, handed to each route as its state rather
/// than reached for through a global — a route body stays a plain function of what it needs.
pub(crate) type Shared = Arc<Viewer>;

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
    // Merge order is a contract: `tools/gen_routes.py` reads the registration order into the table
    // in `docs/viewer.md`, which is the order each route module binds its own.
    Ok(routes::pages::routes()
        .merge(routes::fragments::routes())
        .fallback(routes::not_found)
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
