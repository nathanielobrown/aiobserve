//! The one seam between this crate and a real process.
//!
//! Ported from the `subprocess.run` calls in `src/hyphae/enrich/client.py`, which the Python
//! tests replace by monkeypatching the module attribute. Rust has no such door, so the door is
//! this trait: [`ProcessRunner`] is what a pass spends through, and the fake in
//! `hyphae-testsupport` is what every client leaf drives instead.

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// How often a waiting parent asks whether the child has finished.
const POLL: Duration = Duration::from_millis(20);

/// One process this client would start, whole — what a fake records and a runner spawns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Call {
    /// The binary and its arguments. Never the shell.
    pub argv: Vec<String>,
    /// What the child reads on stdin. None for the auth check, which sends nothing.
    pub input: Option<String>,
    /// The whole environment the child gets: constructed, never inherited.
    pub env: BTreeMap<String, String>,
    /// None for the auth check, which asks its question where the parent stands.
    pub cwd: Option<PathBuf>,
    /// The deadline. A child still running at it is killed and reported as a timeout.
    pub timeout: Duration,
}

/// What a finished process left behind. Stderr is not among it: nothing reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Output {
    pub code: i32,
    pub stdout: String,
}

/// Why no process answered. The three shapes `subprocess.run` raises, kept apart because
/// `preflight` and an item's attempt read them differently.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum CallError {
    /// The child was still running at the call's deadline, and was killed.
    #[error("the call was still running after {0:?}")]
    Timeout(Duration),
    /// Nothing to start: no `claude` where PATH said. `preflight` alone tells this apart.
    #[error("no such binary")]
    NotFound,
    /// The machine could not start the process — no file descriptor, no memory to fork with.
    #[error("the process could not be started: {0}")]
    Os(String),
}

impl CallError {
    fn spawning(error: &std::io::Error) -> Self {
        match error.kind() {
            std::io::ErrorKind::NotFound => Self::NotFound,
            _ => Self::Os(error.to_string()),
        }
    }
}

/// Runs one call to completion, or says why it could not.
///
/// Implementors must be shareable: the pool runs one call per worker thread through the one
/// runner the client holds.
pub trait CliRunner: Send + Sync {
    fn run(&self, call: &Call) -> Result<Output, CallError>;
}

impl<Held: CliRunner + ?Sized> CliRunner for std::sync::Arc<Held> {
    fn run(&self, call: &Call) -> Result<Output, CallError> {
        (**self).run(call)
    }
}

/// The runner a pass really spends through: one child process per call.
#[derive(Debug, Default, Clone, Copy)]
pub struct ProcessRunner;

impl CliRunner for ProcessRunner {
    fn run(&self, call: &Call) -> Result<Output, CallError> {
        let (binary, arguments) = call.argv.split_first().expect("a call names a binary");
        let mut command = Command::new(binary);
        command
            .args(arguments)
            // The child's whole environment, and nothing of this process's: a stray
            // `ANTHROPIC_API_KEY` would divert auth off the subscription with no signal.
            .env_clear()
            .envs(&call.env)
            .stdin(if call.input.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            // Discarded rather than captured: nothing reads it, and an unread pipe that
            // filled would hang the child behind this call's deadline.
            .stderr(Stdio::null());
        if let Some(cwd) = &call.cwd {
            command.current_dir(cwd);
        }
        let mut child = command
            .spawn()
            .map_err(|error| CallError::spawning(&error))?;
        // Both pipes are drained on their own threads: a render is far larger than a pipe
        // buffer, so writing it from here would block against a child blocked on writing back.
        let input = call.input.clone();
        let mut sink = child.stdin.take();
        let writing = std::thread::spawn(move || {
            if let (Some(pipe), Some(text)) = (sink.as_mut(), input) {
                let _ = pipe.write_all(text.as_bytes());
            }
            drop(sink);
        });
        let mut source = child.stdout.take().expect("stdout was piped");
        let reading = std::thread::spawn(move || {
            let mut written = String::new();
            let _ = source.read_to_string(&mut written);
            written
        });
        let deadline = Instant::now() + call.timeout;
        let status = loop {
            match child
                .try_wait()
                .map_err(|error| CallError::Os(error.to_string()))?
            {
                Some(status) => break status,
                None if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = writing.join();
                    let _ = reading.join();
                    return Err(CallError::Timeout(call.timeout));
                }
                None => std::thread::sleep(POLL),
            }
        };
        let _ = writing.join();
        let stdout = reading.join().unwrap_or_default();
        Ok(Output {
            // A child killed by a signal reports no code; anything but zero is a refusal here.
            code: status.code().unwrap_or(-1),
            stdout,
        })
    }
}
