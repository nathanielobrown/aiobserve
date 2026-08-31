//! Each small part rendered on its own: the spaces it owes, and where markup may go.
//!
//! A part is a function, so it can be called with the view-model it takes and read straight —
//! no app, no store, no route. What every page built out of these finally serves is the rest of
//! this tier's business.
//!
//! The centre of gravity is **spaces**. `hypertext` emits nothing between elements, so every
//! space a reader sees is a `" "` somebody wrote on purpose. A leaf here reads the rendered text
//! back through `html::plain` and asserts the gap, because `0 errors` and `0errors` are the same
//! string to every `data-*` reader in the suite.

use hyphae_testsupport::html::{self, Markup as Read};
use hyphae_testsupport::landmarks::SPINE;

use chrono::{TimeZone, Utc};
use hypertext::prelude::*;
use hyphae_enrich::schema::Level;
use hyphae_store::Param;
use hyphae_store::queries;
use hyphae_view::citation::cited;
use hyphae_view::components::{Markup, citation, parts};
use hyphae_view::detail::{Detail, EnrichmentLines};
use hyphae_view::enrichment::{Enrichment, TAXONOMY_VERSION};
use hyphae_view::highlight::Syntax;
use hyphae_view::nav_tree::Bound;
use regex::Regex;

/// One enrichment as a pane reads it, current on both versions this build writes.
fn described() -> Enrichment {
    Enrichment {
        level: Level::Turn,
        item_id: "turn-7".to_owned(),
        description: "Read the extractor and fixed the offload path.".to_owned(),
        description_chars: 46,
        category: "implementation".to_owned(),
        outcome: "completed".to_owned(),
        friction: None,
        friction_chars: None,
        model: "claude-opus-4".to_owned(),
        enriched_at: Some(Utc.with_ymd_and_hms(2026, 3, 1, 0, 0, 0).unwrap()),
        prompt_version: Level::Turn.prompt_version(),
        taxonomy_version: TAXONOMY_VERSION,
    }
}

/// One fat value as a pane previews it, with `cut` characters left behind it.
fn line(name: &str, head: &str, cut: i64) -> Detail {
    Detail {
        name: name.to_owned(),
        head: head.to_owned(),
        cut,
        url: format!("/fragment/{name}"),
        syntax: None,
        markdown: false,
    }
}

/// What a part rendered, as the string every assertion below reads.
fn served(markup: Markup) -> String {
    markup.into_inner()
}

/// The same for a part that renders nothing where it has nothing to say.
fn some(markup: Option<Markup>) -> String {
    served(markup.expect("the part rendered"))
}

// --- The spaces the renderer stopped emitting ---------------------------------------------

#[test]
fn a_stacked_cell_holds_one_space_between_its_secondary_and_the_unit_word() {
    // The gap a template used to write as a literal, which a formatter could have dropped. The
    // unit sits outside the labelled span — a `data-field` carries the value the store holds and
    // nothing else — so without a `" "` of its own the page reads `3errors`.
    let cell = served(parts::stacked(parts::Stacked {
        field: "error_rate",
        primary: "4%",
        secondary_field: "tool_errors",
        secondary: "3",
        unit: Some("errors"),
        primary_mark: None,
        secondary_mark: None,
    }));
    // The unit is outside the labelled span, and one space stands between the two...
    assert!(cell.contains("</span> errors</span>"), "{cell}");
    assert!(html::plain(&cell).contains("3 errors"), "{cell}");
}

#[test]
fn a_stacked_cell_with_no_unit_ends_at_its_number() {
    // The converse: a cell whose secondary needs no word owes no space either. The space rides
    // inside the part rather than beside it, so the two `when` columns — which print a timestamp
    // under a duration and name no unit — do not end in a stray gap.
    let cell = served(parts::stacked(parts::Stacked {
        field: "ago",
        primary: "2 days ago",
        secondary_field: "last_active",
        secondary: "2026-03-01 09:00",
        unit: None,
        primary_mark: None,
        secondary_mark: None,
    }));
    assert!(cell.ends_with("09:00</span></span>"), "{cell}");
}

#[test]
fn a_glyph_carries_the_space_after_it_and_a_plain_title_carries_none() {
    // The mark on a model-written title, and the gap between it and the title it marks. Both
    // halves matter: the space is inside the part, so a row for something no pass described
    // renders nothing at all rather than a lone space the NavTree would pay 3,217 times for.
    assert_eq!(
        some(parts::glyph(true)),
        format!(
            r#"<span class="{}">{}</span> "#,
            parts::GLYPH_CLASS,
            parts::GLYPH
        )
    );
    assert!(parts::glyph(false).is_none());
}

