//! What the stylesheet draws a context bar as: the classes carrying the steps, and the hues.
//!
//! The bars themselves are read against the store in `nav_tree_bars.rs`; what those leaves cannot
//! see is whether a step the markup can carry is a width anything paints.

use axum::http::StatusCode;
use hyphae_testsupport::served::Served;
use hyphae_view::nodes::BAR_STEPS;
use regex::Regex;

#[tokio::test]
async fn a_context_bar_is_drawn_by_three_families_of_class_one_rule_spends() {
    // What three edges are drawn as: a track, the fill, and the two bands nested inside it.
    //
    // The policy forbids the inline width that would carry a percentage, so the numbers ride in as
    // classes and the stylesheet turns them back into widths. That makes the ladder a thing this
    // tier can read: every step the markup can carry has to be a width here, or a row lands on a
    // class that draws nothing and the bar quietly reads as empty.
    let style = stylesheet().await;
    let steps: Vec<i64> = (0..=BAR_STEPS).collect();
    for (family, property) in [
        ("f", "--ctx-fill"),
        ("p", "--ctx-prior"),
        ("b", "--ctx-base"),
    ] {
        let widths: Vec<(i64, i64)> = found(
            &format!(r"li\.node\.{family}(\d+) \{{ {property}: (\d+)%"),
            &style,
        )
        .into_iter()
        .map(|pair| (numbered(&pair[0]), numbered(&pair[1])))
        .collect();
        // Every family runs the whole ladder, bottom to top: a band of nothing is a drawn track
        // with nothing in it, and a full window is the bar's own end.
        let mut named: Vec<i64> = widths.iter().map(|(step, _)| *step).collect();
        named.sort_unstable();
        assert_eq!(named, steps, "{family}");
        // And they are linear, evenly spaced from empty to full — the bar's whole claim is that
        // half of it is half a window.
        let mut drawn = widths.clone();
        drawn.sort_unstable();
        assert_eq!(
            drawn.iter().map(|(_, width)| *width).collect::<Vec<_>>(),
            steps
                .iter()
                .map(|step| step * 100 / BAR_STEPS)
                .collect::<Vec<_>>(),
            "{family}"
        );
    }
    // One rule spends all three, layering the track under the fill under the two bands that stand
    // inside it. Each band is a prefix drawn over the one below, so the ground a reader sees
    // between two edges is the band the second one opens.
    let spent = only(found(r"(li\.node:is\([^)]*\)) > a \{([^}]*)\}", &style));
    let (selector, body) = (spent[0].clone(), spent[1].clone());
    assert!(
        Regex::new(
            &[
                r"var\(--ctx-base, 0%\) 3px,",
                r"\s*var\(--ctx-prior, 0%\) 3px,",
                r"\s*var\(--ctx-fill, 0%\) 3px,",
                r"\s*100% 3px",
            ]
            .concat(),
        )
        .expect("a pattern")
        .is_match(&body),
        "{body}"
    );
    // The tip's own colour is the one thing a kind may take over, so it is a property with the
    // accent as its default rather than a colour written into the layer.
    assert_eq!(
        found(r"var\(--(faint|dim|ctx-tip|line)", &body)
            .into_iter()
            .map(|token| token[0].clone())
            .collect::<Vec<_>>(),
        [
            "faint", "faint", "dim", "dim", "ctx-tip", "ctx-tip", "line", "line"
        ],
        "{body}"
    );
    assert_eq!(
        body.matches("var(--ctx-tip, var(--mark))").count(),
        2,
        "{body}"
    );
    // Every fill class the markup can carry is named by that rule, and so is the mark a run whose
    // thread compacted carries: a step outside it would set a width nothing reads.
    let mut named: Vec<i64> = found(r"\.f(\d+)", &selector)
        .into_iter()
        .map(|step| numbered(&step[0]))
        .collect();
    named.sort_unstable();
    assert_eq!(named, steps, "{selector}");
    assert!(selector.contains(".maxed"), "{selector}");
    // A band alone draws nothing: a row that names where its own share begins without naming where
    // it left the window has no bar to put the band in, and the fill carries the track.
    assert!(
        found(r"li\.node:is\([^)]*\.[pb]\d+", &style).is_empty(),
        "a band is drawn without a fill"
    );
}

