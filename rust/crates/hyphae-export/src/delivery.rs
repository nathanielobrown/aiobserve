//! Getting shaped spans to a backend and knowing what landed: the exporter and its ledger.
//!
//! At-least-once with stable ids. A delivery row in the store records the fingerprint and the
//! mapper version a session shipped under, so re-running ships only what moved and a shaping
//! change re-ships the corpus (`docs/otlp-export.md`). A failed run re-sends the session whole
//! rather than diffing what got through — an append-only backend never dedupes.
//!
//! What a session becomes on the way here is [`crate::otlp`]; this module never reads a store
//! row except the two it owns: the fingerprints it holds, and the ledger it writes.
//!
//! Ported from `src/hyphae/export/otlp_delivery.py`, which stays the authority.

use std::cell::Cell;
use std::collections::{BTreeMap, HashMap};
use std::io::Write as _;
use std::path::Path;
use std::time::{Duration, Instant};

use hyphae_store::source::{SourceError, StoreSource};
use hyphae_store::{Store, StoreError};
use opentelemetry_proto::tonic::collector::trace::v1::{
    ExportTraceServiceRequest, ExportTraceServiceResponse,
};
use opentelemetry_proto::tonic::resource::v1::Resource;
use opentelemetry_proto::tonic::trace::v1::{ResourceSpans, ScopeSpans, Span};
use prost::Message as _;

use crate::otlp::{self, MAPPER_VERSION, ShapeError, TextPolicy};

/// Spans per POST. The biggest canonical session is ~29K spans, so a backfill of it is ~15
/// requests. A field rather than a constant so tests can bind it down and cross a real batch
/// boundary on a recorded session.
pub const DEFAULT_BATCH_SPANS: usize = 2_000;

/// Spans per second, across a whole run. Prior art measured ~40% silent server-side loss with
/// no limiter and none at this rate (issue #6); it puts the full corpus at ~16 minutes, which
/// is a backfill's price for not losing two spans in five.
pub const DEFAULT_RATE: f64 = 300.0;

/// Per request. A backend that has not answered by then is down, not slow.
pub const DEFAULT_TIMEOUT: f64 = 30.0;

/// A 429 or a 5xx is the backend asking us to come back; anything else is our bug. Attempts
/// include the first, and the wait doubles between them unless `Retry-After` says otherwise.
pub const MAX_ATTEMPTS: u32 = 3;
pub const BACKOFF_SECONDS: f64 = 1.0;

/// What the environment holds for the base-case backend: any OTLP/HTTP endpoint.
pub const GENERIC: &str = "generic";
pub const ENDPOINT_ENV: &str = "OTLP_ENDPOINT";
pub const HEADERS_ENV: &str = "OTLP_HEADERS";

/// Lives in the trace store beside the fingerprints it compares against, created on first
/// export like the enrichment tables — table existence, no schema-version bump. Deliberately
/// outside `TABLES`: swept into the replace transaction it would be erased by every
/// re-extract, and every later run would ship the corpus again as duplicates.
const DELIVERY_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS otlp_delivery (
    session_id VARCHAR NOT NULL,
    backend VARCHAR NOT NULL,
    -- The `extract_state` fingerprint that was shipped, and the mapper that shaped it.
    -- Either one moving makes the session undelivered again.
    fingerprint VARCHAR NOT NULL,
    mapper_version VARCHAR NOT NULL,
    -- The local manifest: what a future `--verify` counts against the backend.
    spans_sent BIGINT NOT NULL,
    delivered_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, backend)
);
";

type Result<T> = std::result::Result<T, DeliveryError>;

/// Where spans go, and the name its delivery rows are recorded under.
///
/// `headers` carries the key. It is never logged and never interpolated into an error — the
/// crash paths are exactly where one gets published by accident.
#[derive(Debug, Clone)]
pub struct Backend {
    pub name: String,
    pub endpoint: String,
    pub headers: BTreeMap<String, String>,
}

impl Backend {
    /// A backend that sends no headers at all — the shape a local collector takes.
    pub fn bare(name: &str, endpoint: &str) -> Self {
        Backend {
            name: name.to_owned(),
            endpoint: endpoint.to_owned(),
            headers: BTreeMap::new(),
        }
    }
}

