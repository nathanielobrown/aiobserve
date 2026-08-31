//! What a backend's refusal costs: nothing recorded, and the session sent again.
//!
//! The failure half of `tests/export/test_otlp__delivery.py`. Prior art lost 82.9% of one
//! import's spans while every request came back 200, so these leaves are the ones that hold
//! the delivery promise: a row means the backend confirmed every batch, and nothing else
//! writes one.

use std::collections::HashMap;

use hyphae_export::delivery::{
    Backend, DeliveryError, OtlpExporter, Shipping, named_backend, refresh,
};
use hyphae_store::source::StoreSource;
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_testsupport::receiver::{
    BOUND_BATCH, BOUND_RATE, FIRST, KEY_SENTINEL, LIVE_ENV, Receiver, RefusingClock, Reply, SECOND,
    TestClock, deliver, delivery_rows, refused, sentinel_backend, shaped, shipping_store,
};

#[test]
fn a_server_error_crashes_and_the_next_run_re_sends() {
    // If the backend answers 500 to everything...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    receiver.answer(Reply::status(500));
    let crashed = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect_err("a 500 stops the run");
    // ...then the run crashes with nothing recorded — the failure prior art's issue #2 hid by
    // recording "attempted" — after backing off between attempts...
    assert!(crashed.to_string().contains(FIRST), "{crashed}");
    assert_eq!(delivery_rows(&store), []);
    assert!(!clock.delays().is_empty());
    // ...and when the backend recovers, the next run ships both sessions whole.
    receiver.answer(Reply::default());
    receiver.clear();
    let result = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect("the recovered backend takes both");
    assert_eq!(result.extracted, [FIRST, SECOND]);
    assert_eq!(receiver.spans(), shaped(&store, &[FIRST, SECOND]));
}

#[test]
fn a_rejected_span_crashes_and_poisons_the_run() {
    // If the backend answers 200 while reporting that it kept nothing...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    receiver.answer(Reply {
        rejected_spans: 3,
        error_message: "attribute limit exceeded".to_owned(),
        ..Reply::default()
    });
    let crashed = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect_err("a partial acceptance stops the run");
    // ...then the crash names the session and the batch an operator has to look at, and
    // carries neither transcript text nor the backend key...
    assert!(
        matches!(crashed, DeliveryError::Rejected { .. }),
        "{crashed}"
    );
    let message = crashed.to_string();
    assert!(
        message.contains(FIRST) && message.contains("batch 0"),
        "{message}"
    );
    assert!(!message.contains(KEY_SENTINEL));
    // ...nothing is recorded as delivered...
    assert_eq!(delivery_rows(&store), []);
    // ...and the corpus stays stuck there: a deterministic rejection is a mapper bug we need
    // to see, so there is no skip flag — every later run crashes at the same session, and the
    // session behind it never ships until the mapper changes.
    receiver.clear();
    let again = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
        .expect_err("the same session poisons the next run too");
    assert!(again.to_string().contains(FIRST), "{again}");
    let traces: std::collections::HashSet<Vec<u8>> = receiver
        .spans()
        .into_iter()
        .map(|span| span.trace_id)
        .collect();
    assert_eq!(
        traces,
        std::collections::HashSet::from([shaped(&store, &[FIRST])[0].trace_id.clone()])
    );
}

