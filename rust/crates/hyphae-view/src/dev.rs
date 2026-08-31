//! The dev loop's server half: a file watcher behind a server-sent-event stream.
//!
//! Ported from `src/hyphae/view/dev.py`. [`crate::app::build_app_with`] under [`Mode::Dev`]
//! mounts [`reload_router`] and puts the client script on every page, so a saved stylesheet
//! reaches an open one.
//!
//! Server-sent events rather than a WebSocket because [`crate::app::CSP`] allows a same-origin
//! GET already, and because `EventSource` retries a dropped connection on its own. The
//! reconnect carries no message, so the client reloads on the reconnect itself.
//!
//! Two things the Python loop does that a compiled binary cannot, and what stands in their
//! place:
//!
//! - A `.py` save is uvicorn's own reloader to notice. Nothing here restarts a Rust binary, so
//!   the loop covers what a running server *can* serve differently: the stylesheets and the
//!   scripts under [`STATIC`]
//! - Those bytes are compiled in ([`crate::statics`]), so a re-fetch would hand the browser
//!   what it already had. Under `--dev` the static route reads them off disk instead, which is
//!   what makes the swap show the edit rather than announce it

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::Router;
use axum::response::Sse;
use axum::response::sse::Event as SseEvent;
use notify::{EventKind, RecursiveMode, Watcher as _};
use tokio::sync::broadcast;

/// Where the client listens. Under `/dev/` so that one prefix names everything `--dev` adds.
pub const RELOAD_URL: &str = "/dev/reload";

/// What a stylesheet is called, which is the whole of the classification below.
pub const STYLESHEET: &str = "css";

/// What a running viewer can serve differently without being restarted: the two static files a
/// browser fetches for itself. Markup is not among them — a page is Rust now
/// ([`crate::components`]), and a component edit is a rebuild rather than a message on the wire.
pub const RENDERED: &[&str] = &["css", "js"];

/// The directory the loop watches when its caller names none: the one copy of the static files
/// in the repo, which is also what `build.rs` compiles in.
///
/// A checkout path fixed at compile time, because this whole module is a checkout's tool: an
/// installed binary has no repository to save a stylesheet into.
pub const STATIC: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../src/hyphae/view/static"
);

/// Directories a save under is never a save the viewer renders from — watchfiles' own noise
/// filter, which `Rendered` defers to, written out because `notify` reports everything.
const NOISE: &[&str] = &[
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "node_modules",
    "site-packages",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    "target",
];

/// What the browser is being asked to do with what just changed on disk.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    /// Only stylesheets changed: re-fetch them in place, and the page keeps its scroll, its open
    /// sections, and whatever else a reload would cost.
    Css,
    /// Something the server renders changed: ask for the page again.
    Page,
}

impl Event {
    /// The word that goes on the wire, which is what the client script switches on.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Event::Css => "css",
            Event::Page => "page",
        }
    }
}

/// A change set the watcher would never have yielded — the assumption this module rests on.
#[derive(Debug, thiserror::Error)]
#[error("the watcher yielded an empty change set, which it does not do")]
pub struct EmptyChangeSet;

/// What one debounced change set asks the browser to do.
///
/// CSS only when *every* path in the set is a stylesheet: the client script saved alongside one
/// is a page event, or the edit that needs a load is the one the fast path swallows.
///
/// # Errors
/// [`EmptyChangeSet`] when nothing changed, which the watcher does not report — so it is a bug
/// to crash on rather than an event to classify.
pub fn event_for(changes: &[(EventKind, PathBuf)]) -> Result<Event, EmptyChangeSet> {
    if changes.is_empty() {
        return Err(EmptyChangeSet);
    }
    Ok(
        if changes
            .iter()
            .all(|(_, path)| suffix(path).as_deref() == Some(STYLESHEET))
        {
            Event::Css
        } else {
            Event::Page
        },
    )
}

/// Whether a path the watcher reported is one the viewer renders from.
///
/// The narrowing is not tidiness: a platform watcher reports the containing *directory* beside a
/// saved file, and a directory has no suffix, so an unfiltered stylesheet save reads as a page
/// event. The slow leaf in `tests/dev.rs` records that shape off the real watcher.
#[must_use]
pub fn rendered(path: &Path) -> bool {
    let quiet = path
        .components()
        .any(|part| NOISE.contains(&part.as_os_str().to_string_lossy().as_ref()));
    !quiet && suffix(path).is_some_and(|held| RENDERED.contains(&held.as_str()))
}