#[test]
fn a_counted_list_spaces_each_count_off_its_name_and_commas_the_rest() {
    // `Task ×3, Explore ×1` — the spacing a template wrote across two source lines. Read as text
    // rather than as markup: `counted` writes no elements of its own, so a lost space here is
    // invisible to everything else in the suite.
    let entries = [
        parts::Count {
            name: "Task".to_owned(),
            count: 3,
        },
        parts::Count {
            name: "Explore".to_owned(),
            count: 1,
        },
    ];
    assert_eq!(
        html::plain(&served(parts::counted(&entries, true))),
        "Task ×3, Explore ×1"
    );
}

#[test]
fn the_tail_of_a_cut_list_opens_with_a_space() {
    // `and 4 more` follows a list on the same line, so the gap belongs to the tail.
    assert_eq!(html::plain(&some(parts::more(4))), " and 4 more");
    // And a list the query did not cut says nothing, rather than saying it left out none.
    assert!(parts::more(0).is_none());
}

// --- The opt-outs, which are the reason these take a flag ----------------------------------

#[test]
fn a_counted_list_of_closed_vocabulary_marks_no_name_it_prints() {
    // A taxonomy value is cut at a width its own words cannot reach, so a mark would lie. The
    // name is passed at its full length either way; what changes is whether the cut mark can
    // appear. Proven with a name past the cut, so the two arms differ.
    let long = "a".repeat(queries::LIST_ITEM_CHARS + 10);
    let entries = [parts::Count {
        name: long.clone(),
        count: 2,
    }];
    let marked = html::plain(&served(parts::counted(&entries, true)));
    let whole = html::plain(&served(parts::counted(&entries, false)));
    // The marked arm stopped the name and said so; the closed-vocabulary arm printed it whole.
    assert!(marked.chars().count() < whole.chars().count(), "{marked}");
    assert!(whole.starts_with(&long), "{whole}");
}