#[test]
fn a_failure_part_way_through_re_sends_the_batches_that_landed() {
    // If the backend takes the first batch and then breaks...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let first = shaped(&store, &[FIRST]);
    receiver.replies([Reply::default()]);
    receiver.answer(Reply::status(500));
    let mut bound = Shipping::new(&clock);
    bound.batch_spans = BOUND_BATCH;
    let crashed =
        deliver(&store, sentinel_backend(&receiver), bound).expect_err("the second batch fails");
    // ...then the run leaves no row at all, even though a batch did land — a session is
    // delivered whole or not delivered...
    assert!(crashed.to_string().contains(FIRST), "{crashed}");
    assert_eq!(delivery_rows(&store), []);
    assert_eq!(receiver.spans()[..BOUND_BATCH], first[..BOUND_BATCH]);
    // ...and when the backend recovers, the whole session goes again, first batch included.
    receiver.answer(Reply::default());
    let mut again = Shipping::new(&clock);
    again.batch_spans = BOUND_BATCH;
    let result = deliver(&store, sentinel_backend(&receiver), again).expect("both ship");
    assert_eq!(result.extracted, [FIRST, SECOND]);
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| row.session_id)
            .collect::<Vec<_>>(),
        [FIRST, SECOND]
    );
    // That re-send is the honest duplicate cost of at-least-once with stable ids: across the
    // two runs the backend holds the first batch twice, and the batch it refused four times.
    let mut sent: HashMap<Vec<u8>, usize> = HashMap::new();
    for span in receiver.spans() {
        *sent.entry(span.span_id).or_default() += 1;
    }
    assert_eq!(sent[&first[0].span_id], 2);
    assert_eq!(sent[&first[BOUND_BATCH].span_id], 4);
}

#[test]
fn only_a_clean_acceptance_records_a_delivery() {
    // `delivered` is the only word this system says about a remote it cannot query, so its
    // whole meaning is that no other backend answer, refusal or throttle produces it.
    for reply in [
        Reply::status(400),
        Reply::status(429),
        Reply::status(500),
        Reply {
            rejected_spans: 1,
            error_message: "attribute limit exceeded".to_owned(),
            ..Reply::default()
        },
    ] {
        let receiver = Receiver::start();
        let (_scratch, store) = shipping_store();
        let clock = TestClock::default();
        receiver.answer(reply.clone());
        deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock))
            .expect_err("no answer but a clean one delivers");
        assert_eq!(delivery_rows(&store), [], "{reply:?} recorded a delivery");
    }
}

#[test]
fn a_throttled_batch_waits_the_delay_the_backend_named() {
    // If the backend throttles the first request and takes everything after it...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    receiver.replies([Reply {
        status: 429,
        retry_after: Some(7),
        ..Reply::default()
    }]);
    let result =
        deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock)).expect("both ship");
    // ...then the exporter waited what the header asked for rather than its own backoff — the
    // waits after it are the rate bucket's, which the pacing leaf covers...
    assert_eq!(clock.delays()[0], 7.0);
    // ...and the retry is invisible downstream: one extra request, both sessions delivered,
    // one row each.
    assert_eq!(receiver.bodies().len(), 3);
    assert_eq!(result.extracted, [FIRST, SECOND]);
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| row.session_id)
            .collect::<Vec<_>>(),
        [FIRST, SECOND]
    );
}

#[test]
fn every_wait_goes_through_the_injected_clock() {
    // If the bucket has to pace a multi-batch run against a clock that refuses to wait...
    let receiver = Receiver::start();
    let (_scratch, store) = shipping_store();
    let clock = RefusingClock;
    let paced = refused(|| {
        let mut bound = Shipping::new(&clock);
        bound.batch_spans = BOUND_BATCH;
        bound.rate = BOUND_RATE;
        let _ = deliver(&store, sentinel_backend(&receiver), bound);
    });
    // ...and if a throttled batch has to back off against the same clock...
    receiver.replies([Reply {
        status: 429,
        retry_after: Some(7),
        ..Reply::default()
    }]);
    let backed_off = refused(|| {
        let _ = deliver(&store, sentinel_backend(&receiver), Shipping::new(&clock));
    });
    // ...then both crash out of the injected clock. A waiter that called `thread::sleep`
    // directly would pass every other leaf here while sleeping for real in CI.
    assert!(paced.contains("asked to wait"), "{paced}");
    assert!(backed_off.contains("asked to wait 7"), "{backed_off}");
    assert_eq!(delivery_rows(&store), []);
}