/// A path's extension, lowercased the way both viewers compare one.
fn suffix(path: &Path) -> Option<String> {
    Some(path.extension()?.to_string_lossy().to_lowercase())
}

/// What the reload stream carries: an event for the browser, or the end of the line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Message {
    Reload(Event),
    /// The server is going away. An SSE response has no last chunk, so a graceful exit that
    /// waits on every in-flight response would wait on this one forever — the stream ends
    /// itself instead of being waited out (`crate::app::serve`).
    Stop,
}

/// The channel between the watcher and every open reload stream — the loop's one seam.
///
/// Cloneable and cheap: the router holds one, `serve` holds one to stop the streams with, and a
/// test holds one to publish into. A [`Reloads::detached`] handle has no watcher behind it,
/// which is how a leaf drives the stream without touching a disk.
#[derive(Clone)]
pub struct Reloads {
    messages: broadcast::Sender<Message>,
    /// Kept alive rather than read: dropping the watcher stops the watching.
    _watcher: Option<Arc<notify::RecommendedWatcher>>,
}

/// How many messages a stream may fall behind by before it misses one. A reader that is this far
/// behind is one a single reload would have caught up anyway.
const BACKLOG: usize = 16;

impl Reloads {
    /// A channel with nothing watching: what a test publishes into, and what a router is built
    /// over when the watcher is not the subject.
    #[must_use]
    pub fn detached() -> Self {
        Reloads {
            messages: broadcast::channel(BACKLOG).0,
            _watcher: None,
        }
    }

    /// A channel fed by a watcher over `paths`, recursively.
    ///
    /// # Errors
    /// When the platform's watcher cannot be started or a path cannot be watched — a `--dev`
    /// startup failure rather than a loop that never fires.
    pub fn watching(paths: &[&Path]) -> notify::Result<Self> {
        let (messages, _) = broadcast::channel(BACKLOG);
        let published = messages.clone();
        let mut watcher =
            notify::recommended_watcher(move |answer: notify::Result<notify::Event>| {
                // A watcher error is the platform's, not a change: nothing to tell the browser.
                let Ok(event) = answer else { return };
                let changes: Vec<(EventKind, PathBuf)> = event
                    .paths
                    .iter()
                    .filter(|path| rendered(path))
                    .map(|path| (event.kind, path.clone()))
                    .collect();
                // Everything in the set was noise, which is not the empty set `event_for` refuses:
                // the watcher did report something, and none of it was ours.
                if changes.is_empty() {
                    return;
                }
                let classified = event_for(&changes).expect("a non-empty change set classifies");
                // No receiver is the open case, not a failure: nobody has the page open.
                let _ = published.send(Message::Reload(classified));
            })?;
        for path in paths {
            watcher.watch(path, RecursiveMode::Recursive)?;
        }
        Ok(Reloads {
            messages,
            _watcher: Some(Arc::new(watcher)),
        })
    }

    /// Put one event on the wire, as a save under a watched path would.
    pub fn publish(&self, event: Event) {
        let _ = self.messages.send(Message::Reload(event));
    }

    /// End every open stream, so a graceful exit has nothing left to wait for.
    pub fn stop(&self) {
        let _ = self.messages.send(Message::Stop);
    }
}

/// The reload stream, reading `reloads`.
///
/// A router of its own rather than a route on the viewer's, so that the shipped viewer declares
/// nothing under `/dev/` — `tests/bounds_payload.rs` reads the declared list as the whole of
/// what the viewer serves.
pub fn reload_router<S: Clone + Send + Sync + 'static>(reloads: &Reloads) -> Router<S> {
    let messages = reloads.messages.clone();
    Router::new().route(
        RELOAD_URL,
        axum::routing::get(move || reload_stream(messages.subscribe())),
    )
}

/// One message per change set, until the reader hangs up or the server stops.
async fn reload_stream(
    mut messages: broadcast::Receiver<Message>,
) -> Sse<impl futures_core::Stream<Item = Result<SseEvent, std::convert::Infallible>>> {
    let events = async_stream::stream! {
        loop {
            match messages.recv().await {
                Ok(Message::Reload(event)) => yield Ok(SseEvent::default().data(event.as_str())),
                // The server is going, or the last sender went with it.
                Ok(Message::Stop) | Err(broadcast::error::RecvError::Closed) => break,
                // A reader that fell behind gets the next event; the one it missed said the
                // same thing.
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
            }
        }
    };
    Sse::new(events)
}
