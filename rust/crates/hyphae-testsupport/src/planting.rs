//! The enrichment rows every tier that reads a description queries.
//!
//! Ported from `tests/conftest.py:build_enriched_store` and the two factories under it. The
//! pipeline writes no enrichment row, so anything that reads one has nothing to read until a
//! pass has run. Rows go in through [`EnrichmentStore::upsert`] over the items the store
//! itself lists, so the keys are the ones a real pass writes; only the four model-written
//! fields are invented, and they have to be — no fixture records a model answer.
//!
//! The last item of each level is left undescribed, which is both the gap coverage reports
//! and the partly-enriched store the viewer has to render.

use hyphae_enrich::{Enrichment, EnrichmentStore, Level, Stamp};

use crate::metadata;

/// What kind of work a planted row calls itself.
///
/// Five slots over three categories, so two of them are twice as common as the third whatever
/// the corpus holds: a stratified draw only proves anything against uneven strata, and a cycle
/// that divided the items evenly would prove it by accident of the corpus's size.
pub const PLANTED_CATEGORIES: [&str; 5] = ["implement", "test", "debug", "implement", "test"];

/// How a planted row says its work ended.
pub const PLANTED_OUTCOMES: [&str; 3] = ["completed", "partial", "failed"];

/// How many rows an outcome holds for before the cycle advances. Its own constant, so the
/// outcome cycle keeps its period when the category cycle's length changes.
pub const PLANTED_OUTCOME_RUN: usize = 3;

/// The models a planted row is attributed to.
pub const PLANTED_MODELS: [&str; 2] = ["claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"];

/// Plant a row on all but the last item of each level, as a partial pass would leave them.
pub fn plant(store: &EnrichmentStore) {
    for level in Level::ALL {
        let items = store
            .items(level, None)
            .unwrap_or_else(|error| panic!("the {level} items read: {error}"));
        let described = items.len().saturating_sub(1);
        for (index, item) in items.iter().take(described).enumerate() {
            store
                .upsert(
                    item.as_ref(),
                    &planted_enrichment(index),
                    &planted_stamp(level, index),
                )
                .unwrap_or_else(|error| panic!("the {level} row plants: {error}"));
        }
    }
}

/// What the planted row says, cycling so a distribution has something to distribute.
pub fn planted_enrichment(index: usize) -> Enrichment {
    Enrichment {
        description: format!("Planted description {index}."),
        category: word(
            "category",
            PLANTED_CATEGORIES[index % PLANTED_CATEGORIES.len()],
        ),
        outcome: word(
            "outcome",
            PLANTED_OUTCOMES[(index / PLANTED_OUTCOME_RUN) % PLANTED_OUTCOMES.len()],
        ),
        // Every fourth row, which is coprime with both cycles above: friction that tracked a
        // category could not tell a count of one from a count of the other.
        friction: index
            .is_multiple_of(4)
            .then(|| "Planted friction.".to_owned()),
    }
}

/// What the planted row was written under — real versions, so nothing reads as drift.
pub fn planted_stamp(level: Level, index: usize) -> Stamp {
    Stamp {
        input_hash: format!("planted-{level}-{index}"),
        // A version behind on every fifth row: the stamp breakdown splits on the model and on
        // the prompt version, axes that moved together could not say which, and the viewer's
        // stale tag needs a row on each side of the current version.
        prompt_version: level.prompt_version() - i64::from(index.is_multiple_of(5)),
        taxonomy_version: metadata::enrichment().taxonomy_version,
        model: PLANTED_MODELS[index % PLANTED_MODELS.len()].to_owned(),
    }
}

/// One planted word, checked against the vocabulary the generation bridge carries over.
///
/// The taxonomy is Python's (`enrich/taxonomy.py`), so a word retired there has to red here
/// rather than plant a row no reader of the closed set can classify.
fn word(kind: &str, planted: &'static str) -> String {
    let vocabulary = match kind {
        "category" => &metadata::enrichment().categories,
        _ => &metadata::enrichment().outcomes,
    };
    assert!(
        vocabulary.iter().any(|held| held == planted),
        "`{planted}` is no longer a {kind} — the bridged vocabulary holds {vocabulary:?}"
    );
    planted.to_owned()
}
