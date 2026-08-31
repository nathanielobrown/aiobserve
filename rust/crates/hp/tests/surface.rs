//! The command line's own surface: what each subcommand takes, and what runs it.
//!
//! The port of `tests/test_cli.py`. Every other file in this tier drives `hp` to reach the code
//! under it, so the flags those files happen to pass are covered and the rest are not. This is
//! the file that pins the surface whole — a flag renamed, a default moved, or a subcommand wired
//! to the wrong handler.
//!
//! Three deliberate differences from Python's table, each one the port's own convention rather
//! than a gap:
//!
//! 1. **A path default the machine decides is `None` at the parser and read at the run.**
//!    Python builds `--projects-root` with `DEFAULT_PROJECTS_ROOT` baked in; `hp` resolves
//!    `sessions::default_projects_root()` inside the handler, for the reason the clock is read
//!    per call (`plans/rust-prototype/full-port.md`, "Clocks") — nothing here is decided when
//!    the parser is constructed.
//! 2. **`--as-of` is `None` at the parser**, and `query::today()` supplies the default in
//!    `run_with`. `query.rs::as_of_defaults_to_today` is what pins the value; Python's twin is
//!    the ID that PR #25 owns, whose expectation reads the local date while `cli.py` reads UTC.
//! 3. **`--no-browser` is absent**, so `hp view` opens no tab. Recorded as a scope clause on
//!    `plans/rust-prototype/full-port.md` — the leaf below carries the rest of Python's
//!    `test_the_viewer_opens_a_browser_unless_the_run_says_not_to`.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use clap::{CommandFactory as _, Parser as _};
use hyphae_enrich::client::{DEFAULT_CONCURRENCY, DEFAULT_MODEL};
use hyphae_export::delivery::{DEFAULT_RATE, GENERIC};
use hyphae_export::otlp::DEFAULT_MAX_CHARS;
use hyphae_extract::sessions::{SUBAGENTS_DIR, TRANSCRIPT_SUFFIX, encode_project_path};
use hyphae_testsupport::landmarks::MYCELIA;
use hyphae_view::app::PORT;

use hp::{Cli, Command, DEFAULT_DB};

mod common;

use common::hp;

/// The project a surface row names. Relative, as a person types it: the store subcommands take
/// it as a filter over a corpus already extracted, the discovery ones as the corpus itself.
const PROJECT: &str = "repos/mycelia";

/// What each subcommand parses to when it is given nothing but the arguments it requires: the
/// whole command, so a flag added with no leaf here shows up as a failure rather than as an
/// untested one. Every expectation is a struct literal, which is what makes that true — a new
/// field stops this file compiling.
fn surfaces() -> Vec<(&'static str, Vec<&'static str>, Command)> {
    vec![
        (
            "sessions",
            vec![PROJECT],
            Command::Sessions {
                project: PathBuf::from(PROJECT),
                projects_root: None,
            },
        ),
        (
            "extract",
            vec![PROJECT],
            Command::Extract {
                project: PathBuf::from(PROJECT),
                projects_root: None,
                db: PathBuf::from(DEFAULT_DB),
            },
        ),
        (
            "enrich",
            vec![],
            Command::Enrich(hp::enrich::Args {
                db: PathBuf::from(DEFAULT_DB),
                project: None,
                model: DEFAULT_MODEL.to_owned(),
                dry_run: false,
                limit: None,
                concurrency: DEFAULT_CONCURRENCY,
            }),
        ),
        (
            "export-otlp",
            vec![PROJECT],
            Command::ExportOtlp(hp::export::Args {
                project: PathBuf::from(PROJECT),
                db: PathBuf::from(DEFAULT_DB),
                backend: GENERIC.to_owned(),
                service_name: None,
                rate: DEFAULT_RATE,
                include_text: false,
                max_chars: DEFAULT_MAX_CHARS,
                dry_run: false,
            }),
        ),
        (
            "query",
            vec!["agent_types"],
            Command::Query {
                name: "agent_types".to_owned(),
                db: PathBuf::from(DEFAULT_DB),
                project: None,
                since: None,
                as_of: None,
                param: vec![],
                csv: false,
            },
        ),
        (
            "view",
            vec![],
            Command::View {
                db: PathBuf::from(DEFAULT_DB),
                port: PORT,
                dev: false,
            },
        ),
    ]
}

/// `hp <name> <required…>`, parsed.
fn parse(name: &str, rest: &[&str]) -> Command {
    let mut argv = vec!["hp", name];
    argv.extend_from_slice(rest);
    Cli::try_parse_from(argv)
        .unwrap_or_else(|error| panic!("`hp {name}` parses: {error}"))
        .command
}

/// The store a parsed command was pointed at, or `None` for the one subcommand that reads none.
fn store(command: &Command) -> Option<&Path> {
    match command {
        Command::Sessions { .. } => None,
        Command::Extract { db, .. } | Command::Query { db, .. } | Command::View { db, .. } => {
            Some(db)
        }
        Command::Enrich(args) => Some(&args.db),
        Command::ExportOtlp(args) => Some(&args.db),
    }
}

