//! The viewer over a cached store, driven without a socket.
//!
//! `Router` is a `tower::Service`, so a test hands it a request and gets a response back, the
//! way `TestClient` drives the Python app. [`Served`]'s four constructors mirror the four
//! fixtures `tests/view/conftest.py` offers: the shared corpus and the shared enriched store,
//! each read-only, and a planted copy of either for a leaf that needs text no recorded
//! fixture carries — a `<script>` never reached one of these transcripts, so it has to be
//! written in.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::Body;
use axum::http::{Request, Response, StatusCode};
use hyphae_store::Store;
use hyphae_view::app::{Mode, build_app_with};
use hyphae_view::dev::Reloads;
use tempfile::TempDir;
use tokio_stream::StreamExt as _;
use tower::ServiceExt as _;

use crate::cache;

/// A viewer, and the tempdir its store lives in when the store is a planted copy.
pub struct Served {
    /// Kept alive rather than read: dropping it deletes the file the viewer is reading.
    _scratch: Option<TempDir>,
    path: PathBuf,
    app: Router,
    /// The reload channel under `--dev`, which a leaf publishes into the way a save does.
    reloads: Option<Reloads>,
}

impl Served {
    /// The shared corpus store, served read-only.
    pub fn corpus() -> Self {
        Self::over(cache::corpus_store(), None)
    }

    /// The shared enriched store, served read-only.
    pub fn enriched() -> Self {
        Self::over(cache::enriched_store(), None)
    }

    /// A copy of the corpus store with `plant` written over it.
    pub fn planted(plant: impl Fn(&Store)) -> Self {
        Self::copy_of(cache::corpus_store(), plant)
    }

    /// A copy of the enriched store with `plant` written over it.
    pub fn enriched_planted(plant: impl Fn(&Store)) -> Self {
        Self::copy_of(cache::enriched_store(), plant)
    }

    /// The shared enriched store, served as `hp view --dev` serves it: the checkout's own static
    /// directory, and a channel nothing is watching for.
    ///
    /// The described store rather than the bare one because six routes fetch what an enrichment
    /// pass wrote, and a store no pass has touched answers those with a 404 — the reason
    /// `tests/view/test_dev.py`'s own dev fixture reads it.
    pub fn enriched_dev() -> Self {
        Self::built(
            cache::enriched_store(),
            None,
            Some((Reloads::detached(), PathBuf::from(hyphae_view::dev::STATIC))),
        )
    }

    /// The corpus store served in dev mode over a loop the caller built: its own channel, and its
    /// own static directory. For the two leaves where the loop itself is the subject.
    pub fn corpus_dev(reloads: Reloads, statics: PathBuf) -> Self {
        Self::built(cache::corpus_store(), None, Some((reloads, statics)))
    }

    fn over(path: PathBuf, scratch: Option<TempDir>) -> Self {
        Self::built(path, scratch, None)
    }

    fn built(path: PathBuf, scratch: Option<TempDir>, loop_: Option<(Reloads, PathBuf)>) -> Self {
        let reloads = loop_.as_ref().map(|(reloads, _)| reloads.clone());
        let mode = match loop_ {
            Some((reloads, statics)) => Mode::Dev { reloads, statics },
            None => Mode::Shipped,
        };
        let app = build_app_with(&path, mode).expect("the viewer opens the store");
        Served {
            _scratch: scratch,
            path,
            app,
            reloads,
        }
    }

    /// The channel this viewer's reload stream reads, for a leaf that publishes into it.
    ///
    /// # Panics
    /// On a viewer built without `--dev`, which has no channel.
    pub fn reloads(&self) -> &Reloads {
        self.reloads.as_ref().expect("a dev viewer has a channel")
    }

    fn copy_of(cached: PathBuf, plant: impl Fn(&Store)) -> Self {
        let (scratch, path) = cache::writable_copy(&cached);
        {
            let store = Store::create(&path).expect("the copy opens for writing");
            plant(&store);
        }
        Self::over(path, Some(scratch))
    }

    /// One GET, answered. The router is cloned per call because `oneshot` consumes the service.
    pub async fn get(&self, path: &str) -> Response<Body> {
        self.sent(path, &[]).await
    }

    /// One GET carrying headers, for the leaves that ask what htmx's own request changes.
    pub async fn sent(&self, path: &str, headers: &[(&str, &str)]) -> Response<Body> {
        let mut request = Request::builder().uri(path);
        for (name, value) in headers {
            request = request.header(*name, *value);
        }
        self.app
            .clone()
            .oneshot(request.body(Body::empty()).expect("a GET with no body"))
            .await
            .expect("the router answers")
    }

