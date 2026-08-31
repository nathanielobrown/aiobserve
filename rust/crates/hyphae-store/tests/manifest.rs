//! The generated query manifest: that it covers the catalog, and that every default binds.
//!
//! `rust/metadata/query_manifest.json` is Python's `analyze/manifest.py` written out for this
//! side to compile in (`plans/rust-prototype/full-port.md`). Its freshness against the Python
//! module is gated in the Python tier — `rust-check` runs on machines that never load it — so
//! what is left here is what only this side can see: that the file the crate compiled in
//! answers for every query the crate can load, and that what it says a parameter defaults to
//! is something this side can actually bind.

use hyphae_store::manifest::{self, ParamType, Scope};
use hyphae_store::{Param, queries};

/// Every query the catalog can load has a manifest entry, and every entry names a query.
///
/// The two halves are what a runner needs together: SQL it cannot bind is unusable, and an
/// entry for a file nobody ships is a binding table describing a query that does not exist.
#[test]
fn the_manifest_and_the_query_catalog_name_the_same_queries() {
    let mut catalog: Vec<&str> = queries::QUERIES.iter().map(|(stem, _)| *stem).collect();
    catalog.sort_unstable();
    let mut declared: Vec<&str> = manifest::manifest().keys().map(String::as_str).collect();
    declared.sort_unstable();
    assert_eq!(catalog, declared);
}

/// Every default the manifest carries turns into a value this side can bind.
///
/// The bridge is only worth having if what crosses it is usable: a default of a type this
/// side has no `Param` for would be a slice-6 crash discovered at run time, and a required
/// parameter that quietly answered with a value would bind a question nobody asked.
#[test]
fn every_parameter_binds_the_kind_the_manifest_declares() {
    let mut required = 0;
    let mut defaulted = 0;
    for (name, query) in manifest::manifest() {
        for (parameter, spec) in &query.params {
            let named = format!("{name}.{parameter}");
            match spec.binding() {
                None => {
                    assert!(spec.required, "{named} binds nothing and is not required");
                    required += 1;
                }
                // A default is bound as the type the parameter declares, or as the blank a
                // reader left — which is a value in its own right, not an absent one.
                Some(bound) => {
                    let fits = matches!(
                        (spec.kind, &bound),
                        (_, Param::Absent)
                            | (ParamType::Text, Param::Text(_))
                            | (ParamType::Integer, Param::Int(_))
                            | (ParamType::Date, Param::Date(_))
                    );
                    assert!(fits, "{named} is {:?} and defaults to {bound:?}", spec.kind);
                    defaulted += 1;
                }
            }
        }
    }
    // Both kinds are in the library, so neither arm above passed by never running.
    assert!(
        required > 0 && defaulted > 0,
        "{required} required, {defaulted} defaulted"
    );
}

/// A query's scope crosses the bridge, because it decides what the runner has to give it.
///
/// A corpus query reads the trailing window and the project predicate the runner builds; a
/// keyed one is handed the ids it asks for. Both kinds are in the library.
#[test]
fn both_scopes_reach_this_side() {
    let scopes: Vec<Scope> = manifest::manifest()
        .values()
        .map(|query| query.scope)
        .collect();
    assert!(scopes.contains(&Scope::Corpus) && scopes.contains(&Scope::Keyed));
    // The viewer's own queries are keyed by construction: they answer about one node of one
    // session, and the store they read is whichever one the reader pointed the viewer at.
    for (name, query) in manifest::manifest() {
        if name.starts_with(queries::VIEW_PREFIX) {
            assert_eq!(query.scope, Scope::Keyed, "{name}");
        }
    }
}
