//! `hp view` at the process level: what it refuses to start on, and what it serves while
//! somebody else holds the store.
//!
//! Every leaf here spawns the compiled binary, because the contract is about refusing to
//! launch and a library call cannot see an exit code. The locked-store leaf needs processes
//! for a second reason: DuckDB caches an instance by path, so a store this process created is
//! handed back from the cache on re-open and its file lock is never re-checked — an
//! in-process holder would be testing the harness.
//!
//! The twin is `tests/view/test_lifecycle.py`, which asks the Python viewer the same things;
//! the interrupt leaf's twin is `tests/view/test_dev.py`. `hp extract` at the same level is
//! `extract.rs`.

use std::io::{Read as _, Write as _};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::process::{Child, Command};
use std::time::{Duration, Instant};

use hyphae_store::{Store, schema};

mod common;

use common::{HP, PATIENCE, holding, spawn};

/// A store with the schema and nothing in it — enough for `hp view` to open and serve.
fn empty_store(path: &Path) {
    drop(Store::create(path).expect("a fresh store"));
}

/// A port nothing is listening on, still held by the returned listener.
///
/// Held rather than released: a port asked for and let go is a port something else can take
/// between the two calls, and the leaves below are about what happens when one is taken.
fn held_port() -> (TcpListener, u16) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("the loopback has a spare port");
    let port = listener
        .local_addr()
        .expect("a bound socket has an address")
        .port();
    (listener, port)
}

/// The status and body of one GET against a running viewer, over a socket rather than a client
/// crate — a status line is all these leaves read.
fn get(port: u16, path: &str) -> Option<(u16, String)> {
    let mut socket = TcpStream::connect(("127.0.0.1", port)).ok()?;
    socket
        .write_all(
            format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .ok()?;
    let mut answer = String::new();
    socket.read_to_string(&mut answer).ok()?;
    let status = answer
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())?;
    Some((status, answer))
}

/// Wait until `port` answers `path` with `status`, or fail saying what it answered instead.
fn until(port: u16, path: &str, status: u16, why: &str) -> String {
    let deadline = Instant::now() + PATIENCE;
    let mut last = String::from("nothing answered");
    while Instant::now() < deadline {
        if let Some((code, answer)) = get(port, path) {
            if code == status {
                return answer;
            }
            last = format!("{code}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    panic!("{why}: {path} answered {last}, not {status}");
}

/// A spawned process killed when the leaf that started it ends, whatever it ended with.
struct Spawned(Child);

impl Drop for Spawned {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

// ---------------------------------------------------------------------------
// Refusing to start

/// A second viewer says which port is taken and how to pick another, rather than a bind error.
#[test]
fn view_refuses_a_port_something_else_holds() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    empty_store(&db);
    let (_held, port) = held_port();

    let said = spawn(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);
    let (ok, said) = (said.ok, said.stderr);

    assert!(!ok, "a taken port should refuse: {said}");
    assert!(said.contains(&port.to_string()), "{said}");
    assert!(said.contains("--port"), "{said}");
}

/// A typo in `--db` refuses at startup and names the path — not a browser opened onto an
/// error page, and not a bare `No such file or directory`.
///
/// The port this asks for is one the test itself holds, so a viewer that bound before it
/// checked the store would fail on the port instead. Naming the store is what proves the
/// order.
#[test]
fn view_refuses_a_store_that_is_not_there_before_it_binds() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let missing = scratch.path().join("nothing.duckdb");
    let (_held, port) = held_port();

    let said = spawn(&[
        "view".as_ref(),
        "--db".as_ref(),
        missing.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);
    let (ok, said) = (said.ok, said.stderr);

    assert!(!ok, "a missing store should refuse: {said}");
    assert!(said.contains(&missing.display().to_string()), "{said}");
    assert!(
        !said.contains("--port"),
        "it never got as far as the port: {said}"
    );
}

/// A store of another vintage refuses too, naming the version it holds — the same ordering
/// proof as above, on the other startup check.
#[test]
fn view_refuses_a_store_at_another_schema_version_before_it_binds() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    {
        let store = Store::create(&db).expect("a fresh store");
        store
            .connection()
            .execute(
                "UPDATE meta SET schema_version = ?",
                [schema::SCHEMA_VERSION - 1],
            )
            .expect("the version row is writable");
    }
    let (_held, port) = held_port();

    let said = spawn(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);
    let (ok, said) = (said.ok, said.stderr);

    assert!(!ok, "an older store should refuse: {said}");
    assert!(
        said.contains(&(schema::SCHEMA_VERSION - 1).to_string()),
        "{said}"
    );
    assert!(
        !said.contains("--port"),
        "it never got as far as the port: {said}"
    );
}

/// Starting against a store an extract holds refuses as well, naming the lock rather than the
/// port — the same ordering proof again, on the check that only another process can trip.
#[test]
fn view_refuses_a_locked_store_before_it_binds() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    empty_store(&db);
    let (mut holder, release) = holding(&db, scratch.path());
    let (_held, port) = held_port();

    let said = spawn(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);

    std::fs::write(&release, "").expect("the release file is writable");
    holder.wait().expect("the holder exits");
    let (ok, said) = (said.ok, said.stderr);
    assert!(!ok, "a locked store should refuse: {said}");
    assert!(said.contains("held by another process"), "{said}");
    assert!(
        !said.contains("--port"),
        "it never got as far as the port: {said}"
    );
}

// ---------------------------------------------------------------------------
// Running

/// While an extract holds the store, a page says so and comes back once it lets go.
#[test]
fn a_locked_store_answers_503_and_recovers() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    empty_store(&db);
    let (held, port) = held_port();
    // The viewer binds this port itself, so the reservation is dropped first.
    drop(held);

    #[expect(
        clippy::disallowed_methods,
        reason = "an hp process leaf: the subject is the built binary, exit code and channels included"
    )]
    let _viewer = Spawned(
        Command::new(HP)
            .args(["view", "--db"])
            .arg(&db)
            .args(["--port", &port.to_string()])
            .spawn()
            .expect("hp view starts"),
    );
    until(port, "/", 200, "the viewer never came up");

    let (mut holder, release) = holding(&db, scratch.path());

    // The store is there and it will read again shortly, so the honest answer is a 503...
    let answer = until(port, "/", 503, "a locked store was not refused");
    assert!(
        answer.contains("Another process holds the trace store"),
        "{answer}"
    );
    // ...on an error page like any other, policy included.
    assert!(
        answer.contains("content-security-policy: default-src 'self'"),
        "{answer}"
    );

    // And the viewer serves again once the writer lets go — no restart, because the store is
    // opened per request rather than held.
    std::fs::write(&release, "").expect("the release file is writable");
    until(port, "/", 200, "the viewer never recovered");
    holder.wait().expect("the holder exits");
}