/// Every subcommand's flags and defaults are the ones the command line promises.
#[test]
fn a_subcommand_parses_to_the_arguments_it_documents() {
    for (name, required, expected) in surfaces() {
        assert_eq!(parse(name, &required), expected, "hp {name}");
    }
}

/// The surfaces are checked against the parser, so a seventh subcommand cannot arrive unpinned.
///
/// Without this the table above is a list someone maintains rather than the whole surface.
#[test]
fn every_subcommand_the_parser_exposes_is_pinned_above() {
    let exposed: BTreeSet<String> = Cli::command()
        .get_subcommands()
        .map(|subcommand| subcommand.get_name().to_owned())
        .collect();
    let pinned: BTreeSet<String> = surfaces()
        .into_iter()
        .map(|(name, ..)| name.to_owned())
        .collect();
    assert_eq!(exposed, pinned);
}

/// `--db` means the same path, typed the same way, in every subcommand that names a store.
///
/// The default is pinned per subcommand above; what this adds is that the five agree — same
/// long flag, same `PathBuf` — while `sessions` reads transcripts off disk and takes no store
/// at all. The set is read off the parser rather than off the table, so a `--db` added to
/// `sessions` fails here.
#[test]
fn the_store_flag_is_one_flag_wherever_it_appears() {
    let named: BTreeSet<String> = Cli::command()
        .get_subcommands()
        .filter(|subcommand| {
            subcommand
                .get_arguments()
                .any(|argument| argument.get_long() == Some("db"))
        })
        .map(|subcommand| subcommand.get_name().to_owned())
        .collect();
    assert_eq!(
        named,
        ["enrich", "export-otlp", "extract", "query", "view"]
            .map(str::to_owned)
            .into_iter()
            .collect::<BTreeSet<_>>()
    );
    for (name, required, _) in surfaces() {
        let elsewhere = [required.as_slice(), &["--db", "elsewhere.duckdb"]].concat();
        if !named.contains(name) {
            continue;
        }
        // A `PathBuf`, not the string clap hands back untyped.
        assert_eq!(
            store(&parse(name, &elsewhere)),
            Some(Path::new("elsewhere.duckdb")),
            "hp {name} --db"
        );
    }
}

/// `hp sessions` prints a line per session: its id, its subagents, and its path.
///
/// The subcommand that reads no store — it walks the projects root instead — so nothing else
/// drives its handler and a rewiring of it would otherwise land silently.
#[test]
fn the_sessions_command_lists_the_transcripts_it_found() {
    let scratch = tempfile::tempdir().expect("a tempdir");
    // If a project's directory holds two sessions, one of which spawned a subagent...
    let root = scratch.path().join("projects");
    let directory = root.join(encode_project_path(Path::new(MYCELIA)));
    let subagents = directory.join("a-first").join(SUBAGENTS_DIR);
    std::fs::create_dir_all(&subagents).expect("the scratch tree is writable");
    for id in ["a-first", "b-second"] {
        std::fs::write(directory.join(format!("{id}{TRANSCRIPT_SUFFIX}")), "")
            .expect("a transcript is writable");
    }
    std::fs::write(subagents.join("agent-aaa.jsonl"), "").expect("a subagent run is writable");

    let said = hp(&[
        "sessions".as_ref(),
        MYCELIA.as_ref(),
        "--projects-root".as_ref(),
        root.as_os_str(),
    ]);

    // ...then the listing names both, in discovery order, with the count of subagent
    // transcripts under each and the path a reader would open next.
    assert!(said.ok, "{}", said.stderr);
    assert_eq!(
        said.stdout.lines().collect::<Vec<_>>(),
        vec![
            format!(
                "a-first\t1 subagent(s)\t{}",
                directory.join("a-first.jsonl").display()
            ),
            format!(
                "b-second\t0 subagent(s)\t{}",
                directory.join("b-second.jsonl").display()
            ),
        ]
    );
}

/// `hp view` serves the store, port and mode it was given, and `--dev` is off unless it is typed.
///
/// The port of Python's browser leaf minus its first half: `--no-browser` has no Rust twin
/// (module docstring), and `--dev` is the flag that changes what the pages carry rather than how
/// the process starts. What runs the viewer is one match arm in `run_with`, which no leaf can
/// call without serving — so this reads the parse, and `cli.rs` reads the process.
#[test]
fn the_viewer_takes_the_store_the_port_and_the_mode_it_was_typed() {
    assert_eq!(
        [
            parse("view", &["--db", "traces.duckdb", "--port", "9000"]),
            parse("view", &[]),
            parse("view", &["--dev"]),
        ],
        [
            Command::View {
                db: PathBuf::from("traces.duckdb"),
                port: 9000,
                dev: false,
            },
            Command::View {
                db: PathBuf::from(DEFAULT_DB),
                port: PORT,
                dev: false,
            },
            Command::View {
                db: PathBuf::from(DEFAULT_DB),
                port: PORT,
                dev: true,
            },
        ]
    );
}
