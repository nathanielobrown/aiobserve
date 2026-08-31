//! The dual window and the ISO-week trend, against a frozen store with `$as_of` bound.
//!
//! The twin of `tests/analyze/test_windows.py`. Every number a report quotes is quoted in
//! these two windows, so the leaves here check the arithmetic that relates them: the trailing
//! window is the corpus restricted, and the weeks partition the corpus.
//!
//! Nothing here can read a clock — [`hyphae_analyze::Request`] has no default `as_of`, so a
//! query left unbound will not compile rather than quietly windowing against today. That is
//! what `tests/analyze/test_windows.py`'s far-future guard buys by patching; the leaf holding
//! the CLI to the same thing, where the default does exist, is
//! `hp/tests/query.rs::as_of_defaults_to_today`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use hyphae_store::Row;
use hyphae_testsupport::{cache, landmarks, windows};

mod common;

use common::{by, corpus, number};

/// The two rows `session_counts` answers with.
const CORPUS: &str = "corpus";
const TRAILING: &str = "trailing_window";
/// The bucket `weekly_trend` puts a session with no start time in.
const UNDATED: &str = "undated";

/// `session_counts` rows keyed by period: the corpus row and the trailing-window row.
fn periods(db: &Path, as_of: &str) -> BTreeMap<String, Row> {
    by(&corpus(db, "session_counts", as_of, &[]), "period")
}

/// `weekly_trend` rows keyed by ISO week label. The trend is not windowed, so every caller
/// reads it at the same `$as_of`.
fn weeks(db: &Path) -> BTreeMap<String, Row> {
    by(
        &corpus(db, "weekly_trend", windows::AS_OF_WHOLE, &[]),
        "week",
    )
}

/// `sessions` rows keyed by session id.
fn listing(db: &Path, as_of: &str) -> BTreeMap<String, Row> {
    by(&corpus(db, "sessions", as_of, &[]), "session_id")
}

#[test]
fn the_trailing_window_is_the_corpus_restricted() {
    let db = cache::corpus_store();
    // If the corpus holds 15 mycelia sessions and the 28 days back from 2026-08-07 hold 8...
    let counts = periods(&db, windows::AS_OF_PARTIAL);
    assert_eq!(
        counts[CORPUS].i64("sessions").expect("a count"),
        windows::MYCELIA_SESSIONS as i64
    );
    assert_eq!(
        counts[TRAILING].i64("sessions").expect("a count"),
        windows::IN_WINDOW_AT_PARTIAL as i64
    );

    // ...then the sessions query, written separately, marks exactly those 8 in window...
    let listing = listing(&db, windows::AS_OF_PARTIAL);
    let windowed: BTreeSet<&str> = listing
        .iter()
        .filter(|(_, row)| row.bool("in_window").expect("a flag"))
        .map(|(session, _)| session.as_str())
        .collect();
    assert_eq!(windowed.len(), windows::IN_WINDOW_AT_PARTIAL);

    // ...and they are a subset of the corpus, not a differently drawn set: two queries that
    // disagree here put two numbers in one report that cannot both be true.
    let all: BTreeSet<&str> = listing.keys().map(String::as_str).collect();
    assert!(windowed.is_subset(&all) && windowed != all);

    // ...and every count the window reports is bounded by the corpus count it restricts.
    for column in counts[CORPUS]
        .columns()
        .iter()
        .filter(|name| *name != "period")
    {
        let (Some(whole), Some(part)) = (
            number(&counts[CORPUS], column),
            number(&counts[TRAILING], column),
        ) else {
            continue;
        };
        assert!(part <= whole, "{column}: {part} is not within {whole}");
    }
}

#[test]
fn iso_weeks_partition_the_corpus() {
    // If the corpus spans five unevenly filled weeks, 2026-W27 through W31...
    let weeks = weeks(&cache::corpus_store());
    let counted: Vec<(&str, usize)> = weeks
        .iter()
        .map(|(week, row)| {
            (
                week.as_str(),
                usize::try_from(row.i64("sessions").expect("a count")).expect("a count fits"),
            )
        })
        .collect();
    assert_eq!(counted, windows::WEEKS.to_vec());

    // ...then their sessions sum to the corpus total, which is what makes each week a share
    // of one whole rather than an independently filtered count.
    let total: usize = windows::WEEKS.iter().map(|(_, count)| count).sum();
    assert_eq!(total, windows::MYCELIA_SESSIONS);
}

