//! The tools the fixture corpus records under a name the viewer names its calls by.
//!
//! Restated from `plans/viewer-polish/design.md` rather than read off `view::formatters`, which is
//! the thing every consumer of this table is testing. The eight names the registry knows that no
//! fixture records have no recorded call to serve, and the leaves that sweep this table say so out
//! loud against [`hyphae_view::formatters::NAMED`].

/// One recorded tool: its name, the glyph that leads its rows, and the input field its title is
/// read from.
pub const RECORDED: [(&str, &str, &str); 6] = [
    ("Read", "📖", "file_path"),
    ("Bash", "⚡", "command"),
    ("Agent", "👉", "subagent_type"),
    ("SendMessage", "📬", "to"),
    ("ToolSearch", "🧰", "query"),
    ("PushNotification", "🔔", "message"),
];

/// The glyph that leads a recorded tool's rows.
pub fn glyph(name: &str) -> &'static str {
    RECORDED
        .iter()
        .find(|(held, _, _)| *held == name)
        .unwrap_or_else(|| panic!("the corpus records no {name}"))
        .1
}
