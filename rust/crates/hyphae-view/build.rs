//! Write the static assets' name → bytes table, from the directory the Python viewer serves.
//!
//! Embedded rather than copied, for the reason the query library is (`hyphae-store/build.rs`):
//! htmx, the two NavTree scripts and the stylesheets stay one set of files in the repo, and a
//! `cargo build --release` still answers with a binary that needs nothing beside it.
//!
//! Every file in that directory, `dev-reload.js` included: `hp view --dev` puts it on each page
//! and `hp view` serves it to whoever asks, exactly as the Python static mount does
//! (`hyphae-view/src/dev.rs`).

use std::path::Path;

fn main() {
    let assets = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../src/hyphae/view/static");
    let assets = assets
        .canonicalize()
        .expect("the static assets sit at src/hyphae/view/static");
    // A file added or removed changes the table, so the whole directory is the trigger.
    println!("cargo:rerun-if-changed={}", assets.display());
    let mut names: Vec<String> = std::fs::read_dir(&assets)
        .expect("the static directory is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.is_file())
        .map(|path| {
            path.file_name()
                .expect("a file path ends in a file name")
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    names.sort();
    let mut table = String::from("pub static ASSETS: &[(&str, &str, &[u8])] = &[\n");
    for name in &names {
        let file = assets.join(name);
        table.push_str(&format!(
            "    ({name:?}, {:?}, include_bytes!({:?})),\n",
            content_type(name),
            file.display().to_string()
        ));
    }
    table.push_str("];\n");
    let out = Path::new(&std::env::var("OUT_DIR").expect("cargo sets OUT_DIR")).join("assets.rs");
    std::fs::write(out, table).expect("the generated table is writable");
}

/// What the browser is told each asset is. Starlette's `StaticFiles` guesses from the suffix;
/// this directory holds two suffixes, so the guess is written out rather than depended on.
fn content_type(name: &str) -> &'static str {
    match name.rsplit_once('.') {
        Some((_, "js")) => "text/javascript; charset=utf-8",
        Some((_, "css")) => "text/css; charset=utf-8",
        _ => panic!("no content type for the static asset `{name}` — add one to build.rs"),
    }
}
