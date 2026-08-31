//! What Rust's `hp query` prints against what Python's prints, query by query.
//!
//! The two runners bind the same SQL library over the same store file, so anything they
//! disagree about is this side's: a binding it resolved differently, a value it printed
//! differently, a citation in another order. Every corpus-scoped query runs, at its
//! production defaults, because those are the numbers a report quotes.
//!
//! It runs by default, because a drift nobody runs the check for is a drift nobody finds.
//! Set `HYPHAE_SKIP_PYTHON_PARITY` on a machine with no Python environment — `mise run
//! rust-check` is meant to work there. This leaf and `hyphae-enrich/tests/parity.rs` are the
//! two that shell into `uv`.
//!
//! **Nothing here prints a stored value.** A mismatch names the query, the line and the
//! column and stops: the corpus is recorded sessions, and a failure that dumped a row would
//! put transcript text in a CI log.

use std::process::Command;

use hyphae_testsupport::{cache, corpus, landmarks};

mod common;

/// The escape hatch, named so a failure can point at it.
const SKIP: &str = "HYPHAE_SKIP_PYTHON_PARITY";

/// The date the window is measured back from. Pinned rather than left to today, so the two
/// sides cannot differ by the second that passed between them — and far enough back that the
/// trailing window still covers the corpus (`tests/analyze/conftest.py:AS_OF_WHOLE`).
const AS_OF: &str = "2026-07-28";

/// What a corpus query needs bound before it answers anything, beyond its own defaults.
///
/// Only the two the manifest marks required. Everything else runs at its production default,
/// which is the point: those are the values a report quotes.
const BOUND: [(&str, &[&str]); 2] = [
    // Where to split the fixture corpus's two idle reloads, which followed silences of 6,035
    // and 23,773 seconds: anything between them puts one on each side of the bound.
    ("reload_cost_split", &["short_gap_seconds=10000"]),
    ("select_enrichments", &["level=turn"]),
];

/// Run the same command line through the Python CLI and hand back what each stream got.
///
/// `hyphae.cli.main` in process rather than one `uv run` per query: the seam is the CLI, and
/// twenty-two spawns of a Python interpreter would cost more than the rest of this file.
const DRIVE: &str = r#"
import contextlib, io, json, sys
from hyphae import cli

db, project, as_of, plan = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
answers = []
for name, extra, csv in plan:
    argv = ["query", name, "--db", db, "--project", project, "--as-of", as_of]
    argv += ["--csv"] if csv else []
    for pair in extra:
        argv += ["--param", pair]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        cli.main(*argv)
    answers.append({"stdout": out.getvalue(), "stderr": err.getvalue()})
json.dump(answers, sys.stdout)
"#;

#[test]
fn every_corpus_query_prints_what_python_prints() {
    if std::env::var_os(SKIP).is_some() {
        return;
    }
    let db = cache::enriched_store();
    let plan = plan();
    // Both renderings of every corpus query: the CSV a piped analysis reads, and the aligned
    // table a person does. They share the cell renderer and differ in everything else.
    assert!(plan.len() > 40, "{} runs is too few", plan.len());
    let theirs = drive_python(&db, &plan);
    assert_eq!(theirs.len(), plan.len(), "Python answered a different plan");

    for (run, said) in plan.iter().zip(&theirs) {
        let (name, extra, csv) = run;
        let mut argv: Vec<String> = vec![
            "query".into(),
            name.clone(),
            "--db".into(),
            db.display().to_string(),
            "--project".into(),
            landmarks::MYCELIA.into(),
            "--as-of".into(),
            AS_OF.into(),
        ];
        if *csv {
            argv.push("--csv".into());
        }
        for pair in extra {
            argv.extend(["--param".to_owned(), pair.clone()]);
        }
        let mine = common::hp(&argv);
        assert!(mine.ok, "{name} refused — {}", mine.stderr);
        // The citation and the excluded-sessions line, which say what was bound and what the
        // corpus left out. A whole-string compare: neither carries a stored value.
        assert_eq!(mine.stderr, said.stderr, "{name} says something else aside");
        assert_lines_equal(name, *csv, &mine.stdout, &said.stdout);
    }
}

/// Every corpus query, with whatever [`BOUND`] says it needs — discovery, not a list, so a
/// query added to the library is compared the day it lands.
fn plan() -> Vec<(String, Vec<String>, bool)> {
    hyphae_store::manifest::manifest()
        .iter()
        .filter(|(_, query)| query.scope == hyphae_store::manifest::Scope::Corpus)
        .flat_map(|(name, _)| {
            let extra: Vec<String> = BOUND
                .iter()
                .find(|(bound, _)| bound == name)
                .map(|(_, pairs)| pairs.iter().map(|pair| (*pair).to_owned()).collect())
                .unwrap_or_default();
            [true, false].map(|csv| (name.clone(), extra.clone(), csv))
        })
        .collect()
}

/// One result against another, line by line, printing nothing either holds.
///
/// The header is compared as text — column names are the library's own words, not the
/// corpus's. Below it, a difference is reported as a line and a field number.
fn assert_lines_equal(name: &str, csv: bool, mine: &str, theirs: &str) {
    // `csv.writer`'s `excel` dialect ends every record `\r\n`; the table is plain lines.
    let ending = if csv { "\r\n" } else { "\n" };
    let (mine, theirs): (Vec<&str>, Vec<&str>) =
        (mine.split(ending).collect(), theirs.split(ending).collect());
    assert_eq!(
        mine.first(),
        theirs.first(),
        "{name} answers different columns"
    );
    assert_eq!(
        mine.len(),
        theirs.len(),
        "{name} answers {} line(s) where Python answers {}",
        mine.len(),
        theirs.len()
    );
    for (at, (ours, yours)) in mine.iter().zip(&theirs).enumerate().skip(1) {
        if ours == yours {
            continue;
        }
        let separator = if csv { ',' } else { ' ' };
        let column = ours
            .split(separator)
            .zip(yours.split(separator))
            .position(|(one, two)| one != two)
            .map_or("a field count".to_owned(), |at| format!("field {at}"));
        panic!("{name} line {at}: {column} differs");
    }
}

/// What Python's `hp query` printed for one planned run.
struct Said {
    stdout: String,
    stderr: String,
}

/// Run the whole plan through the Python CLI, in the plan's own order.
fn drive_python(db: &std::path::Path, plan: &[(String, Vec<String>, bool)]) -> Vec<Said> {
    let written = serde_json::to_string(plan).expect("the plan serializes");
    let done = Command::new("uv")
        .args(["run", "--project"])
        .arg(corpus::repo())
        .args(["python", "-c", DRIVE])
        .arg(db)
        .arg(landmarks::MYCELIA)
        .arg(AS_OF)
        .arg(&written)
        .current_dir(corpus::repo())
        .output()
        .expect("uv runs the Python CLI");
    assert!(
        done.status.success(),
        "the Python side failed: {}",
        String::from_utf8_lossy(&done.stderr)
    );
    let answers: Vec<serde_json::Value> =
        serde_json::from_slice(&done.stdout).expect("the Python side answers JSON");
    answers
        .into_iter()
        .map(|answer| Said {
            stdout: answer["stdout"].as_str().expect("stdout").to_owned(),
            stderr: answer["stderr"].as_str().expect("stderr").to_owned(),
        })
        .collect()
}
