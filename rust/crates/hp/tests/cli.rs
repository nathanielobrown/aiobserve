//! `hp` at the process level: what it refuses to start on, and what it does the second time.
//!
//! Every leaf here spawns the compiled binary, because the contract is about refusing to
//! launch and a library call cannot see an exit code. The locked-store leaf needs processes
//! for a second reason: DuckDB caches an instance by path, so a store this process created is
//! handed back from the cache on re-open and its file lock is never re-checked — an
//! in-process holder would be testing the harness.
//!
//! The twin is `tests/view/test_lifecycle.py`, which asks the Python viewer the same things.

use std::io::{Read as _, Write as _};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use hyphae_extract::sessions::encode_project_path;
use hyphae_store::{Store, schema};

/// The binary under test, built by cargo before this file runs.
const HP: &str = env!("CARGO_BIN_EXE_hp");

/// How long a leaf waits for a spawned process to reach the state it is waiting for. Long
/// enough for a cold `uv run` to resolve an environment, and short enough to stay inside the
/// 30 seconds `tests/conftest.py:_HOLDER` holds the store lock for — a wait that outlived the
/// holder would report whatever the viewer said after it let go.
const PATIENCE: Duration = Duration::from_secs(20);

/// The repository root, from this crate's own location.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("the crate sits three levels under the repository root")
}

/// A store with the schema and nothing in it — enough for `hp view` to open and serve.
fn empty_store(path: &Path) {
    drop(Store::create(path).expect("a fresh store"));
}

/// `hp <args>`, run to completion, with what it said on stderr.
fn run(args: &[&std::ffi::OsStr]) -> (bool, String) {
    let done = Command::new(HP).args(args).output().expect("hp runs");
    (
        done.status.success(),
        String::from_utf8_lossy(&done.stderr).into_owned(),
    )
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

/// A project nothing was ever recorded for is a typo far more often than an empty corpus, so
/// it is an error naming both paths rather than a run that extracts nothing.
#[test]
fn extract_over_a_root_with_no_sessions_names_the_directory() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let root = scratch.path().join("projects");
    let project = scratch.path().join("repo");
    std::fs::create_dir_all(&root).expect("the projects root is writable");
    std::fs::create_dir_all(&project).expect("the project directory is writable");

    let (ok, said) = run(&[
        "extract".as_ref(),
        project.as_os_str(),
        "--projects-root".as_ref(),
        root.as_os_str(),
        "--db".as_ref(),
        scratch.path().join("traces.duckdb").as_os_str(),
    ]);

    assert!(!ok, "extracting nothing should fail: {said}");
    // Both halves of the answer: which project was asked for, and where hp looked for it.
    assert!(said.contains(&project.display().to_string()), "{said}");
    assert!(said.contains(&root.display().to_string()), "{said}");
}

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

// ---------------------------------------------------------------------------
// Running

/// The fingerprint's whole purpose, at the level a person sees it: a second run over an
/// untouched tree reads nothing again.
#[test]
fn extract_run_twice_re_extracts_nothing() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    let db = scratch.path().join("traces.duckdb");
    let project = scratch.path().join("repo");
    std::fs::create_dir_all(&project).expect("the project directory is writable");
    // Claude Code names a project's directory after its working directory, so the tree this
    // plants has to be named the way discovery will look for it.
    let root = scratch.path().join("projects");
    let recorded = root.join(encode_project_path(&project));
    std::fs::create_dir_all(&recorded).expect("the session directory is writable");
    for entry in std::fs::read_dir(repo().join("tests/fixtures/spine")).expect("spine is readable")
    {
        let from = entry.expect("the entry is readable").path();
        if from.is_file() {
            std::fs::copy(
                &from,
                recorded.join(from.file_name().expect("a file has a name")),
            )
            .expect("the fixture copies");
        }
    }

    let extract: Vec<&std::ffi::OsStr> = vec![
        "extract".as_ref(),
        project.as_os_str(),
        "--projects-root".as_ref(),
        root.as_os_str(),
        "--db".as_ref(),
        db.as_os_str(),
    ];
    let first = Command::new(HP).args(&extract).output().expect("hp runs");
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    assert!(
        String::from_utf8_lossy(&first.stdout).starts_with("1 session(s) extracted, 0 unchanged"),
        "{}",
        String::from_utf8_lossy(&first.stdout)
    );
    let stamped = extracted_at(&db);

    let second = Command::new(HP).args(&extract).output().expect("hp runs");
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    // Nothing changed on disk, so nothing is read again...
    assert!(
        String::from_utf8_lossy(&second.stdout).starts_with("0 session(s) extracted, 1 unchanged"),
        "{}",
        String::from_utf8_lossy(&second.stdout)
    );
    // ...and the row still says when the first run wrote it, which is what "unchanged" means.
    assert_eq!(extracted_at(&db), stamped);
}

/// Every session's `extracted_at`, by session id.
fn extracted_at(db: &Path) -> Vec<(String, chrono::DateTime<chrono::Utc>)> {
    let store = Store::open_read_only(db).expect("the store opens");
    store
        .fetch(
            "SELECT session_id, extracted_at FROM extract_state ORDER BY session_id",
            &[],
        )
        .expect("extract_state is readable")
        .iter()
        .map(|row| {
            (
                row.str("session_id")
                    .expect("a row names its session")
                    .to_owned(),
                row.timestamp("extracted_at").expect("a row is stamped"),
            )
        })
        .collect()
}

/// While an extract holds the store, a page says so and comes back once it lets go.
///
/// The holder is Python's own `tests/conftest.py:locked` — the extractor that takes this lock
/// in production is the Python one, and the store file is the seam the two implementations
/// share. It signals through a file rather than being polled by an open: a read-only open
/// takes a shared read lock and DuckDB refuses a write open under one, so polling by opening
/// would kill the holder it waited for.
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

    let taken = scratch.path().join("taken");
    let release = scratch.path().join("release");
    let mut holder = Command::new("uv")
        .args(["run", "--project"])
        .arg(repo())
        .args(["python", "-c", HOLD])
        .arg(&db)
        .arg(&taken)
        .arg(&release)
        .current_dir(repo())
        .stderr(Stdio::piped())
        .spawn()
        .expect("uv runs the lock holder");
    touched(&taken, &mut holder, "nothing took the store's write lock");

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
