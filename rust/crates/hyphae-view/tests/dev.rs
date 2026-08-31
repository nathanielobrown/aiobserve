//! The dev loop's server half: what `--dev` adds to a page, and what the shipped viewer never
//! carries.
//!
//! Ported from `tests/view/test_dev.py`. Two apps over the one fixture store, compared byte for
//! byte — the seam is served HTML, like the rest of this tier. What HTML cannot show gets shapes
//! of its own: the reload stream never ends, so a leaf takes it frame by frame
//! (`hyphae_testsupport::served::taken`) rather than reading a body whole, and the graceful exit
//! is only observable in a real process, so that leaf lives in `hp/tests/cli.rs`.
//!
//! The change sets handed to `event_for` here are invented — a platform watcher yields whatever
//! it yields and no recording of one exists — which is why one slow leaf drives the real watcher
//! and checks that shape against what the invented ones assume.

use std::path::{Path, PathBuf};

use axum::http::StatusCode;
use hyphae_testsupport::selections::scenarios;
use hyphae_testsupport::served::{PATIENCE, Served};
use hyphae_view::app::{CSP, HOST, claim};
use hyphae_view::dev::{Event, RELOAD_URL, RENDERED, Reloads, STATIC, event_for, rendered};
use notify::EventKind;
use notify::event::{CreateKind, ModifyKind, RemoveKind};

/// The one line a page adds under `--dev`, whole. A shipped page is the dev page with this string
/// taken out and nothing else changed.
const TAG: &str = r#"<script src="/static/dev-reload.js" defer></script>"#;

// --- What a change set asks the browser to do -------------------------------------------

#[test]
fn a_change_set_is_a_css_event_only_when_every_path_in_it_is_a_stylesheet() {
    // A stylesheet swaps in place; anything else — a script — needs a reload.
    //
    // Invented change sets, labelled: a watcher yields what the platform hands it and no
    // recording of one exists. The slow leaf below checks the shape.
    let cases = [
        // If every path in the set is a stylesheet the page can keep its state...
        (vec!["static/style.css", "static/pygments.css"], Event::Css),
        // ...but the client script beside a stylesheet is a page event, because a set the fast
        // path takes is a set whose script edit never reaches the browser...
        (
            vec!["static/style.css", "static/dev-reload.js"],
            Event::Page,
        ),
        // ...and the client script itself only takes effect on a load.
        (vec!["static/dev-reload.js"], Event::Page),
    ];
    for (paths, expected) in cases {
        let changes: Vec<(EventKind, PathBuf)> = paths
            .iter()
            .map(|path| (EventKind::Modify(ModifyKind::Any), PathBuf::from(path)))
            .collect();
        assert_eq!(
            event_for(&changes).expect("a non-empty set classifies"),
            expected,
            "{paths:?}"
        );
    }
}

#[test]
fn what_happened_to_a_stylesheet_does_not_change_what_the_browser_does() {
    // A stylesheet added, edited or deleted is one thing to a page: fetch the sheets again.
    for kind in [
        EventKind::Create(CreateKind::Any),
        EventKind::Modify(ModifyKind::Any),
        EventKind::Remove(RemoveKind::Any),
    ] {
        let changes = [(kind, PathBuf::from("static/style.css"))];
        assert_eq!(
            event_for(&changes).expect("a non-empty set classifies"),
            Event::Css,
            "{kind:?}"
        );
    }
}

#[test]
fn a_change_set_with_nothing_in_it_is_a_broken_assumption_rather_than_an_event() {
    // The watcher yields only when something changed, so an empty set is a bug to refuse on.
    let refusal = event_for(&[]).expect_err("an empty set is not an event");
    assert!(refusal.to_string().contains("empty"), "{refusal}");
}

#[test]
fn the_watcher_is_told_to_report_only_what_the_viewer_renders_from() {
    // The filter the stream watches under, read directly: suffix, and the watcher's own noise.
    let cases = [
        // What the viewer renders from, which is what a save should reach the browser through...
        ("/w/style.css", true),
        ("/w/dev-reload.js", true),
        // ...a page, which is Rust here: a component edit is a rebuild, and a message from a
        // still-running binary would announce an edit that binary does not have...
        ("/w/node_pages.rs", false),
        // ...the directory macOS reports beside a saved file, which has no suffix and would read
        // as a page event if it got through...
        ("/w", false),
        // ...a file under a watched directory the viewer does not render...
        ("/w/README.md", false),
        // ...and what watchfiles' own filter drops, which this one still stands in for.
        ("/w/__pycache__/style.css", false),
    ];
    for (path, watched) in cases {
        assert_eq!(rendered(Path::new(path)), watched, "{path}");
    }
}

