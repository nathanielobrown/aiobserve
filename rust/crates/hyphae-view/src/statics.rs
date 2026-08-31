//! The files under `/static`, compiled into the binary by `build.rs`.
//!
//! One copy in the repo: these are the same bytes `src/hyphae/view/static/` holds and the Python
//! viewer mounts, so a stylesheet edit reaches both viewers. Everything the CSP allows to load
//! comes from here — `default-src 'self'` and no CDN.

// The name → content type → bytes table, walked out of the static directory by `build.rs`.
include!(concat!(env!("OUT_DIR"), "/assets.rs"));

/// One asset by file name, with what to call it, or nothing where the viewer serves no such file.
pub fn asset(name: &str) -> Option<(&'static str, &'static [u8])> {
    ASSETS
        .iter()
        .find(|(held, _, _)| *held == name)
        .map(|(_, kind, bytes)| (*kind, *bytes))
}

/// Every asset the viewer serves, for a test that wants the set rather than one of them.
pub fn assets() -> &'static [(&'static str, &'static str, &'static [u8])] {
    ASSETS
}