/// Ctrl-C ends a dev viewer that has a browser listening on the reload stream.
///
/// The one place a graceful exit is observable: a router can be asked what its stream does when
/// the server stops (`hyphae-view/tests/dev.rs`), and only a process can be asked whether the
/// server stops at all. An SSE response has no last chunk, so an exit that waited for every
/// in-flight response would never return — `serve` ends the streams instead of waiting them out.
#[test]
fn an_open_stream_does_not_hold_the_server_open_when_it_is_interrupted() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    empty_store(&db);
    let (held, port) = held_port();
    // The viewer binds this port itself, so the reservation is dropped first.
    drop(held);

    #[expect(
        clippy::disallowed_methods,
        reason = "an hp process leaf: the subject is the built binary, exit code and channels included"
    )]
    let mut viewer = Command::new(HP)
        .args(["view", "--dev", "--db"])
        .arg(&db)
        .args(["--port", &port.to_string()])
        .spawn()
        .expect("hp view --dev starts");
    until(port, "/", 200, "the dev viewer never came up");

    // With a reader on the stream, the way a browser with the page open is...
    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("the stream connects");
    stream
        .write_all(
            format!(
                "GET {} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                hyphae_view::dev::RELOAD_URL
            )
            .as_bytes(),
        )
        .expect("the request goes out");
    stream
        .set_read_timeout(Some(PATIENCE))
        .expect("a read deadline");
    let mut head = [0_u8; 64];
    let read = stream.read(&mut head).expect("the response head arrives");
    assert!(
        String::from_utf8_lossy(&head[..read]).contains("200"),
        "the stream is open: {}",
        String::from_utf8_lossy(&head[..read])
    );

    // ...an interrupt still reaps the process, rather than waiting on a response that never
    // completes. The signal a person sends, not a kill: a killed process proves nothing about
    // what its exit path does.
    interrupt(&viewer);
    let status = reaped(&mut viewer);
    assert!(
        status.success(),
        "an interrupted dev viewer exits: {status}"
    );
}

/// Send `child` the signal Ctrl-C sends.
fn interrupt(child: &Child) {
    let pid = i32::try_from(child.id()).expect("a pid fits");
    // The one call in this tier that has to go through libc: `Child` can kill but not signal.
    let sent = unsafe { libc::kill(pid, libc::SIGINT) };
    assert_eq!(sent, 0, "the interrupt was delivered");
}

/// Wait for `child` to exit, or say how long it was given — a hang is the failure this guards.
fn reaped(child: &mut Child) -> std::process::ExitStatus {
    let deadline = Instant::now() + PATIENCE;
    loop {
        if let Some(status) = child.try_wait().expect("the child's state is readable") {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("the interrupted viewer was still running after {PATIENCE:?}");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}