#[test]
fn the_stream_watches_the_static_directory_and_nothing_a_page_is_written_in() {
    // What `--dev` watches, and the suffixes it reports on.
    //
    // A page is Rust here, so a saved component is a rebuild rather than a message on this
    // stream. Widened to the crate, every component save would put a reload on the wire for a
    // binary that cannot have changed. Read off the constant rather than off a run, because
    // what the loop watches is exactly what no served page can show.
    let watched = Path::new(STATIC);
    assert!(watched.is_dir(), "{}", watched.display());
    assert!(
        watched.join("style.css").is_file(),
        "the watched directory is the one the stylesheets are in"
    );
    // The one suffix that never arrived: markup is not something either viewer renders from
    // disk, so a `.html` save is nothing to tell a browser about.
    assert!(!RENDERED.contains(&"html"), "{RENDERED:?}");
}

// Drives the real watcher over a real directory: wall clock, and the only leaf standing between
// a green classifier and change sets it never sees in this shape.
#[tokio::test(flavor = "multi_thread")]
async fn the_change_sets_the_watcher_yields_are_the_shape_the_invented_ones_assume() {
    // What the real watcher hands the classifier is a set of `(kind, absolute path)` pairs, and
    // the noisy half of it is what made the filter necessary.
    //
    // Driven end to end rather than by reading the raw set: the filter and the classifier sit
    // between the watcher and the wire, so the frame that comes out is the whole claim — an
    // unfiltered stylesheet save would arrive here as `page`, because macOS reports the
    // containing directory beside the file and a directory has no suffix.
    let scratch = tempfile::tempdir().expect("a tempdir");
    let reloads = Reloads::watching(&[scratch.path()]).expect("the watcher starts");
    let served = Served::corpus_dev(reloads, scratch.path().to_path_buf());
    let saved = scratch.path().join("style.css");
    let frames = served
        .frames(RELOAD_URL, 1, || {
            std::fs::write(&saved, "body { color: red }").expect("the stylesheet is writable");
        })
        .await;
    assert_eq!(frames, vec!["data: css\n\n".to_owned()]);
}

// --- What the dev app serves that the shipped one does not ------------------------------

#[tokio::test]
async fn a_dev_page_is_a_shipped_page_plus_the_one_script_tag() {
    // `--dev` changes one line of every page and nothing else, and no shipped page mentions it.
    //
    // One comparison for both halves of the promise: that the dev loop reaches every page, and
    // that a viewer built without it serves exactly what it served before.
    let dev = Served::enriched_dev();
    let shipped = Served::enriched();
    for (route, url) in scenarios() {
        let (dev_status, dev_page) = dev.page(&url).await;
        let (shipped_status, shipped_page) = shipped.page(&url).await;
        assert_eq!(dev_status, StatusCode::OK, "{url}");
        assert_eq!(shipped_status, StatusCode::OK, "{url}");
        // Taking the tag out of the dev page leaves the shipped page, byte for byte...
        assert_eq!(dev_page.replace(TAG, ""), shipped_page, "{url}");
        // ...it lands once on a whole page and not at all on a fragment, which stands inside no
        // frame and comes back identical outright...
        let wanted = usize::from(!route.starts_with("/fragment/"));
        assert_eq!(dev_page.matches(TAG).count(), wanted, "{url}");
        // ...and the shipped viewer names neither the client script nor the route it listens on.
        assert!(!shipped_page.contains("dev-reload"), "{url}");
        assert!(!shipped_page.contains("/dev/"), "{url}");
    }
}

