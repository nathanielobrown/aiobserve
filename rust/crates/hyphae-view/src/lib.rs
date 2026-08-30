//! The trace viewer: every node of a session served as its own page.
//!
//! Mirrors `src/hyphae/view/`, module for module. Reading order is the way a request travels:
//! [`store`] reads the rows, [`nodes`] turns them into what a page prints, [`components`]
//! renders them, [`browse`] assembles one page, and [`app`] routes to it.

pub mod app;
pub mod browse;
pub mod builders;
pub mod citation;
pub mod columns;
pub mod components;
pub mod cuts;
pub mod detail;
pub mod enrichment;
pub mod errors;
pub mod expansions;
pub mod format;
pub mod formatters;
pub mod fragments;
pub mod highlight;
pub mod inline_markdown;
pub mod knobs;
pub mod labels;
pub mod listing;
pub mod nav_tree;
pub mod node_pages;
pub mod nodes;
pub mod numbers;
pub mod pages;
pub mod render;
pub mod statics;
pub mod store;
pub mod urls;
pub mod viewer;
pub mod walk;
