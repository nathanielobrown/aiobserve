//! What `hp`'s test files need: the command line run in process, and the binary when only a
//! process will do.
//!
//! A directory rather than a file so that cargo folds it into each test target instead of
//! building it as a target of its own. Each file uses a subset, hence the allow.

#![allow(dead_code)]

use std::ffi::OsStr;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use hyphae_testsupport::corpus::repo;

/// The binary under test, built by cargo before either file runs.
pub const HP: &str = env!("CARGO_BIN_EXE_hp");

/// What one `hp` run said, split by stream — the twin of `tests/analyze/conftest.py`'s `Output`.
///
/// A refusal lands on `stderr` wherever it was produced, because that is where the process
/// puts it: `main.rs` is a writer and an exit code over the same two channels.
#[derive(Debug)]
pub struct Output {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

/// `hp <args>`, run **in this process** over two buffers.
///
/// The seam the whole CLI tier sits on: a subcommand is a function taking two writers, so a
/// leaf that is not about the process boundary costs a function call rather than a spawn.
pub fn hp<S: AsRef<OsStr>>(args: &[S]) -> Output {
    hp_with(args, &hp::ClaudeCli)
}

/// The same, against a stand-in for the two doors `hp enrich` opens onto a real model.
///
/// The port of monkeypatching `cli.preflight` and `cli.build_client`: no leaf here starts a
/// `claude`, and one that forgot to pass a fake would have to name [`hp::ClaudeCli`] to do it.
pub fn hp_with<S: AsRef<OsStr>>(args: &[S], models: &dyn hp::Models) -> Output {
    let mut argv: Vec<std::ffi::OsString> = vec!["hp".into()];
    argv.extend(args.iter().map(|arg| arg.as_ref().to_owned()));
    let (mut out, mut err) = (Vec::new(), Vec::new());
    let answer = hp::main_with(argv, models, &mut out, &mut err);
    let mut stderr = String::from_utf8_lossy(&err).into_owned();
    let ok = match answer {
        Ok(()) => true,
        Err(refusal) => {
            stderr.push_str(&refusal.message);
            stderr.push('\n');
            false
        }
    };
    Output {
        ok,
        stdout: String::from_utf8_lossy(&out).into_owned(),
        stderr,
    }
}

/// `hp <args>` as a real process, for the leaves where the process *is* the subject.
///
/// Two things only a spawn can show: an exit status the shell sees, and what the extractor
/// wrote to the process's own stderr — `hyphae-extract` warns with `eprintln!`, which no
/// writer this side passes in can catch.
pub fn spawn<S: AsRef<OsStr>>(args: &[S]) -> Output {
    let done = Command::new(HP).args(args).output().expect("hp runs");
    Output {
        ok: done.status.success(),
        stdout: String::from_utf8_lossy(&done.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&done.stderr).into_owned(),
    }
}

/// How long a leaf waits for a spawned process to reach the state it is waiting for. Long
/// enough for a cold `uv run` to resolve an environment, and short enough to stay inside the
/// 30 seconds `tests/conftest.py:_HOLDER` holds the store lock for — a wait that outlived the
/// holder would report whatever the viewer said after it let go.
pub const PATIENCE: Duration = Duration::from_secs(20);

/// Wait until `signal` exists, or fail — a child that died first says why on the way out.
pub fn touched(signal: &Path, child: &mut Child, why: &str) {
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
pub fn holding(db: &Path, scratch: &Path) -> (Child, PathBuf) {
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
