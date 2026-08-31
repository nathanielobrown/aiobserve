//! An OTLP/HTTP endpoint in this process, and the store the export tiers ship from.
//!
//! The twin of `tests/export/conftest.py`. The design's chosen seam: a real client, real
//! protobuf, a real store, and a server that decodes what arrived instead of a mock that
//! records what was asked. It can be scripted to answer the way a backend under load does — a
//! partial rejection, a 429, a 500 — which is the only way to test the failure paths the
//! prior importer's data loss came from.
//!
//! Python guards the tier by monkeypatching `httpx.Client.send`; nothing here can reach into
//! a `reqwest` client the same way, so [`deliver`] refuses a backend that does not point at
//! this machine's loopback. Anything a leaf ships through it therefore stays local.

use std::collections::{BTreeMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::post;
use chrono::{DateTime, Utc};
use hyphae_export::delivery::{
    Backend, DeliveryError, OtlpExporter, RefreshResult, Shipping, refresh,
};
use hyphae_extract::SessionSource;
use hyphae_model::SessionTrace;
use hyphae_store::Store;
use hyphae_store::source::StoreSource;
use opentelemetry_proto::tonic::collector::trace::v1::{
    ExportTracePartialSuccess, ExportTraceServiceRequest, ExportTraceServiceResponse,
};
use opentelemetry_proto::tonic::resource::v1::Resource;
use opentelemetry_proto::tonic::trace::v1::Span;
use prost::Message as _;

use crate::landmarks::{MYCELIA, SERVER_TOOLS, SPINE};

/// The two sessions the export tiers ship, in the order `sessions()` lists them — the id
/// order the poison-pill leaf reads as "later in the run".
pub const FIRST: &str = SERVER_TOOLS;
pub const SECOND: &str = SPINE;

/// Planted onto the backend's headers, so a leak of the key has a distinct string to find.
pub const KEY_SENTINEL: &str = "planted-key-not-a-real-credential";

/// Names the backend an opt-in live send ships to. Unset, every leaf here stays on this machine.
pub const LIVE_ENV: &str = "HYPHAE_LIVE_OTLP";

/// The only hosts a test may ship to. Anything else is a real backend: billed, and handed a
/// transcript.
const LOOPBACK: &[&str] = &["127.0.0.1", "localhost", "::1", "[::1]"];

/// What the receiver answers with. Build one to script a backend's bad day.
#[derive(Debug, Clone)]
pub struct Reply {
    pub status: u16,
    /// Spans the backend refuses. Nonzero is the deterministic-rejection shape: HTTP 200 with
    /// a body saying part of the batch never landed.
    pub rejected_spans: i64,
    pub error_message: String,
    /// Seconds, sent as `Retry-After` when set.
    pub retry_after: Option<u32>,
}

impl Default for Reply {
    fn default() -> Self {
        Reply {
            status: 200,
            rejected_spans: 0,
            error_message: String::new(),
            retry_after: None,
        }
    }
}

impl Reply {
    /// An answer that only carries a status — the shape most scripted replies take.
    pub fn status(status: u16) -> Self {
        Reply {
            status,
            ..Reply::default()
        }
    }
}

/// What the endpoint took and what it will answer, behind the lock the handler holds.
#[derive(Debug, Default)]
struct Inner {
    /// Inflated, so a leaf sweeping the payload for a leaked string reads plaintext.
    bodies: Vec<Vec<u8>>,
    /// Exactly what arrived, before the receiver decoded the transfer encoding.
    raw_bodies: Vec<Vec<u8>>,
    /// One entry per request, so a leaf can prove the key it asserts absent elsewhere was in
    /// fact sent — otherwise that assertion passes on an exporter that sends no headers.
    sent_headers: Vec<BTreeMap<String, String>>,
    /// Answered in order, one per request, before `reply` takes over for the rest.
    replies: VecDeque<Reply>,
    reply: Reply,
}

/// A running OTLP endpoint: its URL, every request body it took, and what it answers.
///
/// The server runs on its own runtime and stops when this value drops, so a leaf holds it for
/// as long as it ships.
pub struct Receiver {
    pub url: String,
    inner: Arc<Mutex<Inner>>,
    /// Dropped last: shutting the runtime down stops the server task.
    _runtime: tokio::runtime::Runtime,
}

impl Receiver {
    /// An OTLP endpoint on an ephemeral port of this machine's loopback.
    ///
    /// # Panics
    /// When the port cannot be bound or the runtime cannot be built.
    pub fn start() -> Self {
        // Bound synchronously, so the port is known before the first leaf reads `url`.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("an ephemeral port");
        listener
            .set_nonblocking(true)
            .expect("the listener goes non-blocking");
        let address = listener.local_addr().expect("the bound address");
        let inner = Arc::new(Mutex::new(Inner::default()));
        let app = Router::new()
            .route("/v1/traces", post(take))
            .with_state(Arc::clone(&inner));
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("a runtime for the receiver");
        runtime.spawn(async move {
            let listener =
                tokio::net::TcpListener::from_std(listener).expect("the listener moves to tokio");
            axum::serve(listener, app)
                .await
                .expect("the receiver serves");
        });
        Receiver {
            url: format!("http://{address}/v1/traces"),
            inner,
            _runtime: runtime,
        }
    }

    /// Script the next answers, one per request, before the standing one takes over.
    pub fn replies(&self, replies: impl IntoIterator<Item = Reply>) {
        self.lock().replies = replies.into_iter().collect();
    }

    /// What the receiver answers with once the scripted replies run out.
    pub fn answer(&self, reply: Reply) {
        self.lock().reply = reply;
    }

    /// Forget what arrived, so a second pass asserts on its own requests alone.
    pub fn clear(&self) {
        let mut inner = self.lock();
        inner.bodies.clear();
        inner.raw_bodies.clear();
        inner.sent_headers.clear();
    }

    /// Each request decoded — the assertion surface, rather than the raw bytes.
    ///
    /// # Panics
    /// When a body is not a decodable export request, which is a mapper or transport bug.
    pub fn requests(&self) -> Vec<ExportTraceServiceRequest> {
        self.bodies()
            .iter()
            .map(|body| {
                ExportTraceServiceRequest::decode(body.as_slice()).expect("an export request")
            })
            .collect()
    }

    /// Every span the receiver decoded, across every request, in arrival order.
    pub fn spans(&self) -> Vec<Span> {
        self.requests()
            .into_iter()
            .flat_map(|request| request.resource_spans)
            .flat_map(|resource| resource.scope_spans)
            .flat_map(|scope| scope.spans)
            .collect()
    }

    /// How many spans each request carried, in POST order.
    pub fn batch_sizes(&self) -> Vec<usize> {
        self.requests()
            .into_iter()
            .map(|request| {
                request
                    .resource_spans
                    .iter()
                    .flat_map(|resource| &resource.scope_spans)
                    .map(|scope| scope.spans.len())
                    .sum()
            })
            .collect()
    }

    /// The resource each request routed under.
    ///
    /// # Panics
    /// When a request carries resource spans with no resource, which nothing emits.
    pub fn resources(&self) -> Vec<Resource> {
        self.requests()
            .into_iter()
            .flat_map(|request| request.resource_spans)
            .map(|resource| resource.resource.expect("a request names its resource"))
            .collect()
    }

    /// The inflated payloads, in arrival order.
    pub fn bodies(&self) -> Vec<Vec<u8>> {
        self.lock().bodies.clone()
    }

    /// Exactly what arrived on the wire, gzip and all.
    pub fn raw_bodies(&self) -> Vec<Vec<u8>> {
        self.lock().raw_bodies.clone()
    }

    /// The headers each request was sent under, lowercased.
    pub fn sent_headers(&self) -> Vec<BTreeMap<String, String>> {
        self.lock().sent_headers.clone()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().expect("the receiver's lock is held")
    }
}

/// Answers one POST the way an OTLP collector does, per the receiver's current reply.
async fn take(State(inner): State<Arc<Mutex<Inner>>>, headers: HeaderMap, body: Bytes) -> Response {
    let reply = {
        let mut inner = inner.lock().expect("the receiver's lock is held");
        inner.raw_bodies.push(body.to_vec());
        let gzipped = headers
            .get("content-encoding")
            .is_some_and(|encoding| encoding == "gzip");
        inner.bodies.push(if gzipped {
            inflate(&body)
        } else {
            body.to_vec()
        });
        inner.sent_headers.push(
            headers
                .iter()
                .map(|(name, value)| {
                    (
                        name.as_str().to_owned(),
                        String::from_utf8_lossy(value.as_bytes()).into_owned(),
                    )
                })
                .collect(),
        );
        inner
            .replies
            .pop_front()
            .unwrap_or_else(|| inner.reply.clone())
    };
    let body = ExportTraceServiceResponse {
        partial_success: Some(ExportTracePartialSuccess {
            rejected_spans: reply.rejected_spans,
            error_message: reply.error_message.clone(),
        }),
    }
    .encode_to_vec();
    let mut response = Response::builder()
        .status(StatusCode::from_u16(reply.status).expect("a scripted status"))
        .header("Content-Type", "application/x-protobuf");
    if let Some(seconds) = reply.retry_after {
        response = response.header("Retry-After", seconds.to_string());
    }
    response
        .body(body.into())
        .expect("the scripted answer builds")
}

fn inflate(body: &[u8]) -> Vec<u8> {
    use std::io::Read as _;
    let mut inflated = Vec::new();
    flate2::read::GzDecoder::new(body)
        .read_to_end(&mut inflated)
        .expect("the payload said it was gzip");
    inflated
}

/// The injected time seam: every wait the exporter asked for, and a clock that honors it.
///
/// Leaves assert the delays *requested*, never wall-clock elapsed time, so a pacing leaf is
/// exact and costs nothing.
#[derive(Debug, Default)]
pub struct TestClock {
    delays: std::cell::RefCell<Vec<f64>>,
    now: std::cell::Cell<f64>,
}

impl TestClock {
    /// Every wait asked of this clock, in order.
    pub fn delays(&self) -> Vec<f64> {
        self.delays.borrow().clone()
    }
}

impl hyphae_export::delivery::Clock for TestClock {
    fn monotonic(&self) -> f64 {
        self.now.get()
    }

    fn sleep(&self, seconds: f64) {
        self.delays.borrow_mut().push(seconds);
        self.now.set(self.now.get() + seconds);
    }
}

/// What a clock that refuses to wait panics with, so a leaf can recognize its own crash.
pub const REFUSED_WAIT: &str = "the exporter asked to wait";

/// A clock that crashes instead of waiting.
///
/// A waiter reaching `thread::sleep` directly misses the seam entirely — and sleeps for real
/// in CI, where every leaf in the tier would still pass.
#[derive(Debug, Default)]
pub struct RefusingClock;

impl hyphae_export::delivery::Clock for RefusingClock {
    fn monotonic(&self) -> f64 {
        0.0
    }

    fn sleep(&self, seconds: f64) {
        panic!("{REFUSED_WAIT} {seconds}s");
    }
}

/// Run `shipping` and give back the panic message instead of unwinding.
///
/// [`RefusingClock`] crashes rather than returning an error, so a leaf that expects the crash
/// catches it here. The panic hook is silenced for the duration: the backtrace it would print
/// is the leaf's expected outcome.
///
/// # Panics
/// When the closure does not panic at all.
pub fn refused(shipping: impl FnOnce()) -> String {
    let hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    let crashed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(shipping));
    std::panic::set_hook(hook);
    let payload = crashed.expect_err("the clock refused to wait");
    payload
        .downcast_ref::<String>()
        .cloned()
        .or_else(|| {
            payload
                .downcast_ref::<&str>()
                .map(|held| (*held).to_owned())
        })
        .expect("a panic carrying a message")
}

