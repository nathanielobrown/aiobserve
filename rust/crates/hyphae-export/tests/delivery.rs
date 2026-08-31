//! Delivery: what reaches the backend, and what gets recorded once it lands.
//!
//! A real client against the in-process receiver, real protobuf, a real store — the design's
//! seam. Every leaf here is about the promise the exporter makes: at-least-once with stable
//! ids, a delivery row written only after the backend confirmed every batch. What happens
//! when the backend refuses is `tests/failures.rs`.
//!
//! The twin of `tests/export/test_otlp__delivery.py`.

use std::collections::BTreeMap;

use chrono::Utc;
use hyphae_export::delivery::{
    Backend, DEFAULT_BATCH_SPANS, DEFAULT_RATE, DeliveryError, OtlpExporter, Shipping,
};
use hyphae_export::otlp::{MAPPER_VERSION, ShapeError};
use hyphae_store::Store;
use hyphae_testsupport::otlp::{Value, emitted, read};
use hyphae_testsupport::receiver::{
    BOUND_BATCH, BOUND_RATE, FIRST, Receiver, SECOND, TestClock, batch_sizes, deliver,
    delivery_rows, sentinel_backend, shaped, shipping_store, trace_of,
};

#[test]
fn the_receiver_decodes_what_the_exporter_encoded() {
    // If both recorded sessions are exported...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let result = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect("the receiver takes both sessions");
    assert_eq!(result.extracted, [FIRST, SECOND]);
    // ...then the receiver decodes each session's whole span list, in session order, with
    // every field intact — ids, times and attributes included. Every other leaf that reads
    // decoded spans rests on this one.
    assert_eq!(receiver.spans(), shaped(&store, &[FIRST, SECOND]));
}

#[test]
fn the_resource_names_the_project_and_the_exporter() {
    // If the sessions of `/Users/nob/repos/mycelia` are exported...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    // ...then each request's resource routes to a service named for the directory, and
    // carries the version a re-shaping would bump...
    assert_eq!(
        read(&receiver.resources()[0].attributes),
        BTreeMap::from([
            ("service.name".to_owned(), Value::Str("mycelia".to_owned())),
            (
                "hyphae.exporter.version".to_owned(),
                Value::Str(MAPPER_VERSION.to_owned())
            ),
            (
                "hyphae.telemetry.source".to_owned(),
                Value::Str("store-export".to_owned())
            ),
        ])
    );
    // ...and an operator who wants another dataset overrides the service name.
    receiver.clear();
    store
        .connection()
        .execute("DELETE FROM otlp_delivery", [])
        .expect("the ledger clears");
    let mut named = Shipping::new(&clock);
    named.service_name = Some("mycelia-backfill".to_owned());
    deliver(&store, sentinel_backend(&receiver), named).expect("both ship again");
    assert_eq!(
        read(&receiver.resources()[0].attributes)["service.name"],
        Value::Str("mycelia-backfill".to_owned())
    );
}

#[test]
fn a_session_with_no_project_and_no_service_name_crashes() {
    // If a session carrying times but no `project_dir` reaches the exporter — planted, since
    // the recorded session with no `project_dir` records no times either, and the source
    // filter places neither...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let mut placeless = trace_of(&store, FIRST);
    placeless.session.project_dir = None;
    let exporter = OtlpExporter::new(sentinel_backend(&receiver), &store, Shipping::new(&clock))
        .expect("the exporter opens");
    // ...then it crashes before anything is sent, naming the session and the drift it is: no
    // place, rather than the no-clock drift the mapper refuses sessions for.
    let crashed = exporter
        .export(&placeless, "fingerprint")
        .expect_err("a placeless session cannot be routed");
    assert!(
        matches!(
            &crashed,
            DeliveryError::Shape(shape)
                if matches!(&**shape, ShapeError::PlacelessSession { session_id } if session_id == FIRST)
        ),
        "{crashed}"
    );
    assert!(receiver.bodies().is_empty());
}

#[test]
fn a_confirmed_session_records_one_delivery_row() {
    // If the store's sessions are exported...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    #[expect(
        clippy::disallowed_methods,
        reason = "the real clock is the oracle: `delivered_at` has to fall between two live reads"
    )]
    let before = Utc::now();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    let fingerprints = store.fingerprints().expect("the extract state reads back");
    // ...then each one has a row under this backend, carrying the fingerprint that was
    // shipped and the mapper version that shaped it — the two things a later run compares
    // against to decide whether to send again...
    for row in delivery_rows(&store) {
        assert_eq!(row.fingerprint, fingerprints[&row.session_id]);
        assert_eq!(row.mapper_version, MAPPER_VERSION);
        #[expect(
            clippy::disallowed_methods,
            reason = "the real clock is the oracle: `delivered_at` has to fall between two live reads"
        )]
        let after = Utc::now();
        assert!(before <= row.delivered_at && row.delivered_at <= after);
        assert_eq!(
            row.spans_sent as usize,
            emitted(&trace_of(&store, &row.session_id)).len()
        );
    }
    assert_eq!(sessions(&store), [FIRST, SECOND]);
}

