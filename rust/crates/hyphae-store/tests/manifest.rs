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

/// Where `name` first appears as a JSON key indented `indent` spaces.
///
/// The file is pretty-printed two spaces per level, so a query is a key at 2 and a parameter
/// one at 6. Reading the committed text is the point: parsing it the way the crate does could
/// only ever agree with itself.
fn key_at(text: &str, name: &str, indent: usize) -> usize {
    let key = format!("\n{}\"{name}\":", " ".repeat(indent));
    text.find(&key)
        .unwrap_or_else(|| panic!("`{name}` is not a key at indent {indent}"))
}

/// The manifest keeps the order `manifest.py` declared, queries and parameters alike.
///
/// Load-bearing rather than tidy. A citation line writes its bindings out in the order the
/// query declares them, and the refusal for an unknown name lists every query in the order
/// the module holds them — so a map that sorted on the way in would put both lines out of
/// step with Python's, and a parity diff would report every multi-parameter query.
#[test]
fn the_manifest_keeps_the_order_python_declared() {
    let text = manifest::MANIFEST_JSON;
    let declared: Vec<&str> = manifest::manifest().keys().map(String::as_str).collect();

    // Against a file that happened to be alphabetical, a sorting map would pass both halves
    // below. Each half is worth only as much as the disagreement it could see.
    let alphabetical = {
        let mut sorted = declared.clone();
        sorted.sort_unstable();
        sorted
    };
    assert_ne!(
        declared, alphabetical,
        "the library declares in sorted order"
    );

    let mut starts: Vec<(usize, &str)> = declared
        .iter()
        .map(|name| (key_at(text, name, 2), *name))
        .collect();
    let mut in_file = starts.clone();
    in_file.sort_unstable();
    let in_file: Vec<&str> = in_file.into_iter().map(|(_, name)| name).collect();
    assert_eq!(declared, in_file, "the queries are out of the file's order");

    // A parameter name repeats across the library, so each is looked for inside its own
    // query's block — from its key to the next query's.
    starts.sort_unstable();
    starts.push((text.len(), ""));
    let mut unsorted = 0;
    for pair in starts.windows(2) {
        let [(from, name), (to, _)] = pair else {
            unreachable!("windows(2) yields pairs")
        };
        let block = &text[*from..*to];
        let params: Vec<&str> = manifest::entry(name)
            .params
            .keys()
            .map(String::as_str)
            .collect();
        let mut found: Vec<(usize, &str)> = params
            .iter()
            .map(|parameter| (key_at(block, parameter, 6), *parameter))
            .collect();
        found.sort_unstable();
        let found: Vec<&str> = found.into_iter().map(|(_, parameter)| parameter).collect();
        assert_eq!(params, found, "{name}'s parameters are out of order");
        let mut alphabetical = params.clone();
        alphabetical.sort_unstable();
        unsorted += usize::from(params != alphabetical);
    }
    assert!(unsorted > 0, "no query declares its parameters unsorted");
}
