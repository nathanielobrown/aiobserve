//! The escaping contract a component rests on, held rather than remembered.
//!
//! Every other leaf reads a served page. These two read one component, because what they pin is
//! the library's rule rather than the viewer's: an upgrade that changed it would change every
//! page at once and no page test would say which rule moved.

use hyphae_view::components::parts;
use hyphae_view::highlight::Syntax;

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
fn a_value_a_component_marked_up_reaches_the_page_as_markup() {
    // The other half: what the highlighter wrote is elements by the time it is a child, so a
    // component that escaped it again would print a reader the tags.
    let served = parts::code("a < b", Syntax::Sql, "value").into_inner();
    assert!(served.contains("a &lt; b"), "{served}");
    assert!(served.contains("<pre data-field=\"value\""), "{served}");
}
