//! Enrichment: what a model wrote about each run, turn and session in the trace store.
//!
//! It holds the enrichment schema, the item shapes a prompt is rendered from, and the store
//! that reads enrichable items out and writes accepted answers back. What stays Python for
//! now: the prompt render, the CliRunner seam that buys the answers, and validation.
//! `src/hyphae/enrich/` stays the authority for everything here until the Python pass
//! retires.

pub mod items;
pub mod schema;
pub mod store;

pub use items::{
    AgentRunItem, ApiCallRow, Item, RunSection, SessionChild, SessionItem, ToolCallRow, TurnItem,
    level_of,
};
pub use schema::Level;
pub use store::{EnrichError, Enrichment, EnrichmentStore, RunLink, Stamp};