/// Small enough that both recorded sessions overflow it several times, so a batch boundary a
/// leaf asserts on is a real overflow of recorded spans rather than a planted one.
pub const BOUND_BATCH: usize = 7;

/// Spans per second for the leaves that assert pacing. Any rate works against [`TestClock`],
/// which only moves when the exporter asks it to.
pub const BOUND_RATE: f64 = 100.0;

/// This leaf's own writable copy of the two-session store: every delivery leaf writes rows.
///
/// The twin of `tests/export/conftest.py:store_path`. The `TempDir` comes back with the store
/// — dropping it deletes the file the store is on.
///
/// # Panics
/// When the cached store cannot be copied or opened.
pub fn shipping_store() -> (tempfile::TempDir, Store) {
    let (scratch, path) = crate::cache::writable_copy(&crate::cache::delivered_store());
    let store = Store::open_for_write(&path).expect("the copy opens for writing");
    (scratch, store)
}

/// Every span each session ships, in the order a run sends them.
///
/// # Panics
/// When the store holds no such session, or one the mapper refuses.
pub fn shaped(store: &Store, sessions: &[&str]) -> Vec<Span> {
    sessions
        .iter()
        .flat_map(|session| crate::otlp::emitted(&trace_of(store, session)))
        .collect()
}

/// How [`BOUND_BATCH`] partitions each session's spans, in POST order.
pub fn batch_sizes(sessions: &[Vec<Span>]) -> Vec<usize> {
    sessions
        .iter()
        .flat_map(|spans| spans.chunks(BOUND_BATCH).map(<[Span]>::len))
        .collect()
}

