//! The trace viewer: every node of a session served as its own page.
//!
//! Mirrors `src/hyphae/view/`, module for module. Reading order is the way a request travels:
//! [`store`] reads the rows, [`nodes`] turns them into what a page prints, [`components`]
//! renders them, [`browse`] assembles one page, and [`app`] routes to it.

pub mod app;
pub mod browse;
pub mod builders;
pub mod columns;
pub mod components;
pub mod cuts;
pub mod format;
pub mod formatters;
pub mod inline_markdown;
pub mod knobs;
pub mod labels;
pub mod nodes;
pub mod render;
pub mod statics;
pub mod store;