/// Everything that stops a run, none of it carrying a key or a transcript value.
#[derive(Debug, thiserror::Error)]
pub enum DeliveryError {
    /// The environment does not say where to ship. Raised before anything is read.
    #[error("{0}")]
    Configuration(String),
    /// A batch never landed: the backend refused it, or stopped answering.
    #[error(
        "{backend} answered {status} for session {session_id} batch {batch} after {attempts} \
         attempt(s). Nothing was recorded as delivered; the next run sends the session again."
    )]
    Refused {
        backend: String,
        status: u16,
        session_id: String,
        batch: usize,
        attempts: u32,
    },
    /// The backend took the request and kept only part of it — a mapper bug we need to see.
    #[error(
        "{backend} rejected {rejected} span(s) of session {session_id} batch {batch}: {reason}. \
         Nothing was recorded as delivered, and no flag skips it — fix the mapper and bump \
         MAPPER_VERSION."
    )]
    Rejected {
        backend: String,
        rejected: i64,
        session_id: String,
        batch: usize,
        reason: String,
    },
    /// Boxed: a bare `ShapeError` is 120 bytes, which would put every `Result` in this
    /// module over the size clippy refuses.
    #[error(transparent)]
    Shape(Box<ShapeError>),
    #[error(transparent)]
    Source(#[from] SourceError),
    #[error(transparent)]
    Store(#[from] StoreError),
    #[error(transparent)]
    DuckDb(#[from] duckdb::Error),
    /// The transport itself failed: no answer, a refused connection, a timeout. Never carries
    /// the request headers, so a key cannot ride out on it.
    #[error("{backend} could not be reached: {message}")]
    Transport { backend: String, message: String },
    #[error("the backend answered with a body this build cannot decode: {0}")]
    Decode(#[from] prost::DecodeError),
}

impl From<ShapeError> for DeliveryError {
    fn from(error: ShapeError) -> Self {
        DeliveryError::Shape(Box::new(error))
    }
}

/// A named backend: where it takes spans, and how its key travels.
///
/// Endpoints and header names verified against prior art
/// (`/Users/nob/repos/mac_settings/claude-otel/import_transcripts.py:114`).
#[derive(Debug, Clone, Copy)]
pub struct BackendSpec {
    pub endpoint: &'static str,
    /// The environment variable holding the key, and the header it goes in *bare* — Logfire
    /// refuses an `authorization: Bearer …`, and the failure is a 401 an hour into a backfill.
    pub key_env: &'static str,
    pub header: &'static str,
}

/// The registry `--backend` reads. Adding an entry is one edit.
pub const BACKENDS: &[(&str, BackendSpec)] = &[
    (
        "honeycomb",
        BackendSpec {
            endpoint: "https://api.honeycomb.io/v1/traces",
            key_env: "HONEYCOMB_API_KEY",
            header: "x-honeycomb-team",
        },
    ),
    (
        "logfire",
        BackendSpec {
            endpoint: "https://logfire-us.pydantic.dev/v1/traces",
            key_env: "LOGFIRE_API_KEY",
            header: "authorization",
        },
    ),
];

/// What `--backend` takes, discovered from the registry.
pub fn backend_names() -> Vec<&'static str> {
    std::iter::once(GENERIC)
        .chain(BACKENDS.iter().map(|(name, _)| *name))
        .collect()
}

/// Where `--backend <name>` ships, resolved from the environment.
///
/// Validated here rather than at the first POST, so a misconfigured run refuses before it
/// reads a session. `OTLP_ENDPOINT` overrides a named backend's endpoint — the way a run
/// reaches a collector standing in front of the real thing.
pub fn named_backend(name: &str, environ: &HashMap<String, String>) -> Result<Backend> {
    if name == GENERIC {
        return generic_backend(environ);
    }
    let spec = BACKENDS
        .iter()
        .find(|(known, _)| *known == name)
        .map(|(_, spec)| spec)
        .ok_or_else(|| DeliveryError::Configuration(format!("no backend named {name}")))?;
    let key = read(environ, spec.key_env);
    if key.is_empty() {
        return Err(DeliveryError::Configuration(format!(
            "{} is unset or empty. Put it in .env or the environment",
            spec.key_env
        )));
    }
    let endpoint = read(environ, ENDPOINT_ENV);
    Ok(Backend {
        name: name.to_owned(),
        endpoint: if endpoint.is_empty() {
            spec.endpoint.to_owned()
        } else {
            endpoint
        },
        headers: BTreeMap::from([(spec.header.to_owned(), key)]),
    })
}

/// The base-case backend: any OTLP/HTTP endpoint, with optional headers.
///
/// Validated here rather than at the first POST, so a misconfigured run refuses before it
/// reads a session. `OTLP_HEADERS` is `name=value` pairs separated by commas.
pub fn generic_backend(environ: &HashMap<String, String>) -> Result<Backend> {
    let endpoint = read(environ, ENDPOINT_ENV);
    if endpoint.is_empty() {
        return Err(DeliveryError::Configuration(format!(
            "{ENDPOINT_ENV} is unset or empty. Put it in .env or the environment"
        )));
    }
    Ok(Backend {
        name: GENERIC.to_owned(),
        endpoint,
        headers: headers(&read(environ, HEADERS_ENV))?,
    })
}

fn read(environ: &HashMap<String, String>, name: &str) -> String {
    environ
        .get(name)
        .map_or("", |value| value.trim())
        .to_owned()
}

fn headers(value: &str) -> Result<BTreeMap<String, String>> {
    let mut parsed = BTreeMap::new();
    for pair in value
        .split(',')
        .map(str::trim)
        .filter(|pair| !pair.is_empty())
    {
        let Some((name, held)) = pair.split_once('=') else {
            return Err(DeliveryError::Configuration(format!(
                "{HEADERS_ENV} takes comma-separated name=value pairs"
            )));
        };
        parsed.insert(name.to_owned(), held.to_owned());
    }
    Ok(parsed)
}

/// The time seam every wait goes through: the rate bucket's and the retry backoff's alike.
///
/// A test binds one that records what was asked for and never waits, which is what lets a
/// pacing leaf be exact and cost nothing.
pub trait Clock {
    /// Seconds from an arbitrary origin, moving forward only.
    fn monotonic(&self) -> f64;
    /// Wait this long. A test clock may record the request instead.
    fn sleep(&self, seconds: f64);
}

/// The real clock: `Instant` for the bucket, `thread::sleep` for the wait.
pub struct SystemClock {
    origin: Instant,
}

impl SystemClock {
    pub fn new() -> Self {
        SystemClock {
            origin: Instant::now(),
        }
    }
}

impl Default for SystemClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemClock {
    fn monotonic(&self) -> f64 {
        self.origin.elapsed().as_secs_f64()
    }

    fn sleep(&self, seconds: f64) {
        std::thread::sleep(Duration::from_secs_f64(seconds));
    }
}

/// A token bucket over spans, so a run cannot outrun what a backend will really keep.
struct Pacer<'a> {
    rate: f64,
    clock: &'a dyn Clock,
    ready: Cell<f64>,
}

impl<'a> Pacer<'a> {
    fn new(rate: f64, clock: &'a dyn Clock) -> Result<Self> {
        if rate <= 0.0 {
            return Err(DeliveryError::Configuration(format!(
                "a rate of {rate} spans/s would never send anything"
            )));
        }
        Ok(Pacer {
            rate,
            clock,
            ready: Cell::new(clock.monotonic()),
        })
    }

    /// Block until this many spans may leave, then charge them to the bucket.
    fn wait(&self, spans: usize) {
        let mut now = self.clock.monotonic();
        if now < self.ready.get() {
            self.clock.sleep(self.ready.get() - now);
            now = self.ready.get();
        }
        self.ready.set(now + spans as f64 / self.rate);
    }
}

/// How a run ships: what it says it is, how much text it carries, and how fast it goes.
///
/// Every field is the caller's choice; [`Shipping::new`] fills the measured defaults so the
/// numbers live in one place.
pub struct Shipping<'a> {
    /// `None` routes each session to a service named for its project directory.
    pub service_name: Option<String>,
    /// Transcript text stays home unless the caller opts it in.
    pub text: TextPolicy,
    pub batch_spans: usize,
    pub rate: f64,
    /// Per request, in seconds.
    pub timeout: f64,
    pub clock: &'a dyn Clock,
}