#[test]
fn a_fact_prints_the_dash_the_viewer_prints_for_a_column_the_store_left_null() {
    // A header names its fields whether or not the session filled them.
    let fact = served(parts::fact("git_branch", None));
    assert!(
        fact.contains(r#"<dd data-field="git_branch">—</dd>"#),
        "{fact}"
    );
}

#[test]
fn a_fact_reads_its_label_off_its_value_with_a_space_between_them() {
    // `Cost $1.48`, never `Cost$1.48` — the one gap the stylesheet is not the only thing holding.
    // A `<dt>` and the `<dd>` beside it are two elements, so nothing is written between them and
    // a reader whose stylesheet never arrived meets the label welded to the number.
    assert_eq!(
        html::plain(&served(parts::fact("cost_usd", Some("$1.48")))),
        "Cost $1.48"
    );
}

#[test]
fn a_fact_whose_value_is_composed_carries_the_markup_the_caller_built() {
    // The mount for a value no formatter makes: a list, and the count of what its query cut. The
    // `<dl>` shape is `fact`'s — one place decides what a labelled fact looks like — and what
    // changes is that the caller hands markup rather than a string.
    let composed = rsx! { <span>"commit, pr"</span> }.memoize();
    let fact = served(parts::labelled("skills", composed));
    assert_eq!(html::plain(&fact), "Skills commit, pr");
    assert!(
        fact.contains(r#"<dd data-field="skills"><span>commit, pr</span></dd>"#),
        "{fact}"
    );
}

// --- Where markup may go, and where it may not ---------------------------------------------

#[test]
fn prose_renders_the_markdown_a_session_wrote_rather_than_printing_it() {
    // `view::render` owns the escaping, and its `Markup` reaches the page as markup. The other
    // half of the rule the package holds: a component constructs no `Markup` and consumes the
    // ones the producers make.
    let written = served(parts::prose("brief", Some("Read **schema.md** first.")));
    assert!(
        Read::of(&written)
            .prose("brief")
            .contains("<strong>schema.md</strong>"),
        "{written}"
    );
}

#[test]
fn a_detail_reads_its_head_the_one_way_its_row_said_the_value_was_written() {
    // Three arms, one flag each: a syntax the record named, markdown, or the stored bytes. A
    // value cannot be two of them — the same flag decides how it is marked up and whether the
    // pane walls it as a quotation — so the classes are asserted beside the markup.
    let lit_up = served(parts::detail(&Detail {
        syntax: Some(Syntax::Json),
        ..line("input", r#"{"a": 1}"#, 0)
    }));
    let written = served(parts::detail(&Detail {
        markdown: true,
        ..line("brief", "Read **schema.md**.", 0)
    }));
    let stored = served(parts::detail(&line("result", "plain output", 0)));
    // The syntax the row named re-lays the value out for reading rather than printing the one
    // line the store holds. The `code json` wall and the lexer's classes under it arrive with
    // the highlighter itself, which `view::highlight` does not paint yet.
    assert_eq!(
        html::plain(&Read::of(&lit_up).block("input")),
        "{\n  \"a\": 1\n}"
    );
    // ...markdown is rendered and walled as the quotation it is...
    assert!(
        Read::of(&written)
            .prose("brief")
            .contains("<strong>schema.md</strong>"),
        "{written}"
    );
    assert!(written.contains(r#"class="detail quoted""#), "{written}");
    // ...and everything else is the characters the store holds, in an unclassed block.
    assert_eq!(Read::of(&stored).walled("result"), "");
    assert_eq!(
        html::plain(&Read::of(&stored).block("result")),
        "plain output"
    );
}

#[test]
fn a_cut_value_offers_the_rest_of_itself_where_the_head_stood() {
    // The one place a fat column crosses the wire whole, in both blocks that preview one. A
    // detail swaps its whole section and an enrichment line swaps its own span; the targets
    // differ and the offer does not, which is why one function writes both links.
    let detail = served(parts::detail(&line("result", "head", 900)));
    let written = some(parts::enrichment_line(Some(&line(
        "description",
        "head",
        40,
    ))));
    assert_eq!(Read::of(&detail).values("hx-target"), ["closest .detail"]);
    assert_eq!(
        Read::of(&written).values("hx-target"),
        ["closest .enrichment-line"]
    );
    // Each says how much is behind the head, and each opens with the space that follows it.
    assert!(
        html::plain(&detail).contains("+900 more character(s)"),
        "{detail}"
    );
    assert!(
        html::plain(&written).contains(" +40 more character(s)"),
        "{written}"
    );
    // A value the query did not cut offers nothing.
    let whole = served(parts::detail(&line("result", "head", 0)));
    assert!(!whole.contains("hx-get"), "{whole}");
}

#[test]
fn a_stored_value_that_reads_as_markup_is_printed_rather_than_obeyed() {
    // The escaping every component gets for free, asserted where store text reaches a page. A
    // transcript can hold anything the agent read, so a branch named `<script>` has to come back
    // out as the characters it is. What a component owes is to hand values in as children and
    // never to build a `Markup` around one.
    let fact = served(parts::fact("git_branch", Some("<script>alert(1)</script>")));
    assert!(!fact.contains("<script>"), "{fact}");
    assert!(
        html::plain(&fact).ends_with("<script>alert(1)</script>"),
        "{fact}"
    );
}

#[test]
fn a_summary_stands_the_provenance_on_the_glyph_and_the_words_beside_it() {
    // What a pass wrote, under the mark that says a model wrote it and who. The glyph carries the
    // provenance because the pane is the one surface with room for it; a NavTree row carries the
    // mark alone.
    let about = described();
    let lines = EnrichmentLines {
        description: Some(line("description", &about.description, 0)),
        friction: None,
    };
    let summary = served(parts::summary(&about, &lines));
    assert!(
        Read::of(&summary)
            .values("title")
            .contains(&about.provenance()),
        "{summary}"
    );
    // The words follow the glyph with a space between, and the closed vocabularies follow them.
    assert!(
        html::plain(&summary).contains(&format!("{} {}", parts::GLYPH, about.description)),
        "{summary}"
    );
    assert!(
        summary.contains(r#"data-field="category">implementation"#),
        "{summary}"
    );
    // Nothing said the row was stale, so no tag claims it was.
    assert!(!summary.contains("stale"), "{summary}");
}

// --- The parts no page consumes until a later slice ----------------------------------------

#[test]
fn an_unpriced_mark_appears_only_where_our_table_priced_nothing() {
    // A total missing calls is not what was spent, and the page has to say so. Outside the
    // labelled span either way, so a reader of `data-field="cost_usd"` gets the number the store
    // holds whether or not the mark is beside it.
    let marked = some(parts::unpriced(3));
    assert!(marked.starts_with("<sup "), "{marked}");
    assert_eq!(html::plain(&marked), "*");
    assert!(
        Read::of(&marked).values("title")[0].contains("3 call(s)"),
        "{marked}"
    );
    // A cost our table priced whole carries no mark at all.
    assert!(parts::unpriced(0).is_none());
}

#[test]
fn a_kind_mark_is_written_with_no_space_and_no_word_of_its_own() {
    // The mark stands for a word the markup around it already carries. Two claims, both about
    // bytes: no trailing space — every caller writes its own, and a byte here is 3,217 bytes of
    // NavTree — and no `title`, which would be the same word as often.
    assert_eq!(
        served(parts::mark("◆")),
        r#"<span class="icon" aria-hidden="true">◆</span>"#
    );
}

#[test]
fn a_cost_badge_carries_its_share_as_a_class_and_its_money_as_the_field() {
    // The two readings of one badge: what it is worth, and how deep its ground is drawn. The step
    // rides the class rather than a `data-*` of its own — the stylesheet is the only reader of it
    // — and the money rides the field, which is what a test asserting spend reads.
    let badge = served(parts::badge("warm-3", "cost_usd", 1.25));
    assert_eq!(Read::of(&badge).values("class"), ["badge warm-3"]);
    assert_eq!(html::plain(&badge), "$1.25");
}

#[test]
fn a_stacked_cell_hangs_each_mark_off_the_line_that_owns_what_it_qualifies() {
    // Two slots, because the two lists stack the value a mark belongs to at different heights.
    // The session list stacks output tokens under a cost and marks the cost; the projects landing
    // stacks a cost under a session count and marks the cost again. One slot would put the mark
    // on the wrong number for one of them.
    let cell = served(parts::stacked(parts::Stacked {
        field: "cost_usd",
        primary: "$3.10",
        secondary_field: "output_tokens",
        secondary: "900",
        unit: Some("out"),
        primary_mark: parts::unpriced(2),
        secondary_mark: Some(parts::mark("◆")),
    }));
    // The first mark closes the primary line before the secondary span opens...
    let marked = r#"<sup title="2 call(s) at a model our price table lacks">*</sup>"#;
    assert!(
        cell.contains(&format!("$3.10</span>{marked}<span")),
        "{cell}"
    );
    // ...and the second sits inside the secondary span, between its number and the unit word.
    assert!(
        cell.contains(r#">900</span><span class="icon" aria-hidden="true">◆</span> out</span>"#),
        "{cell}"
    );
}

// --- The two mounts of a page's provenance --------------------------------------------------

#[test]
fn a_footer_and_a_fragments_list_cite_a_query_the_same_way() {
    // What produced a page is written once and mounted twice: folded, and open. A page's footer
    // folds it away — it is provenance, not content — while an element swapped into someone
    // else's page has no footer to end and stands its lines open. The `<li>` used to be written
    // in both templates, which is two answers to one question, so the leaf is that the two mounts
    // carry the same lines.
    let header: Bound = vec![
        ("session_id", Param::Text(SPINE.to_owned())),
        ("head_chars", Param::Int(80)),
    ];
    let runs: Bound = vec![("session_id", Param::Text(SPINE.to_owned()))];
    let ran = [
        ("session".to_owned(), cited("view_session_header", &header)),
        ("runs".to_owned(), cited("view_runs", &runs)),
    ];
    let folded = some(citation::footer(&ran));
    let open = served(citation::listed(&ran));
    let item = Regex::new("<li>.*?</li>").expect("a pattern");
    let lines: Vec<&str> = item.find_iter(&folded).map(|at| at.as_str()).collect();
    // Two queries in, two lines out, and the same two either way...
    assert_eq!(lines.len(), ran.len());
    assert_eq!(
        lines,
        item.find_iter(&open)
            .map(|at| at.as_str())
            .collect::<Vec<_>>()
    );
    // ...with the fold the only thing that differs between the mounts...
    assert!(folded.contains("what produced this page"), "{folded}");
    assert!(!open.contains("what produced this page"), "{open}");
    // ...and a page that ran no query carrying no footer at all, where the open mount is part of
    // the element it was swapped in with and always has its count to show.
    assert!(citation::footer(&[]).is_none());
    assert!(
        served(citation::listed(&[])).contains(r#"data-citations="0""#),
        "an empty list still counts"
    );
}
