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
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::http::{HeaderValue, header};
use axum::response::Response;
use axum::{Router, middleware};

use crate::dev::{self, Reloads};
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

/// Which of the two viewers to build. No default: they are different things and the caller
/// knows which it wants (`src/hyphae/view/app.py:serve` says the same of its own flag).
pub enum Mode {
    /// What `hp view` serves, and what is shipped.
    Shipped,
    /// What `hp view --dev` serves: the reload stream on the channel given, the client script on
    /// every page, and the statics read from `statics` rather than from the build ([`crate::dev`]).
    ///
    /// The directory is an argument so a leaf can point a viewer at a temporary one rather than
    /// write into the checkout while the suite runs; [`serve`] passes [`crate::dev::STATIC`].
    Dev { reloads: Reloads, statics: PathBuf },
}

/// The viewer over the store at `db_path`, which must exist and hold this schema.
///
/// Fails at startup rather than on the first page: a typo in `--db` should not open a browser
/// onto an error page. [`Reader::open`] is what refuses.
///
/// # Errors
/// As [`build_app_with`] does.
pub fn build_app(db_path: &Path) -> Result<Router, ViewError> {
    build_app_with(db_path, Mode::Shipped)
}

/// The same, in the mode given.
///
/// Under [`Mode::Dev`] the app also serves the reload stream and puts its client on every page,
/// so a saved stylesheet reaches an open one. Nothing else differs: a shipped page is a dev page
/// minus that one script tag.
///
/// # Errors
/// When the store is missing, held by a writer, or at a schema version this build does not read.
pub fn build_app_with(db_path: &Path, mode: Mode) -> Result<Router, ViewError> {
    // Both startup checks, before a route exists to fail one of them: the store opens at the
    // schema version this build knows, and a named instant parses.
    format::check_clock();
    let viewer = Arc::new(Viewer {
        reader: Reader::open(db_path)?,
        dev: match &mode {
            Mode::Shipped => None,
            Mode::Dev { statics, .. } => Some(statics.clone()),
        },
    });
    // Merge order is a contract: `tools/gen_routes.py` reads the registration order into the table
    // in `docs/viewer.md`, which is the order each route module binds its own.
    let mut app = routes::pages::routes().merge(routes::fragments::routes());
    // Merged in above the policy layer rather than beside it, so the stream carries the same
    // header every other response does — the whole shape of this loop was chosen to leave [`CSP`]
    // untouched.
    if let Mode::Dev { reloads, .. } = &mode {
        app = app.merge(dev::reload_router(reloads));
    }
    Ok(app
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

/// Take `port` on the loopback, or refuse naming it and `remedy` — how to get another.
///
/// The server's own listener rather than a probe before one, which is what keeps the loop's own
/// move working: stopping a viewer and starting it again lands on an address still in `TIME_WAIT`,
/// where a plain bind is refused and the socket a server takes is not. Nothing here can be
/// stricter than the server, because this *is* the server's bind
/// (`tests/view/test_dev.py:test_a_port_the_server_could_bind_is_not_refused_by_the_probe`).
///
/// # Errors
/// When something else is listening on the port.
pub async fn claim(port: u16, remedy: &str) -> Result<tokio::net::TcpListener, String> {
    let address = SocketAddr::new(HOST.parse().expect("the loopback address parses"), port);
    tokio::net::TcpListener::bind(address)
        .await
        .map_err(|error| {
            format!(
                "port {port} is in use — something may already be serving at \
                 http://{HOST}:{port}/. {remedy} ({error})"
            )
        })
}

/// Run the viewer until interrupted, refusing a port something else already holds.
///
/// `dev` adds the reload loop ([`crate::dev`]); it has no default because the two viewers are
/// different things and the caller knows which it wants.
///
/// # Errors
/// When the store will not open, or the port is taken.
pub async fn serve(db_path: &Path, port: u16, dev: bool) -> Result<(), Box<dyn std::error::Error>> {
    // The watcher starts before the store is read, so `--dev` in a checkout whose static
    // directory has moved fails at startup rather than serving a loop that never fires.
    let watched = Path::new(dev::STATIC);
    let reloads = dev
        .then(|| dev::Reloads::watching(&[watched]))
        .transpose()?;
    let mode = match reloads.clone() {
        Some(reloads) => Mode::Dev {
            reloads,
            statics: watched.to_path_buf(),
        },
        None => Mode::Shipped,
    };
    let app = build_app_with(db_path, mode)?;
    // Bound after the store checks and before anything is printed, so a port something else
    // holds is a refusal rather than a URL that never answers.
    let listener = claim(port, "Pass --port to use another.").await?;
    println!("hp view: {} at http://{HOST}:{port}/", db_path.display());
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            // The reload stream has no last chunk, so a graceful wait would wait on it forever.
            // Ending the streams is what makes Ctrl-C with a browser listening an exit.
            if let Some(reloads) = reloads {
                reloads.stop();
            }
        })
        .await?;
    Ok(())
}
