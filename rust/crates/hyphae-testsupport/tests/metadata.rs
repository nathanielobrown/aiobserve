//! The generated registries this side compiles in: that they parse, and that a lookup is a
//! crash rather than a shrug.
//!
//! `rust/metadata/*.json` is Python written out for a reader that cannot import it
//! (`plans/rust-prototype/full-port.md`). Their freshness against the Python modules is gated
//! in the Python tier, where both can be read; what these leaves hold is the reading this side
//! does — that the accessors the drift leaves are written against answer, and that a name the
//! registry does not carry cannot pass for a number.

use hyphae_testsupport::metadata;

/// Every number the registry carries is reachable by the name Python declares it under.
///
/// The drift leaves in `hyphae-view` and `hyphae-store` are one lookup each, so a lookup that
/// answered `None` or zero for a renamed constant would turn every one of them green against
/// nothing. Walking the whole registry is what proves the accessors read what was parsed.
#[test]
fn every_number_the_registry_carries_answers_to_its_python_name() {
    let registry = metadata::bounds();
    assert!(!registry.bounds.is_empty() && !registry.sizes.is_empty());
    assert!(!registry.widths.is_empty() && !registry.knobs.is_empty());
    for (name, bound) in &registry.bounds {
        assert_eq!(metadata::bound(name), bound, "{name}");
        assert!(
            bound.default <= bound.ceiling,
            "{name} defaults over its ceiling"
        );
    }
    for (name, size) in &registry.sizes {
        assert_eq!(metadata::size(name), *size, "{name}");
    }
    for (name, width) in &registry.widths {
        assert_eq!(metadata::width(name), *width, "{name}");
    }
}

/// A name no registry entry carries crashes, naming the generator that would add it.
///
/// The failure this closes is a rename: a constant that moved in Python leaves a drift leaf
/// asking for a name nobody writes, and the useful answer is the crash, not a pass.
#[test]
#[should_panic(expected = "gen_bounds")]
fn a_size_the_registry_does_not_carry_is_a_crash() {
    metadata::size("NO_SUCH_SIZE");
}

/// Each enrichment level names the table its rows live in and the prompt they were written to.
///
/// What the cache key folds and what the viewer renders provenance from, so an entry short of
/// either is a stamp nothing can be judged against.
#[test]
fn every_enrichment_level_carries_a_table_and_a_prompt_version() {
    let enrichment = metadata::enrichment();
    assert!(!enrichment.levels.is_empty());
    for (word, level) in &enrichment.levels {
        assert!(level.prompt_version > 0, "{word} has no prompt version");
        assert!(!level.table.is_empty() && !level.base.is_empty(), "{word}");
        assert!(
            !level.keys.is_empty() && !level.base_keys.is_empty(),
            "{word}"
        );
    }
    // The two closed vocabularies, and no member written twice — a duplicate would be a
    // classifier offered the same answer under two spellings.
    assert!(enrichment.taxonomy_version > 0);
    for vocabulary in [&enrichment.categories, &enrichment.outcomes] {
        assert!(!vocabulary.is_empty());
        let named: std::collections::BTreeSet<&String> = vocabulary.iter().collect();
        assert_eq!(named.len(), vocabulary.len());
    }
}
