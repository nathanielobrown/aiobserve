//! What a route module is built with: the store to read, and whether `--dev` is on.
//!
//! Ported from `src/hyphae/view/viewer.py`. One per app, handed to each route as its state rather
//! than reached for through a global.

use std::path::PathBuf;

use crate::store::Reader;

/// The store every route reads, and whether `--dev` is on.
pub struct Viewer {
    pub reader: Reader,
    /// Under `--dev`, the directory the static files are read from — which is also the directory
    /// the reload loop watches ([`crate::dev`]). `None` is the shipped viewer, which answers from
    /// the copy compiled into the binary.
    pub dev: Option<PathBuf>,
}

impl Viewer {
    /// Whether this is the dev viewer, which is all a page needs to know: the one line it adds.
    #[must_use]
    pub fn dev(&self) -> bool {
        self.dev.is_some()
    }
}