/// The backend most leaves ship through: generic, at this receiver, carrying the sentinel key.
pub fn sentinel_backend(receiver: &Receiver) -> Backend {
    Backend {
        name: "generic".to_owned(),
        endpoint: receiver.url.clone(),
        headers: BTreeMap::from([("x-key".to_owned(), KEY_SENTINEL.to_owned())]),
    }
}

/// One `export-otlp` pass over the store, exactly as the CLI runs it.
///
/// Time is injected through `shipping`: every wait goes into the clock it names rather than
/// into the leaf's wall clock.
///
/// # Panics
/// When the backend points anywhere but this machine's loopback. A run that forgets the
/// receiver's URL bills a real backend and hands it a transcript, so it fails here instead.
pub fn deliver(
    store: &Store,
    backend: Backend,
    shipping: Shipping<'_>,
) -> Result<RefreshResult, DeliveryError> {
    let host = host_of(&backend.endpoint);
    assert!(
        LOOPBACK.contains(&host.as_str()),
        "a test tried to reach {host}. Only {LOOPBACK:?} are allowed; a real backend needs \
         {LIVE_ENV}."
    );
    let exporter = OtlpExporter::new(backend, store, shipping)?;
    refresh(Path::new(MYCELIA), &StoreSource::new(store), &exporter)
}

