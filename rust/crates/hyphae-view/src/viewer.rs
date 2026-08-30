//! What a route module is built with: the store to read, and whether `--dev` is on.
//!
//! Ported from `src/hyphae/view/viewer.py`. One per app, handed to each route as its state rather
//! than reached for through a global.

use crate::store::Reader;

/// The store every route reads, and whether `--dev` is on.
pub struct Viewer {
    pub reader: Reader,
    pub dev: bool,
}
