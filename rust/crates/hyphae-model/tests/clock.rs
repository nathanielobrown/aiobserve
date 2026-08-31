//! Which of the two freezes wins when both are set.
//!
//! One leaf in its own file, and it has to stay that way: both routes are read-once and
//! irreversible, so a second leaf beside this one would share the state it set. nextest gives
//! every leaf a process anyway; a file of its own is what keeps `cargo test` honest too.
//!
//! That the environment alone stops the clock is `hyphae-view/tests/format.rs`, where the
//! relative times it feeds are.

use chrono::{DateTime, Utc};
use hyphae_model::clock;

/// A test that stopped the clock itself outranks a variable its parent exported.
///
/// The order matters because both routes are live at once under the browser tier: the
/// gallery exports the variable for the viewer it starts, and a leaf running inside that
/// process tree has no way to unexport it.
#[test]
fn a_frozen_instant_beats_the_environment() {
    // SAFETY: this is the file's only leaf, and no thread has started.
    unsafe { std::env::set_var(clock::FIXED_NOW, "2026-08-30T12:00:00Z") };
    let chosen: DateTime<Utc> = "2030-01-01T00:00:00Z".parse().expect("an RFC 3339 instant");

    clock::freeze(chosen);

    assert_eq!(clock::utcnow(), chosen);
    // And it stays stopped: a second read is the same instant, not a moved one.
    assert_eq!(clock::utcnow(), chosen);
}