/// The host an endpoint names, without pulling a URL parser into the test tier.
fn host_of(endpoint: &str) -> String {
    let after_scheme = endpoint
        .split_once("://")
        .map_or(endpoint, |(_, rest)| rest);
    let authority = after_scheme.split('/').next().unwrap_or_default();
    let host = authority
        .rsplit_once(':')
        .map_or(authority, |(host, port)| {
            if port.chars().all(|character| character.is_ascii_digit()) {
                host
            } else {
                authority
            }
        });
    host.to_owned()
}

/// One row of the delivery ledger. `delivered_at` is last, so a leaf that cannot compare a
/// clock slices it off.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryRow {
    pub session_id: String,
    pub backend: String,
    pub fingerprint: String,
    pub mapper_version: String,
    pub spans_sent: i64,
    pub delivered_at: DateTime<Utc>,
}

/// The whole ledger, in a stable order.
///
/// # Panics
/// When the store holds no ledger table, which means nothing has ever been exported to it.
pub fn delivery_rows(store: &Store) -> Vec<DeliveryRow> {
    let mut statement = store
        .connection()
        .prepare(
            "SELECT session_id, backend, fingerprint, mapper_version, spans_sent, delivered_at \
             FROM otlp_delivery ORDER BY session_id, backend",
        )
        .expect("the ledger is readable");
    let rows = statement
        .query_map([], |row| {
            Ok(DeliveryRow {
                session_id: row.get(0)?,
                backend: row.get(1)?,
                fingerprint: row.get(2)?,
                mapper_version: row.get(3)?,
                spans_sent: row.get(4)?,
                delivered_at: row.get(5)?,
            })
        })
        .expect("the ledger reads back");
    rows.map(|row| row.expect("a ledger row")).collect()
}

/// One session read back out of the store, the way the exporter is handed it.
///
/// # Panics
/// When the store holds no such session.
pub fn trace_of(store: &Store, session_id: &str) -> SessionTrace {
    StoreSource::new(store)
        .extract(&SessionSource {
            id: session_id.to_owned(),
            files: Vec::new(),
            fingerprint: "x".to_owned(),
        })
        .expect("the store holds the session")
}
