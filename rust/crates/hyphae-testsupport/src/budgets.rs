//! What a page the viewer serves may weigh.
//!
//! The ceilings of `tests/view/budgets.py`, which the leaves that sweep served responses read
//! as constants. The arithmetic that *predicts* those responses — the measured cost of a row, a
//! crumb, a preview, and the worst-case sums over them — is not ported: each of its pins is a
//! measurement of the Python viewer's own markup, and re-taking them belongs with the leaves
//! that spend them (`hyphae-view/tests/bounds_payload.rs`).

/// What a page may weigh. The list is the page a corpus grows, so `knobs::SESSIONS.ceiling` rows
/// of what one row can hold have to fit under it.
pub const PAGE_BYTES: usize = 500_000;
