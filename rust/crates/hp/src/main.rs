//! `hp` — the prototype's command line: `sessions` lists what was recorded, `extract` writes the
//! trace store, `enrich` describes it, `query` reads it, `export-otlp` ships it and `view` serves
//! it.
//!
//! All six of `src/hyphae/cli.py`'s subcommands, carrying that CLI's flag names and defaults so a
//! parity oracle can drive either implementation from one command line. `hp view` takes no
//! `--no-browser`, because it opens no browser — the one flag of the six the port leaves out
//! (`plans/rust-prototype/full-port.md`). `tests/surface.rs` is what pins the rest.
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