    /// One GET's status and body text, sent with headers.
    pub async fn page_sent(&self, path: &str, headers: &[(&str, &str)]) -> (StatusCode, String) {
        Self::read(self.sent(path, headers).await).await
    }

    /// One GET's status and body text, which is what most leaves assert over.
    pub async fn page(&self, path: &str) -> (StatusCode, String) {
        Self::read(self.get(path).await).await
    }

    async fn read(response: Response<Body>) -> (StatusCode, String) {
        let status = response.status();
        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("the whole body");
        (
            status,
            String::from_utf8(bytes.to_vec()).expect("a page is UTF-8"),
        )
    }

    /// One GET whose response never ends, taken to its first `count` data frames.
    ///
    /// The reader `Served::page` cannot be: it waits for the whole body, and a reload stream has
    /// no last chunk. `poke` runs over and over while the stream is open, because nothing says
    /// when a watcher is listening and a single save can land before anything is.
    ///
    /// # Panics
    /// When the stream does not reach `count` frames inside [`PATIENCE`], or when it ends first.
    pub async fn frames(&self, path: &str, count: usize, poke: impl Fn()) -> Vec<String> {
        let response = self.get(path).await;
        assert_eq!(response.status(), StatusCode::OK, "{path}");
        taken(response, count, poke).await
    }

    /// The served store's own path, for a test that reads it back.
    pub fn db(&self) -> PathBuf {
        self.path.clone()
    }
}

/// The session with the most turns on its main thread, which is the one whose children log has
/// pages to walk. Read from the store rather than guessed: the fixture corpus is discovered, so
/// a session added to it must not quietly turn a paging test into a one-page one.
pub fn busiest_session(db: &Path) -> (String, i64) {
    let store = Store::open_read_only(db).expect("the store opens read only");
    let rows = store
        .fetch(
            "SELECT session_id, count(*) AS turns FROM turns WHERE source = 'main' \
             GROUP BY session_id ORDER BY turns DESC, session_id LIMIT 1",
            &[],
        )
        .expect("the store answers");
    let row = rows.first().expect("the corpus has a session with turns");
    (
        row.str("session_id").expect("a session id").to_owned(),
        row.i64("turns").expect("a turn count"),
    )
}

/// Every session in the store in the list's default order: newest first, empties last.
///
/// A session the store gave no start sorts to the bottom whichever way the list is ordered: "the
/// store does not know" is not a date, and a row that carries none is not the newest thing that
/// happened.
pub fn listed_sessions(db: &Path) -> Vec<String> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    store
        .fetch(
            "SELECT session_id FROM session_rollups \
             ORDER BY started_at DESC NULLS LAST, session_id DESC",
            &[],
        )
        .expect("the store answers")
        .iter()
        .map(|row| row.str("session_id").expect("a session id").to_owned())
        .collect()
}

/// Every session id the fixture corpus put in the store, sorted.
pub fn session_ids(db: &Path) -> Vec<String> {
    let store = Store::open_read_only(db).expect("the store opens read only");
    store
        .fetch("SELECT id FROM sessions ORDER BY id", &[])
        .expect("the store answers")
        .iter()
        .map(|row| row.str("id").expect("a session id").to_owned())
        .collect()
}

/// How long a streamed leaf waits for the frames it asked for. Generous because a platform
/// watcher's latency is the platform's, not ours.
pub const PATIENCE: Duration = Duration::from_secs(15);

/// The first `count` data frames of a streaming response, poking while it waits.
///
/// # Panics
/// When the stream ends first, or nothing arrives inside [`PATIENCE`].
pub async fn taken(response: Response<Body>, count: usize, poke: impl Fn()) -> Vec<String> {
    let mut data = response.into_body().into_data_stream();
    let mut held: Vec<String> = Vec::new();
    let deadline = Instant::now() + PATIENCE;
    while held.len() < count {
        tokio::select! {
            chunk = data.next() => {
                let bytes = chunk.expect("the stream ended before it said anything")
                    .expect("the stream yields bytes");
                held.push(String::from_utf8(bytes.to_vec()).expect("a frame is UTF-8"));
            }
            () = tokio::time::sleep(Duration::from_millis(120)) => {
                assert!(Instant::now() < deadline, "nothing arrived in {PATIENCE:?}");
                poke();
            }
        }
    }
    held
}