#[tokio::test]
async fn the_shipped_viewer_declares_no_route_under_dev() {
    // `--dev` adds the reload stream and nothing else; without it the route is not there.
    //
    // What keeps `SCENARIOS` meaning "everything the shipped viewer serves" — the completeness
    // leaf in `tests/bounds_payload.rs` never has to list a dev route.
    assert!(
        !hyphae_view::routes::paths()
            .iter()
            .any(|path| path.starts_with("/dev/")),
        "the declared list is the shipped viewer's whole surface"
    );
    let shipped = Served::corpus();
    assert_eq!(shipped.page(RELOAD_URL).await.0, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn the_client_script_is_the_file_on_disk_whichever_mode_asked_for_it() {
    // The static route serves the reload client either way; only dev asks for it.
    let on_disk = std::fs::read_to_string(Path::new(STATIC).join("dev-reload.js"))
        .expect("the client script is in the repo");
    for served in [Served::corpus(), Served::enriched_dev()] {
        let (status, body) = served.page("/static/dev-reload.js").await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body, on_disk);
    }
}

#[tokio::test]
async fn a_saved_stylesheet_is_the_bytes_on_disk_rather_than_the_ones_compiled_in() {
    // What makes the css swap show the edit instead of announcing it.
    //
    // The static files are compiled into the binary, so a re-fetch after a save would hand the
    // browser exactly what it already had. Under `--dev` the route reads the watched directory
    // instead — and the shipped viewer keeps answering from the build, which is what lets an
    // installed binary serve with no repository beside it.
    //
    // A temporary directory rather than the checkout's, for the reason the watched paths are an
    // argument at all: this leaf must not write into the static directory the rest of the suite
    // is reading.
    let scratch = tempfile::tempdir().expect("a tempdir");
    let saved = "body { color: rebeccapurple }";
    std::fs::write(scratch.path().join("style.css"), saved).expect("the stylesheet is writable");

    let dev = Served::corpus_dev(Reloads::detached(), scratch.path().to_path_buf());
    let shipped = Served::corpus();
    let (dev_status, dev_sheet) = dev.page("/static/style.css").await;
    let (_, built_sheet) = shipped.page("/static/style.css").await;

    assert_eq!(dev_status, StatusCode::OK);
    assert_eq!(dev_sheet, saved, "the dev viewer serves what was saved");
    assert_ne!(built_sheet, saved, "the shipped viewer serves its build");
    // And a name the watched directory has no file for still comes back, from the build: a
    // half-populated directory is a stale page rather than a 404 mid-loop.
    let (fallback_status, fallback) = dev.page("/static/pygments.css").await;
    assert_eq!(fallback_status, StatusCode::OK);
    assert_eq!(fallback, shipped.page("/static/pygments.css").await.1);
}

#[tokio::test]
async fn the_reload_stream_answers_as_an_event_stream_under_the_same_policy() {
    // `/dev/reload` is SSE, and it carries the policy every other response carries.
    //
    // The whole shape of this loop — a same-origin GET, a client script served from the app —
    // was chosen to leave `CSP` untouched, so the string is read back here rather than trusted.
    let served = Served::enriched_dev();
    let response = served.get(RELOAD_URL).await;
    assert_eq!(response.status(), StatusCode::OK);
    let headers = response.headers();
    assert_eq!(headers["content-type"], "text/event-stream", "{headers:?}");
    assert_eq!(headers["content-security-policy"], CSP, "{headers:?}");
}

#[tokio::test]
async fn a_file_saved_under_a_watched_path_becomes_one_message_on_the_stream() {
    // Saving a file the viewer renders from sends the browser the event for its kind.
    //
    // Published rather than saved: the channel between the watcher and the stream is the seam,
    // and the leaf above proves a real save reaches it. Two events, one stream, in order —
    // which is also what says the stream does not close after the first.
    let served = Served::enriched_dev();
    let reloads = served.reloads().clone();
    let frames = served
        .frames(RELOAD_URL, 2, move || {
            reloads.publish(Event::Css);
            reloads.publish(Event::Page);
        })
        .await;
    assert_eq!(
        frames,
        vec!["data: css\n\n".to_owned(), "data: page\n\n".to_owned()]
    );
}

#[tokio::test]
async fn a_stopped_server_ends_the_stream_rather_than_leaving_it_open() {
    // The half of graceful shutdown a router can be asked, with the process leaf in
    // `hp/tests/cli.rs` asking the other.
    //
    // An SSE response has no last chunk, so a graceful exit that waits for every in-flight
    // response would wait on this one forever. `serve` ends the streams instead of waiting them
    // out, and this is that ending: the body finishes, from the inside.
    let served = Served::enriched_dev();
    let reloads = served.reloads().clone();
    let response = served.get(RELOAD_URL).await;
    reloads.stop();
    // Bounded, because the failure this leaf guards against is a stream that never ends: an
    // unbounded read of one would hang the suite instead of reporting it.
    let body = tokio::time::timeout(
        PATIENCE,
        axum::body::to_bytes(response.into_body(), usize::MAX),
    )
    .await
    .expect("a stopped stream ends rather than staying open")
    .expect("the body reads");
    assert!(body.is_empty(), "{body:?}");
}

#[tokio::test]
async fn a_port_the_server_could_bind_is_not_refused_by_the_probe_that_guards_it() {
    // Stopping a dev viewer and starting it again is the loop's own move, so `claim` may only
    // refuse a port the server itself would have failed on.
    //
    // A connection the server side closed holds its address in `TIME_WAIT` for a minute or so,
    // where a bind without `SO_REUSEADDR` is refused. `claim` returns the server's own listener
    // rather than probing ahead of one, so there is nothing here that can be stricter — this
    // leaf is what would notice if the bind were ever replaced by a probe that is.
    let port = {
        let listener = std::net::TcpListener::bind((HOST, 0)).expect("the loopback binds");
        let port = listener
            .local_addr()
            .expect("a bound socket has an address")
            .port();
        let reader = std::net::TcpStream::connect((HOST, port)).expect("the listener accepts");
        // The server side closing first is what leaves the address in `TIME_WAIT`.
        drop(listener.accept().expect("the connection arrives").0);
        drop(reader);
        port
    };
    let taken = claim(port, "Unreachable: the port is free.")
        .await
        .expect("a port in TIME_WAIT is one the server would have taken");
    drop(taken);

    // And the case it is there for still refuses, naming the port and the way out.
    let held = std::net::TcpListener::bind((HOST, port)).expect("the port is free again");
    let refused = claim(port, "Stop the other one.")
        .await
        .expect_err("a port something else is listening on is refused");
    assert!(
        refused.contains(&format!("port {port} is in use")),
        "{refused}"
    );
    assert!(refused.contains("Stop the other one."), "{refused}");
    drop(held);
}
