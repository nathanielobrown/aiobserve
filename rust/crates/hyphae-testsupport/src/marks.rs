//! The character every surface prints beside a node of each kind.
//!
//! Written out rather than read from `nodes::Kind::icon`: these are the viewer's whole visual
//! vocabulary, and a test that imported the table would agree with any edit to it. Shared
//! because more than one test binary reads a node's mark and the tables must not drift — the
//! twin of the `MARKS` dict in `tests/view/test_node.py`.

/// One mark serves both buckets: each holds what the transcript could not attach, and a reader
/// meets them as one kind of hole, not two.
pub const MARKS: [(&str, &str); 8] = [
    ("session", "❖"),
    ("turn", "❯"),
    ("run", "◎"),
    ("call", "⇄"),
    ("tool", "⚒"),
    ("compaction", "⊟"),
    ("unattributed", "∅"),
    ("unattached", "∅"),
];

/// The mark a kind is named with wherever a page names one of its nodes.
pub fn mark(kind: &str) -> &'static str {
    MARKS
        .iter()
        .find(|(named, _)| *named == kind)
        .unwrap_or_else(|| panic!("no mark for {kind}"))
        .1
}