#[test]
fn spans_sent_counts_what_the_receiver_took() {
    // The recorded manifest is what a future `--verify` compares against, so a count taken
    // from the sender's own intent would prove nothing about delivery.
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    let sent: i64 = delivery_rows(&store).iter().map(|row| row.spans_sent).sum();
    assert_eq!(sent as usize, receiver.spans().len());
}

#[test]
fn an_unchanged_session_is_not_sent_again() {
    // If everything was delivered once...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    let delivered_at: Vec<_> = delivery_rows(&store)
        .into_iter()
        .map(|row| row.delivered_at)
        .collect();
    receiver.clear();
    // ...then a second run skips every session — no request, and the ledger untouched. This
    // is what lets the command run on a schedule instead of duplicating the corpus.
    let result =
        deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("a no-op pass");
    assert_eq!(result.skipped, [FIRST, SECOND]);
    assert!(receiver.bodies().is_empty());
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| row.delivered_at)
            .collect::<Vec<_>>(),
        delivered_at
    );
}

#[test]
fn a_changed_fingerprint_re_sends_the_whole_session() {
    // If a delivered session is re-extracted under a new fingerprint...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    receiver.clear();
    store
        .connection()
        .execute(
            "UPDATE extract_state SET fingerprint = ? WHERE session_id = ?",
            duckdb::params!["moved", FIRST],
        )
        .expect("the fingerprint moves");
    // ...then it ships again in full — an append-only backend cannot be patched, so the unit
    // of correction is the whole session...
    let result = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect("the moved session ships");
    assert_eq!(result.extracted, [FIRST]);
    assert_eq!(receiver.spans(), shaped(&store, &[FIRST]));
    // ...and the row now records the fingerprint that was actually shipped.
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| (row.session_id, row.fingerprint))
            .collect::<Vec<_>>(),
        [
            (FIRST.to_owned(), "moved".to_owned()),
            (SECOND.to_owned(), "fixture".to_owned()),
        ]
    );
}

#[test]
fn a_stale_mapper_version_re_sends_everything() {
    // If everything was delivered, and the mapper is then changed — recorded here by
    // rewriting the version the rows carry, which is what a bump looks like to the reader...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    receiver.clear();
    store
        .connection()
        .execute("UPDATE otlp_delivery SET mapper_version = ?", ["0"])
        .expect("the recorded version moves");
    // ...then no session counts as delivered, every span goes again, and the rows come back
    // carrying the current version. This is the only recovery path from a mapper bug.
    let result = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect("the corpus ships again");
    assert_eq!(result.extracted, [FIRST, SECOND]);
    assert_eq!(
        receiver.spans().len(),
        shaped(&store, &[FIRST, SECOND]).len()
    );
    assert!(
        delivery_rows(&store)
            .iter()
            .all(|row| row.mapper_version == MAPPER_VERSION)
    );
}

#[test]
fn delivery_is_recorded_per_backend() {
    // If the store is delivered to one backend and then to a second...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    receiver.clear();
    let second = Backend {
        name: "second".to_owned(),
        ..sentinel_backend(&receiver)
    };
    deliver(&store, second, Shipping::new(&clock)).expect("both ship to the second backend");
    // ...then the second sees the full corpus, since nothing it holds was ever shipped...
    assert_eq!(
        receiver.spans().len(),
        shaped(&store, &[FIRST, SECOND]).len()
    );
    // ...and each backend keeps its own row per session.
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| (row.session_id, row.backend))
            .collect::<Vec<_>>(),
        [
            (FIRST.to_owned(), "generic".to_owned()),
            (FIRST.to_owned(), "second".to_owned()),
            (SECOND.to_owned(), "generic".to_owned()),
            (SECOND.to_owned(), "second".to_owned()),
        ]
    );
}

#[test]
fn the_ledger_survives_a_re_extract() {
    // If a delivered session is extracted again — the replace transaction that deletes every
    // row the session owns...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    let trace = trace_of(&store, FIRST);
    store
        .export(&trace, "re-extracted")
        .expect("the session re-extracts");
    // ...then its delivery row is still there. A table swept into the replace by reflex would
    // erase the ledger on every extract, and every later run would duplicate the corpus.
    assert_eq!(sessions(&store), [FIRST, SECOND]);
}

