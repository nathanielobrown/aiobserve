//! `hp view` at the process level: what it refuses to start on, and what it serves while
//! somebody else holds the store.
//!
//! Every leaf here spawns the compiled binary, because the contract is about refusing to
//! launch and a library call cannot see an exit code. The locked-store leaf needs processes
//! for a second reason: DuckDB caches an instance by path, so a store this process created is
//! handed back from the cache on re-open and its file lock is never re-checked — an
//! in-process holder would be testing the harness.
//!
//! The twin is `tests/view/test_lifecycle.py`, which asks the Python viewer the same things.
//! `hp extract` at the same level is `extract.rs`.

use std::io::{Read as _, Write as _};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use hyphae_store::{Store, schema};
use hyphae_testsupport::corpus::repo;

mod common;

use common::{HP, run};

/// How long a leaf waits for a spawned process to reach the state it is waiting for. Long
/// enough for a cold `uv run` to resolve an environment, and short enough to stay inside the
/// 30 seconds `tests/conftest.py:_HOLDER` holds the store lock for — a wait that outlived the
/// holder would report whatever the viewer said after it let go.
const PATIENCE: Duration = Duration::from_secs(20);

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

/// Wait until `signal` exists, or fail — a child that died first says why on the way out.
fn touched(signal: &Path, child: &mut Child, why: &str) {
    let deadline = Instant::now() + PATIENCE;
    while !signal.exists() {
        if let Some(status) = child.try_wait().expect("the child's state is readable") {
            let mut said = String::new();
            if let Some(stderr) = child.stderr.as_mut() {
                let _ = stderr.read_to_string(&mut said);
            }
            panic!("{why}: the process exited {status} — {said}");
        }
        assert!(
            Instant::now() < deadline,
            "{why}: nothing happened in {PATIENCE:?}"
        );
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Hold `db`'s write lock from another process, and the file that lets go of it.
///
/// The holder is Python's own `tests/conftest.py:locked` — the extractor that takes this lock
/// in production is the Python one, and the store file is the seam the two implementations
/// share. It signals through a file rather than being polled by an open: a read-only open
/// takes a shared read lock and DuckDB refuses a write open under one, so polling by opening
/// would kill the holder it waited for.
fn holding(db: &Path, scratch: &Path) -> (Child, std::path::PathBuf) {
    let taken = scratch.join("taken");
    let release = scratch.join("release");
    let mut holder = Command::new("uv")
        .args(["run", "--project"])
        .arg(repo())
        .args(["python", "-c", HOLD])
        .arg(db)
        .arg(&taken)
        .arg(&release)
        .current_dir(repo())
        .stderr(Stdio::piped())
        .spawn()
        .expect("uv runs the lock holder");
    touched(&taken, &mut holder, "nothing took the store's write lock");
    (holder, release)
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

    let (ok, said) = run(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);

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

    let (ok, said) = run(&[
        "view".as_ref(),
        "--db".as_ref(),
        missing.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);

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

    let (ok, said) = run(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);

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

    let (ok, said) = run(&[
        "view".as_ref(),
        "--db".as_ref(),
        db.as_os_str(),
        "--port".as_ref(),
        port.to_string().as_ref(),
    ]);

    std::fs::write(&release, "").expect("the release file is writable");
    holder.wait().expect("the holder exits");
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

/// The lock holder, run by `uv`: take the store's write lock, say so, and hold it until told.
const HOLD: &str = r#"
import pathlib, sys, time
sys.path.insert(0, str(pathlib.Path.cwd()))
from tests.conftest import locked

db, taken, release = (pathlib.Path(argument) for argument in sys.argv[1:4])
with locked(db):
    taken.touch()
    # `locked` holds for 30 seconds and lets go on its own, so this only has to outlive the
    # two requests the leaf makes; the deadline is what stops a killed test leaving a holder.
    deadline = time.monotonic() + 30
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
"#;