impl<'a> Shipping<'a> {
    /// The defaults an unbound run uses, over the clock the caller names.
    pub fn new(clock: &'a dyn Clock) -> Self {
        Shipping {
            service_name: None,
            text: otlp::METADATA_ONLY,
            batch_spans: DEFAULT_BATCH_SPANS,
            rate: DEFAULT_RATE,
            timeout: DEFAULT_TIMEOUT,
            clock,
        }
    }
}

/// Ships each session's spans to one backend and records what the backend confirmed.
///
/// The promise is at-least-once with stable ids: a session is sent whole or not at all, a
/// failure records nothing and re-sends next run, and a backend that ignores span identity
/// will hold duplicates. Nothing here diffs what already landed — that machinery was the
/// prior importer's largest bug source.
///
/// Takes an open store rather than a path because DuckDB admits one writer at a time: the
/// [`StoreSource`] reading beside it has to be holding the same one.
pub struct OtlpExporter<'a> {
    backend: Backend,
    store: &'a Store,
    shipping: Shipping<'a>,
    pacer: Pacer<'a>,
    client: reqwest::blocking::Client,
}

impl<'a> OtlpExporter<'a> {
    /// Creates the ledger table if the store has none, as the enrichment layer does.
    ///
    /// Python calls `check_shape` first; this build writes and reads a store already at
    /// `SCHEMA_VERSION` and leaves migration in Python, as [`hyphae_store`] does.
    pub fn new(backend: Backend, store: &'a Store, shipping: Shipping<'a>) -> Result<Self> {
        let pacer = Pacer::new(shipping.rate, shipping.clock)?;
        #[expect(
            clippy::disallowed_methods,
            reason = "the one HTTP client this workspace builds; every send crosses it"
        )]
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs_f64(shipping.timeout))
            .build()
            .map_err(|error| DeliveryError::Transport {
                backend: backend.name.clone(),
                message: error.to_string(),
            })?;
        store.connection().execute_batch(DELIVERY_SCHEMA)?;
        Ok(OtlpExporter {
            backend,
            store,
            shipping,
            pacer,
            client,
        })
    }

    /// What this backend holds, as far as delivery can tell.
    ///
    /// Rows recorded under an older mapper are left out, which is what makes a shaping change
    /// re-send the corpus: [`refresh`] sees them as sessions it never shipped.
    pub fn fingerprints(&self) -> Result<HashMap<String, String>> {
        let mut statement = self.store.connection().prepare(
            "SELECT session_id, fingerprint FROM otlp_delivery \
             WHERE backend = ? AND mapper_version = ?",
        )?;
        let rows = statement
            .query_map([&self.backend.name, &MAPPER_VERSION.to_owned()], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?;
        let mut held = HashMap::new();
        for row in rows {
            let (session_id, fingerprint) = row?;
            held.insert(session_id, fingerprint);
        }
        Ok(held)
    }

    /// Ship one session, and record it only once every batch came back confirmed.
    pub fn export(&self, trace: &hyphae_model::SessionTrace, fingerprint: &str) -> Result<()> {
        let spans = otlp::session_spans(trace, &self.shipping.text)?;
        let resource =
            otlp::session_resource(&trace.session, self.shipping.service_name.as_deref())?;
        let mut sent = 0usize;
        for (index, batch) in spans.chunks(self.shipping.batch_spans).enumerate() {
            self.post(&trace.session.id, index, batch, &resource)?;
            sent += batch.len();
        }
        self.store.connection().execute(
            "INSERT OR REPLACE INTO otlp_delivery VALUES (?, ?, ?, ?, ?, ?)",
            duckdb::params![
                trace.session.id,
                self.backend.name,
                fingerprint,
                MAPPER_VERSION,
                sent as i64,
                hyphae_model::clock::utcnow(),
            ],
        )?;
        Ok(())
    }

    /// Send one batch and read the answer, retrying only what the backend asked us to.
    fn post(
        &self,
        session_id: &str,
        index: usize,
        batch: &[Span],
        resource: &Resource,
    ) -> Result<()> {
        let payload = ExportTraceServiceRequest {
            resource_spans: vec![ResourceSpans {
                resource: Some(resource.clone()),
                scope_spans: vec![ScopeSpans {
                    spans: batch.to_vec(),
                    ..Default::default()
                }],
                ..Default::default()
            }],
        }
        .encode_to_vec();
        // Compressed here rather than by the client: the payload is protobuf, it is large,
        // and a fixed mtime keeps the same batch encoding to the same bytes.
        let body = gzip(&payload);
        for attempt in 1..=MAX_ATTEMPTS {
            // Every attempt is charged, including a retry: what a backend throttles on is
            // what actually arrives, not what we meant to send once.
            self.pacer.wait(batch.len());
            let mut request = self
                .client
                .post(&self.backend.endpoint)
                .header("Content-Type", "application/x-protobuf")
                .header("Content-Encoding", "gzip");
            for (name, value) in &self.backend.headers {
                request = request.header(name, value);
            }
            let response =
                request
                    .body(body.clone())
                    .send()
                    .map_err(|error| DeliveryError::Transport {
                        backend: self.backend.name.clone(),
                        // Only the kind of failure, never the request: the headers that
                        // authorized it are exactly what must not reach a log.
                        message: transport_message(&error),
                    })?;
            let status = response.status();
            let retry_after = response
                .headers()
                .get("retry-after")
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            if status.is_success() {
                let content = response.bytes().map_err(|error| DeliveryError::Transport {
                    backend: self.backend.name.clone(),
                    message: transport_message(&error),
                })?;
                return self.check_rejections(session_id, index, &content);
            }
            let retryable = status.as_u16() == 429 || status.as_u16() >= 500;
            if !retryable || attempt == MAX_ATTEMPTS {
                return Err(DeliveryError::Refused {
                    backend: self.backend.name.clone(),
                    status: status.as_u16(),
                    session_id: session_id.to_owned(),
                    batch: index,
                    attempts: attempt,
                });
            }
            self.shipping
                .clock
                .sleep(backoff(retry_after.as_deref(), attempt));
        }
        unreachable!("the loop returns on its last attempt")
    }

    /// Crash on a partial acceptance: the run is stuck here until the mapper changes.
    ///
    /// A deterministic rejection — an attribute cap, a timestamp the backend refuses — makes
    /// this session a poison pill: every run crashes at it and the sessions behind it stop
    /// shipping. That is the intended shape. It is a mapper bug, the fix is a mapper change,
    /// and the `MAPPER_VERSION` bump that comes with it re-sends everything.
    fn check_rejections(&self, session_id: &str, index: usize, content: &[u8]) -> Result<()> {
        let reply = ExportTraceServiceResponse::decode(content)?;
        let rejected = reply
            .partial_success
            .as_ref()
            .map_or(0, |partial| partial.rejected_spans);
        if rejected == 0 {
            return Ok(());
        }
        let reason = reply
            .partial_success
            .map(|partial| partial.error_message)
            .filter(|message| !message.is_empty())
            .unwrap_or_else(|| "no reason given".to_owned());
        Err(DeliveryError::Rejected {
            backend: self.backend.name.clone(),
            rejected,
            session_id: session_id.to_owned(),
            batch: index,
            reason,
        })
    }
}

