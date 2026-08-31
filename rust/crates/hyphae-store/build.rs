//! Write the query library's name → text table, from the directory Python reads at run time.
//!
//! `analyze/queries.py:load` opens `queries/<name>.sql` when it is asked for; Rust compiles
//! the bytes in instead, and `include_str!` takes a literal path. Walking the directory here
//! is what keeps the two consumers over one set of files: a query added to the library is in
//! the Rust catalog without anyone listing it twice.

use std::path::Path;

fn main() {
    let queries = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../src/hyphae/analyze/queries");
    let queries = queries
        .canonicalize()
        .expect("the SQL library sits at src/hyphae/analyze/queries");
    // A file added or removed changes the table, so the whole directory is the trigger.
    println!("cargo:rerun-if-changed={}", queries.display());
    let mut stems: Vec<String> = std::fs::read_dir(&queries)
        .expect("the SQL library is readable")
        .map(|entry| entry.expect("the entry is readable").path())
        .filter(|path| path.extension().is_some_and(|extension| extension == "sql"))
        .map(|path| {
            path.file_stem()
                .expect("a .sql path ends in a file name")
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    stems.sort();
    let mut table = String::from("pub static QUERIES: &[(&str, &str)] = &[\n");
    for stem in &stems {
        let file = queries.join(format!("{stem}.sql"));
        table.push_str(&format!(
            "    ({stem:?}, include_str!({:?})),\n",
            file.display().to_string()
        ));
    }
    table.push_str("];\n");
    let out = Path::new(&std::env::var("OUT_DIR").expect("cargo sets OUT_DIR")).join("queries.rs");
    std::fs::write(out, table).expect("the generated table is writable");
}
