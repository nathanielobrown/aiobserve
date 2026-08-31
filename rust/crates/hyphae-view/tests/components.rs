//! The escaping contract a component rests on, held rather than remembered.
//!
//! Every other leaf reads a served page. These two read one component, because what they pin is
//! the library's rule rather than the viewer's: an upgrade that changed it would change every
//! page at once and no page test would say which rule moved.

use hyphae_store::queries;
use hyphae_view::components::parts;
use hyphae_view::highlight::{Syntax, lit};

#[test]
fn an_attribute_is_escaped_even_when_its_value_is_already_markup() {
    // The behaviour that inverts the rule everywhere else: markup in a child position passes
    // through untouched, and the same value in an attribute is escaped like any other string.
    // The whole attribute-position rule rests on it.
    let served = parts::code("SELECT 1", Syntax::Sql, "<b>&</b>").into_inner();
    assert!(
        served.contains(r#"data-field="&lt;b&gt;&amp;&lt;/b&gt;""#),
        "{served}",
    );
    assert!(!served.contains("<b>&</b>"), "{served}");
}

#[test]
fn a_markup_child_reaches_the_page_as_the_markup_its_producer_made() {
    // The other half of the rule: what `highlight::lit` marked up is not escaped again.
    //
    // Real material, and the material this component actually renders in production — a query
    // file this build ships, marked up by the producer the query page hands to it. A hand-built
    // `Markup` would prove that the library honours the type; this proves the producer still
    // makes one.
    let statement = queries::load("view_sessions");
    let shown = lit(Some(statement), Syntax::Sql);
    // The producer really made markup out of it, so there is something here to escape...
    assert!(shown.html.contains("<span"), "{}", shown.html);
    // ...and every byte of it reaches the page as markup rather than as visible tag text.
    let served = parts::code(statement, Syntax::Sql, "sql").into_inner();
    assert!(served.contains(&shown.html), "{served}");
    assert!(!served.contains("&lt;span"), "{served}");
}