#[test]
fn a_session_with_no_start_time_lands_in_a_bucket_that_names_itself() {
    let db = cache::corpus_store();
    // If the one recorded session with no `started_at` also has no `project_dir`, the corpus
    // predicate excludes it and no bucket mentions it...
    assert!(!weeks(&db).contains_key(UNDATED));
    assert!(
        !listing(&db, windows::AS_OF_WHOLE).contains_key(landmarks::NO_PROJECT_SESSION),
        "the corpus predicate cannot place a session with no project"
    );

    // ...but with a `project_dir` planted on it, the predicate does place it, and the trend
    // names it `undated` rather than silently dropping it into a NULL week...
    let (_scratch, planted) = common::planted(|store| {
        // The planted value is invented; the rest of the session is the recorded trace. The
        // Python twin re-extracts the fixture with the field replaced, which lands the same
        // one column on the same row — this writes the column, because the store already
        // holds every other row of that session.
        store
            .connection()
            .execute(
                "UPDATE sessions SET project_dir = ? WHERE id = ?",
                [landmarks::MYCELIA, landmarks::NO_PROJECT_SESSION],
            )
            .expect("the copy takes the planted project");
    });
    let planted = weeks(&planted);
    assert_eq!(planted[UNDATED].i64("sessions").expect("a count"), 1);

    // ...so the partition still holds: the buckets sum to the corpus count, one higher than
    // before. A session the trend cannot date is a session the reader can still see.
    let total: i64 = planted
        .values()
        .map(|row| row.i64("sessions").expect("a count"))
        .sum();
    assert_eq!(total, windows::MYCELIA_SESSIONS as i64 + 1);
}

#[test]
fn as_of_alone_decides_the_window() {
    let db = cache::corpus_store();
    // If `$as_of` moves from 2026-07-28, which opens the window before the earliest session,
    // to 2026-08-07, which opens it mid-corpus...
    let whole = periods(&db, windows::AS_OF_WHOLE);
    let partial = periods(&db, windows::AS_OF_PARTIAL);

    // ...then the window covers 15 sessions and then 8, off one frozen store...
    assert_eq!(
        whole[TRAILING].i64("sessions").expect("a count"),
        windows::MYCELIA_SESSIONS as i64
    );
    assert_eq!(
        partial[TRAILING].i64("sessions").expect("a count"),
        windows::IN_WINDOW_AT_PARTIAL as i64
    );

    // ...while the corpus row, which no window touches, does not move.
    assert_eq!(whole[CORPUS].values(), partial[CORPUS].values());

    // ...and moving `$as_of` back inside the corpus closes the window's far edge too:
    // 2026-07-19 still opens before the earliest session, so the only sessions it can drop
    // are the three recorded after that day...
    let mid = periods(&db, windows::AS_OF_MID);
    assert_eq!(
        mid[TRAILING].i64("sessions").expect("a count"),
        windows::IN_WINDOW_AT_MID as i64
    );

    // ...and the sessions listing agrees on which three: out of window is exactly started
    // after `$as_of`, while the session recorded at 20:27 that same evening stays in, because
    // the bound runs to the end of `$as_of`'s day. Drop the bound and a window rebound to an
    // earlier date quietly reports sessions from its own future.
    let listing = listing(&db, windows::AS_OF_MID);
    let excluded: BTreeSet<&str> = listing
        .iter()
        .filter(|(_, row)| !row.bool("in_window").expect("a flag"))
        .map(|(session, _)| session.as_str())
        .collect();
    let after: BTreeSet<&str> = listing
        .iter()
        .filter(|(_, row)| {
            row.timestamp("started_at").expect("a start").date_naive()
                > windows::date(windows::AS_OF_MID)
        })
        .map(|(session, _)| session.as_str())
        .collect();
    assert_eq!(excluded, after);
    assert_eq!(
        excluded.len(),
        windows::MYCELIA_SESSIONS - windows::IN_WINDOW_AT_MID
    );
}