#[tokio::test]
async fn a_run_a_compaction_and_a_maxed_thread_each_take_the_tip_in_a_colour_of_their_own() {
    // The three hues beside the accent, and the one row that is drawn full whatever it holds.
    //
    // A hue is keyed on the row's own kind, which the row already carries — a second class saying
    // `run` on a run would be eight bytes a row for what the markup says already. What no kind can
    // say is that a run's own thread compacted, and that is the one mark the bar mints: the window
    // it ran out of, drawn full in the alarm the rest of the viewer flags an error with.
    //
    // The colours themselves are eyeballed on the gallery (`.claude/rules/viewer-ui.md`); what
    // this holds is that they are three different tokens, and that each is defined in both
    // schemes — a token a dark page leaves unset is a band that vanishes for half the readers.
    let style = stylesheet().await;
    let tips: Vec<(String, String)> = found(
        r"li\.node\.(\w+) > a \{[^}]*--ctx-tip: var\((--[\w-]+)\)",
        &style,
    )
    .into_iter()
    .map(|pair| (pair[0].clone(), pair[1].clone()))
    .collect();
    let mut kinds: Vec<&str> = tips.iter().map(|(kind, _)| kind.as_str()).collect();
    kinds.sort_unstable();
    assert_eq!(kinds, ["compaction", "maxed", "run"], "{tips:?}");
    // Three hues, none of them the accent a turn or a call draws its tip in.
    let mut hues: Vec<&str> = tips.iter().map(|(_, hue)| hue.as_str()).collect();
    hues.sort_unstable();
    hues.dedup();
    assert_eq!(hues.len(), tips.len(), "{tips:?}");
    assert!(!hues.contains(&"--mark"), "{tips:?}");
    // A maxed row is the whole track: a run that filled its window says so at full width, whatever
    // the last call of its thread happened to leave behind.
    let maxed = only(
        found(r"li\.node\.(\w+) > a \{([^}]*)\}", &style)
            .into_iter()
            .filter(|rule| rule[0] == "maxed")
            .collect(),
    );
    assert!(
        maxed[1].contains("--ctx-fill: 100%") && maxed[1].contains("--ctx-prior: 0%"),
        "{}",
        maxed[1]
    );
    // And every token the bar spends is defined for both schemes, light and dark alike.
    let dark = only(found(
        r"(?s)@media \(prefers-color-scheme: dark\) \{([^}]*)\}",
        &style,
    ));
    for token in hues
        .iter()
        .copied()
        .chain(["--faint", "--dim", "--mark", "--line"])
    {
        assert!(
            Regex::new(&format!(r"(?m)^\s*{token}: #"))
                .expect("a pattern")
                .is_match(&style),
            "{token}"
        );
        assert!(dark[0].contains(&format!("{token}: #")), "{token}");
    }
}

/// The served stylesheet with its comments taken out, which is what these leaves read.
async fn stylesheet() -> String {
    let (status, style) = Served::corpus().page("/static/style.css").await;
    assert_eq!(status, StatusCode::OK);
    Regex::new(r"(?s)/\*.*?\*/")
        .expect("a pattern")
        .replace_all(&style, "")
        .into_owned()
}

/// Every match of a pattern, as its capture groups.
fn found(pattern: &str, text: &str) -> Vec<Vec<String>> {
    Regex::new(pattern)
        .expect("a pattern")
        .captures_iter(text)
        .map(|caught| {
            caught
                .iter()
                .skip(1)
                .map(|group| group.expect("a group matched").as_str().to_owned())
                .collect()
        })
        .collect()
}

/// The single match a stylesheet leaf reads, so a second one is a failure and not a pick.
fn only<T>(found: Vec<T>) -> T {
    let [one] = <[T; 1]>::try_from(found).unwrap_or_else(|found| {
        panic!("one match, not {}", found.len());
    });
    one
}

/// A captured group read as the number it spells.
fn numbered(caught: &str) -> i64 {
    caught.parse().expect("a number")
}
