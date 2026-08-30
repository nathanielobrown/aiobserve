//! The versioned SQL library, read from the files `src/hyphae/analyze/queries/` holds.
//!
//! Compiled in with `include_str!` rather than copied: the `.sql` file is the unit a report
//! cites and a reader re-runs, so both implementations must run the same bytes. That is also
//! why the workspace lives inside the repo — the path below is what makes the two trees one
//! source of truth.
//!
//! Stage 1 needs one query. Stage 3 ports the rest, at which point this becomes the `Page` /
//! `Fragment` / `Value` catalog of `view/store.py`.

/// One api call, whole: the header of a call's node page.
///
/// The stage-1 spike's node-page query, chosen because its `tools` column is a
/// `STRUCT(first STRUCT(name, fields STRUCT(...)), names LIST(VARCHAR))` — the nested shape
/// the design flagged as the go/no-go risk. Binds `$session_id`, `$source`, `$api_call_id`,
/// `$head_chars` and `$detail_chars`, and calls the macros in [`crate::macros`].
pub const VIEW_CALL_HEADER: &str =
    include_str!("../../../../src/hyphae/analyze/queries/view_call_header.sql");
