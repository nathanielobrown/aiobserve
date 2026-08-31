//! `hp` — the prototype's command line: `extract` writes the trace store, `view` serves it.
//!
//! These are the two of `src/hyphae/cli.py`'s subcommands the prototype ports. The flags they
//! share carry that CLI's names and defaults, so a parity oracle can drive either implementation
//! from one command line; `--no-browser` and `--dev` are out of scope and absent, not ignored
//! (`plans/rust-prototype/design.md`).
//!
//! Both refuse rather than degrade: `extract` on a project with nothing recorded under the
//! projects root, `view` on a missing store, a store at another schema version, or a held
//! port — the store checks run before the bind, so the message names the store, not the socket.
//!
//! The commands themselves are the library beside this file, which owns nothing but the
//! process: the real streams in, and a [`hp::CliError`] out as a line on stderr and a status.

use std::io::Write;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut out = std::io::stdout().lock();
    let mut err = std::io::stderr().lock();
    match hp::main(std::env::args_os(), &mut out, &mut err) {
        Ok(()) => ExitCode::SUCCESS,
        Err(refusal) => {
            // Verbatim: the message already carries whatever prefix its kind wants.
            let _ = writeln!(err, "{refusal}");
            ExitCode::from(refusal.code)
        }
    }
}
