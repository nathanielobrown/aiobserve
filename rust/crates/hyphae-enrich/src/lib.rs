//! Enrichment: what a model wrote about each run, turn and session in the trace store.
//!
//! It holds the enrichment schema, the item shapes a prompt is rendered from, the store that
//! reads enrichable items out and writes accepted answers back, the render that turns an item
//! into prompt text, and the screen every model answer passes before a row is written. `src/hyphae/enrich/` stays the authority for
//! everything here until the Python pass retires.

pub mod items;
pub mod prompts;
/// The `EnrichmentStore` reads, in their own file: `store` is over the length budget with
/// them in it, and they are the half that only assembles items.
mod read;
pub mod schema;
pub mod store;
pub mod taxonomy;
pub mod validation;

pub use items::{
    AgentRunItem, ApiCallRow, Item, RunSection, SessionChild, SessionItem, ToolCallRow, TurnItem,
    level_of,
};
pub use prompts::{Budgets, input_hash, render_run, render_session, render_turn};
pub use schema::Level;
pub use store::{EnrichError, EnrichmentStore, RunLink, Stamp};
pub use validation::{Enrichment, FailureKind, InvalidOutput, ItemFailure, validate};