#[test]
fn a_named_backend_sends_its_key_under_its_own_header() {
    for (name, key_env, header, endpoint) in [
        (
            "honeycomb",
            "HONEYCOMB_API_KEY",
            "x-honeycomb-team",
            "https://api.honeycomb.io/v1/traces",
        ),
        (
            "logfire",
            "LOGFIRE_API_KEY",
            "authorization",
            "https://logfire-us.pydantic.dev/v1/traces",
        ),
    ] {
        // If a named backend is configured with nothing but its key variable...
        let receiver = Receiver::start();
        let (_scratch, store) = shipping_store();
        let clock = TestClock::default();
        let only_key = HashMap::from([(key_env.to_owned(), KEY_SENTINEL.to_owned())]);
        let resolved = named_backend(name, &only_key).expect("a key is all it needs");
        // ...then it resolves to the endpoint prior art verified (`claude-otel:114`)...
        assert_eq!(
            (resolved.name.as_str(), resolved.endpoint.as_str()),
            (name, endpoint)
        );
        // ...and shipping through it puts the key on the wire under that backend's own header
        // name, bare — Logfire refuses an `authorization: Bearer …`, and a reflex prefix there
        // is a 401 an hour into a backfill...
        let mut redirected = only_key.clone();
        redirected.insert("OTLP_ENDPOINT".to_owned(), receiver.url.clone());
        let target = named_backend(name, &redirected).expect("the override endpoint holds");
        deliver(&store, target, Shipping::new(&clock)).expect("both ship");
        assert_eq!(receiver.sent_headers()[0][header], KEY_SENTINEL);
        // ...while a run whose key variable is unset refuses before it reads a session.
        let missing = named_backend(name, &HashMap::new()).expect_err("no key, no run");
        assert!(missing.to_string().contains(key_env), "{missing}");
    }
}

#[test]
fn a_request_that_leaves_this_machine_is_refused() {
    // The guard is [`deliver`]'s own, so a run that forgets the receiver's URL fails here
    // rather than billing a backend and handing it a transcript. Python monkeypatches the
    // client for this; nothing here can reach into a `reqwest` client the same way, so the
    // helper every leaf ships through refuses the endpoint instead.
    let (_scratch, store) = shipping_store();
    let clock = TestClock::default();
    let off_machine = refused(|| {
        let _ = deliver(
            &store,
            Backend::bare("generic", "https://example.com/v1/traces"),
            Shipping::new(&clock),
        );
    });
    assert!(off_machine.contains("example.com"), "{off_machine}");
}

#[test]
fn a_live_send_is_accepted() {
    // This is the only leaf that can touch auth and dataset routing at all, and it never runs
    // green in CI. It returns when no backend is named, rather than faking the one thing no
    // receiver we wrote can prove — nextest has no runtime skip, so an unset gate is a
    // return.
    let name = std::env::var(LIVE_ENV)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if name.is_empty() {
        return;
    }
    // Past the gate the leaf is loud. A named backend that will not build is a misconfigured
    // run, and the whole point of opening the gate was to send: swallowing the `Err` here
    // would hand back the same green as a live send that the backend confirmed.
    let environment: HashMap<String, String> = std::env::vars().collect();
    let backend = named_backend(&name, &environment).unwrap_or_else(|error| {
        panic!("{LIVE_ENV} names `{name}`, which this run cannot build: {error}")
    });
    let (_scratch, store) = shipping_store();
    let clock = hyphae_export::delivery::SystemClock::new();
    // Any refusal — a status, or a nonzero `partial_success` — comes back out of `export()`,
    // so reaching the rows means the backend took every span of both sessions.
    let exporter = OtlpExporter::new(backend, &store, Shipping::new(&clock)).expect("it opens");
    let result = refresh(
        std::path::Path::new(MYCELIA),
        &StoreSource::new(&store),
        &exporter,
    )
    .expect("the live backend takes both sessions");
    assert_eq!(result.extracted, [FIRST, SECOND]);
    assert_eq!(
        delivery_rows(&store)
            .into_iter()
            .map(|row| row.session_id)
            .collect::<Vec<_>>(),
        [FIRST, SECOND]
    );
}
