//! What `hp`'s test files need: the command line run in process, and the binary when only a
//! process will do.
//!
//! A directory rather than a file so that cargo folds it into each test target instead of
//! building it as a target of its own. Each file uses a subset, hence the allow.

#![allow(dead_code)]

use std::ffi::OsStr;
use std::process::Command;

/// The binary under test, built by cargo before either file runs.
pub const HP: &str = env!("CARGO_BIN_EXE_hp");

/// What one `hp` run said, split by stream — the twin of `tests/analyze/conftest.py`'s `Output`.
///
/// A refusal lands on `stderr` wherever it was produced, because that is where the process
/// puts it: `main.rs` is a writer and an exit code over the same two channels.
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