/// What a transport failure was, without the request that caused it.
fn transport_message(error: &reqwest::Error) -> String {
    if error.is_timeout() {
        return "no answer before the timeout".to_owned();
    }
    if error.is_connect() {
        return "the connection was refused".to_owned();
    }
    "the request did not complete".to_owned()
}

/// The payload, gzipped at a fixed mtime so one batch always encodes to the same bytes.
fn gzip(payload: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::GzBuilder::new().write(Vec::new(), flate2::Compression::default());
    encoder.write_all(payload).expect("a vector takes writes");
    encoder.finish().expect("a vector takes writes")
}

/// How long to wait before the next attempt, honoring the backend's own answer.
fn backoff(retry_after: Option<&str>, attempt: u32) -> f64 {
    if let Some(named) = retry_after
        && let Ok(seconds) = named.trim().parse::<u32>()
    {
        return f64::from(seconds);
    }
    BACKOFF_SECONDS * 2f64.powi(attempt as i32 - 1)
}

/// What one pass changed, in session ids — enough for a caller to report or assert on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefreshResult {
    pub extracted: Vec<String>,
    pub skipped: Vec<String>,
}

/// Ship every session of `project` this backend has not already confirmed.
///
/// The `refresh` loop of `src/hyphae/pipeline.py`, over the one extractor and the one
/// exporter this tier has. Idempotent by construction: an unchanged session is skipped, and a
/// changed one is sent whole rather than patched.
pub fn refresh(
    project: &Path,
    source: &StoreSource<'_>,
    exporter: &OtlpExporter,
) -> Result<RefreshResult> {
    let held = exporter.fingerprints()?;
    let mut extracted = Vec::new();
    let mut skipped = Vec::new();
    for session in source.sessions(project)? {
        if held
            .get(&session.id)
            .is_some_and(|known| *known == session.fingerprint)
        {
            skipped.push(session.id);
            continue;
        }
        exporter.export(&source.extract(&session)?, &session.fingerprint)?;
        extracted.push(session.id);
    }
    Ok(RefreshResult { extracted, skipped })
}
