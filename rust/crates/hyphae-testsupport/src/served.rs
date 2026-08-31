//! The viewer over a cached store, driven without a socket.
//!
//! `Router` is a `tower::Service`, so a test hands it a request and gets a response back, the
//! way `TestClient` drives the Python app. [`Served`]'s four constructors mirror the four
//! fixtures `tests/view/conftest.py` offers: the shared corpus and the shared enriched store,
//! each read-only, and a planted copy of either for a leaf that needs text no recorded
//! fixture carries — a `<script>` never reached one of these transcripts, so it has to be
//! written in.

use std::path::{Path, PathBuf};

use axum::Router;
use axum::body::Body;
use axum::http::{Request, Response, StatusCode};
use hyphae_store::Store;
use hyphae_view::app::build_app;
use tempfile::TempDir;
use tower::ServiceExt as _;

use crate::cache;

/// A viewer, and the tempdir its store lives in when the store is a planted copy.
pub struct Served {
    /// Kept alive rather than read: dropping it deletes the file the viewer is reading.
    _scratch: Option<TempDir>,
    path: PathBuf,
    app: Router,
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

    fn over(path: PathBuf, scratch: Option<TempDir>) -> Self {
        let app = build_app(&path).expect("the viewer opens the store");
        Served {
            _scratch: scratch,
            path,
            app,
        }
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