#[test]
fn the_ledger_is_created_without_a_schema_bump() {
    // If a store written by `extract` — which knows nothing of OTLP — is exported...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    assert_eq!(ledger_tables(&store), 0);
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    // ...then the table appears beside the enrichment tables, and the schema version is
    // untouched: the ledger is not part of the shape `extract` and the viewer agree on.
    assert_eq!(ledger_tables(&store), 1);
    let version: i64 = store
        .connection()
        .query_row("SELECT schema_version FROM meta", [], |row| row.get(0))
        .expect("the store records its schema version");
    assert_eq!(version, i64::from(hyphae_store::schema::SCHEMA_VERSION));
}

#[test]
fn a_multi_batch_session_ships_every_span_exactly_once() {
    // If both recorded sessions are shipped with the batch bound below their span counts...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let sessions = [shaped(&store, &[FIRST]), shaped(&store, &[SECOND])];
    assert!(sessions.iter().all(|spans| spans.len() > BOUND_BATCH));
    let mut bound = Shipping::new(&clock);
    bound.batch_spans = BOUND_BATCH;
    deliver(&store, sentinel_backend(&receiver), bound).expect("both ship");
    // ...then each session arrives as `ceil(n / size)` POSTs, none of them over the bound...
    assert_eq!(receiver.batch_sizes(), batch_sizes(&sessions));
    // ...and what the receiver decoded is the whole span list in order, with no span in two
    // batches. Prior art's issue #1 was a batching bug that lost 82.9% of its spans while
    // every request came back 200.
    let arrived = receiver.spans();
    assert_eq!(arrived, shaped(&store, &[FIRST, SECOND]));
    let unique: std::collections::HashSet<&Vec<u8>> =
        arrived.iter().map(|span| &span.span_id).collect();
    assert_eq!(unique.len(), arrived.len());
}

#[test]
fn the_shipping_defaults_are_the_measured_ones() {
    // Every other leaf in this tier binds its own batch size and rate, so without this pin
    // the tier passes at any defaults — including the ones prior art's issue #6 lost ~40% of
    // its spans at. 2,000 spans puts the biggest recorded session at ~15 POSTs, and 300/s is
    // the rate that landed 177 of 177 (`plans/otlp-export/design.md`).
    assert_eq!((DEFAULT_BATCH_SPANS, DEFAULT_RATE), (2_000, 300.0));
}

#[test]
fn the_bucket_paces_the_sends_through_the_injected_clock() {
    // If a run at a bound rate and batch size ships both sessions...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let sessions = [shaped(&store, &[FIRST]), shaped(&store, &[SECOND])];
    let mut bound = Shipping::new(&clock);
    bound.batch_spans = BOUND_BATCH;
    bound.rate = BOUND_RATE;
    deliver(&store, sentinel_backend(&receiver), bound).expect("both ship");
    // ...then each POST charges its own span count to the bucket, so the wait before a batch
    // is what the batch before it cost, and the first send is free. Issue #6 measured 40%
    // silent server-side loss without a limiter and 0% with one; a wall-clock version of this
    // assertion would be both slow and a flake, so the leaf asserts the delays *requested*.
    let sizes = batch_sizes(&sessions);
    let asked = clock.delays();
    assert_eq!(asked.len(), sizes.len() - 1);
    for (waited, count) in asked.iter().zip(&sizes) {
        assert!(
            (waited - *count as f64 / BOUND_RATE).abs() < 1e-9,
            "waited {waited}s for {count} spans"
        );
    }
}

#[test]
fn the_payload_travels_gzipped() {
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    // If a batch is shipped, the bytes on the wire carry gzip's magic number and the header
    // that lets a collector inflate them...
    assert!(
        receiver
            .sent_headers()
            .iter()
            .all(|headers| headers["content-encoding"] == "gzip")
    );
    assert!(
        receiver
            .raw_bodies()
            .iter()
            .all(|body| body[..2] == [0x1f, 0x8b])
    );
    // ...and they are smaller than the protobuf inside them. Every other leaf reads the
    // inflated payload, so a missing encode step would otherwise pass the whole tier.
    let wire: usize = receiver.raw_bodies().iter().map(Vec::len).sum();
    let inflated: usize = receiver.bodies().iter().map(Vec::len).sum();
    assert!(
        wire < inflated,
        "{wire} bytes on the wire, {inflated} inside"
    );
}

/// The sessions the ledger holds, in its own order.
fn sessions(store: &Store) -> Vec<String> {
    delivery_rows(store)
        .into_iter()
        .map(|row| row.session_id)
        .collect()
}

/// Whether the store carries the ledger table yet.
fn ledger_tables(store: &Store) -> i64 {
    store
        .connection()
        .query_row(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'otlp_delivery'",
            [],
            |row| row.get(0),
        )
        .expect("the catalog reads")
}
