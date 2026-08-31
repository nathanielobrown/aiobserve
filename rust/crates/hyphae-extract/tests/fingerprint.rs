//! What re-extraction depends on: the digest over a session's whole file set.
//!
//! Real files in a tempdir, because the digest reads size and mtime — there is nothing to
//! test without a filesystem underneath.

use hyphae_testsupport::corpus;

use std::path::{Path, PathBuf};

use hyphae_extract::{EXTRACTOR_VERSION, fingerprint};

/// The `spine/` fixture copied into a tempdir: a main transcript, two subagent transcripts
/// and their metas. Returns the copy's root and the session's files in discovery order.
fn planted(root: &Path) -> Vec<PathBuf> {
    let stem = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";
    copy_tree(&corpus::fixtures().join("spine"), root);
    let source = corpus::source(&root.join(format!("{stem}.jsonl")));
    source.files
}

fn copy_tree(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).expect("the destination is writable");
    for entry in std::fs::read_dir(from).expect("the fixture directory is readable") {
        let entry = entry.expect("the entry is readable");
        let target = to.join(entry.file_name());
        if entry.file_type().expect("the entry has a type").is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), &target).expect("the file copies");
        }
    }
}

/// Touch a file so its mtime moves, without changing what it says.
fn touch(path: &Path) {
    let text = std::fs::read_to_string(path).expect("the file is readable");
    // A whole second, because HFS+ and some mounts keep mtime to that resolution: a
    // sub-millisecond rewrite would leave the digest legitimately unchanged.
    std::thread::sleep(std::time::Duration::from_millis(1100));
    std::fs::write(path, text).expect("the file is writable");
}

/// The leaf the digest exists for: a subagent transcript or an offload file changing must
/// move it, not only the main transcript. A port that digests one file passes everything else.
#[test]
fn a_change_to_any_session_file_moves_the_digest() {
    let directory = tempfile::tempdir().expect("a tempdir");
    let root = directory.path();
    let files = planted(root);
    let mut before = fingerprint(&files, root, EXTRACTOR_VERSION).expect("the files are readable");

    // Every file but the main transcript, which is `files[0]`.
    let companions = &files[1..];
    assert!(
        companions.len() > 1,
        "spine plants {} companion file(s)",
        companions.len()
    );
    for companion in companions {
        touch(companion);
        let after = fingerprint(&files, root, EXTRACTOR_VERSION).expect("the files are readable");
        assert_ne!(
            before,
            after,
            "touching {} left the digest where it was",
            companion
                .strip_prefix(root)
                .expect("the file is under the tempdir")
                .display()
        );
        // Re-baseline, or every companion after the first is only compared against the
        // original digest: a port that digests one file moves away from it once and then
        // passes the rest of the loop for free.
        before = after;
    }
}

/// Two passes over an untouched tree agree, or nothing would ever be skipped.
#[test]
fn an_untouched_tree_digests_the_same_twice() {
    let directory = tempfile::tempdir().expect("a tempdir");
    let root = directory.path();
    let files = planted(root);
    let once = fingerprint(&files, root, EXTRACTOR_VERSION).expect("the files are readable");
    let twice = fingerprint(&files, root, EXTRACTOR_VERSION).expect("the files are readable");
    assert_eq!(once, twice);
}

/// The version is folded in, so bumping it re-extracts the corpus rather than leaving old
/// rows parsed by old logic. This is also why the Rust extractor declares its own string:
/// sharing Python's would make each read the other's rows as current.
#[test]
fn the_digest_folds_in_the_extractor_version() {
    let directory = tempfile::tempdir().expect("a tempdir");
    let root = directory.path();
    let files = planted(root);
    let ours = fingerprint(&files, root, EXTRACTOR_VERSION).expect("the files are readable");
    let theirs = fingerprint(&files, root, "7").expect("the files are readable");
    assert_ne!(ours, theirs);
}

/// Byte parity with `extract/claude_code.py:fingerprint` over one tree, at one version.
///
/// The version is a parameter precisely so this leaf can hand over Python's string: with it,
/// two digests over the same files must agree, and any disagreement is in the entry format
/// — the relative path spelling, the size, or the mtime resolution.
#[test]
fn the_digest_matches_the_python_one_over_the_same_tree() {
    let directory = tempfile::tempdir().expect("a tempdir");
    let root = directory.path();
    let files = planted(root);
    let python_version = python(&[
        "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); \
         from hyphae.extract.claude_code import EXTRACTOR_VERSION; print(EXTRACTOR_VERSION)",
        &repo().join("src").display().to_string(),
    ]);
    let ours = fingerprint(&files, root, &python_version).expect("the files are readable");

    let listed: Vec<String> = files
        .iter()
        .map(|path| path.display().to_string())
        .collect();
    let mut argv = vec![
        "-c".to_owned(),
        "import sys; sys.path.insert(0, sys.argv[1]); \
         from pathlib import Path; from hyphae.extract.claude_code import fingerprint; \
         print(fingerprint([Path(p) for p in sys.argv[3:]], Path(sys.argv[2])))"
            .to_owned(),
        repo().join("src").display().to_string(),
        root.display().to_string(),
    ];
    argv.extend(listed);
    let borrowed: Vec<&str> = argv.iter().map(String::as_str).collect();
    assert_eq!(ours, python(&borrowed));
}

/// Run the repo's Python and hand back its one line of output.
///
/// The virtualenv `uv` owns when it is there, the system interpreter otherwise — the code
/// this calls is stdlib-only, so either can run it.
fn python(args: &[&str]) -> String {
    let venv = repo().join(".venv/bin/python");
    let interpreter = if venv.exists() {
        venv
    } else {
        PathBuf::from("python3")
    };
    #[expect(
        clippy::disallowed_methods,
        reason = "the python bridge: the oracle is the fingerprint Python computes"
    )]
    let run = std::process::Command::new(interpreter)
        .args(args)
        .current_dir(repo())
        .output()
        .expect("a Python interpreter is available");
    assert!(
        run.status.success(),
        "python failed: {}",
        String::from_utf8_lossy(&run.stderr)
    );
    String::from_utf8(run.stdout)
        .expect("python printed UTF-8")
        .trim()
        .to_owned()
}

fn repo() -> PathBuf {
    corpus::fixtures()
        .parent()
        .expect("tests/ has a parent")
        .parent()
        .expect("the repo")
        .to_owned()
}
