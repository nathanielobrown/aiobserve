//! What both of `hp`'s process-level test files need: the binary, and one run of it.
//!
//! A directory rather than a file so that cargo folds it into each test target instead of
//! building it as a target of its own. Each file uses a subset, hence the allow.

#![allow(dead_code)]

use std::process::Command;

/// The binary under test, built by cargo before either file runs.
pub const HP: &str = env!("CARGO_BIN_EXE_hp");

/// `hp <args>`, run to completion, with what it said on stderr.
pub fn run(args: &[&std::ffi::OsStr]) -> (bool, String) {
    let done = Command::new(HP).args(args).output().expect("hp runs");
    (
        done.status.success(),
        String::from_utf8_lossy(&done.stderr).into_owned(),
    )
}
