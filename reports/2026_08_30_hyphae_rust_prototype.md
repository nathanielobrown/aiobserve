# 2026-08-30 — hyphae: the Rust prototype, judged

Whether to convert hyphae to Rust, answered by building it rather than arguing about it. Branch `rust-prototype` holds a cargo workspace under `rust/`. Its `hp` binary extracts the real corpus, serves the viewer, and passes the browser tier unchanged. `plans/rust-prototype/design.md` set the three questions this report answers: does the component layer read better, what does the edit loop cost, and does one binary do the product's job.

Three of its claims did not survive the full port; see [the corrections](#three-corrections-from-the-full-port) at the end.

Every number below was measured on one machine (Apple silicon, macOS 25.5) against frozen copies of two real corpora. Nothing here is a benchmark of the two languages; it measures this port on this hardware.

## Scope: five pieces stayed in Python

The judgment is only as wide as what was built. Each of these was deferred whole, not half-built:

- **Enrichment writing.** The Rust viewer reads enrichment rows the Python passes wrote; no Rust code writes one
- **OTLP export.** `hp export-otlp` stays Python. The store file is the seam
- **`hp view --dev`.** The Rust server serves; it has no watch loop and no reload endpoint
- **The doc and tooling layer** — cogs, aigarden, mutation testing, the gallery index, the bounds and budget sweeps
- **Store migrations and the shape check.** The Rust store creates a fresh store and refuses one at another schema version rather than migrating it

Two deviations belong in the record beside them:

- **Reader-error status codes differ, deliberately.** A malformed query value — `?page=abc`, or a kin fetch missing its required `thread` or `depth` — is a 422 from FastAPI and a 400 from axum. Either way the reader made the mistake, and no URL a page mints reaches it, so the parity claim below is bounded by these two cases
- **The fourth `insta` snapshot pins the preset control, not the cost badge the testing plan named.** The badge is pinned inside the NavTree rows snapshot rather than in one of its own

One more scope line: store rows stayed generic — `Row` with typed getters over a column-name lookup — rather than a struct for each of the ~40 named queries, so the SQL stays the schema authority in both languages. That was the design's choice A, and it leaves the Rust viewer no more compile-time coverage of query shapes than Python has.

## Readability: the markup reads better, the plumbing around it reads worse

The design named one exhibit, the NavTree row, because that function is the viewer's densest component. Here it is in both languages, whole.

Python, `src/hyphae/view/components/nav_tree.py`:

```python
def _row(*, row: NavTreeRow, suffix: str) -> Html:
    """One node's row: what it is, what it is called, and what it cost.

    `ancestor` is what the stylesheet clamps: a step of the open path above the selection stays
    at the top of the scroller while the rows under it go by. A class rather than a key of its
    own, like the bar's — it is a thing the stylesheet paints and not a value the store holds —
    and it rides the rows of one path, never the level around them.
    """
    node = row.node
    url = f"{node.url}{suffix}"
    return htpy.li(
        class_=f"row node {node.kind} {node.bar}{' ancestor' if row.ancestor else ''}",
        data_depth=row.depth,
        data_nav_tree=node.key,
        data_selected=node.key if row.selected else None,
        aria_current="true" if row.selected else None,
    )[
        [
            _peek(numbers=node.numbers),
            # A row links where it fetches: one URL, whether the reader clicks it, pastes it, or
            # comes back to it from a bookmark. What the click does with the response is written
            # once, on `#nav-tree-rows`, and inherited from here.
            htpy.a(href=url, hx_get=url)[
                [
                    parts.mark(character=node.icon),
                    parts.glyph(enriched=node.enriched),
                    htpy.span(data_field="title")[node.nav_tree_title],
                    _error(node.is_error),
                    _compacted(node.compactions),
                    _cost(row=row),
                ]
            ],
        ]
    ]
```

Rust, `rust/crates/hyphae-view/src/components/nav_tree.rs`:

```rust
/// One node's row: what it is, what it is called, and what it cost.
///
/// `ancestor` is what the stylesheet clamps: a step of the open path above the selection stays at
/// the top of the scroller while the rows under it go by. A class rather than a key of its own,
/// like the bar's — it is a thing the stylesheet paints and not a value the store holds.
fn line(row: &NavTreeRow, suffix: &str) -> Markup {
    let node = &row.node;
    let url = format!("{}{suffix}", node.url());
    let selected = row.selected.then(|| node.key());
    // Byte-for-byte what htpy writes, trailing space and all: a node with no context bar leaves
    // the class list ending on the separator, and the two viewers serve one page.
    let mut class = format!("row node {} {}", node.kind, node.bar());
    if row.ancestor {
        class.push_str(" ancestor");
    }
    rsx! {
        <li
            class=(class)
            data-depth=(row.depth)
            data-nav-tree=(node.key())
            data-selected=[selected.as_deref()]
            aria-current=[row.selected.then_some("true")]
        >
            (peek(&node.numbers()))
            // A row links where it fetches: one URL, whether the reader clicks it, pastes it, or
            // comes back to it from a bookmark. What the click does with the response is written
            // once, on `#nav-tree-rows`, and inherited from here.
            <a href=(url) hx-get=(url)>
                (parts::mark(node.icon()))
                (parts::glyph(node.enriched))
                <span data-field="title">(node.nav_tree_title())</span>
                (error(node.is_error))
                (compacted(node.compactions))
                (cost(node))
            </a>
        </li>
    }
    .memoize()
}
```

Judge them yourself. My reading: the markup half is plainly better in Rust. `rsx!` writes HTML that looks like HTML, so the `<li>`/`<a>`/`<span>` nesting is visible at a glance. htpy's `element(attrs)[children]` bracket makes a reader hold two shapes at once, and it pushes the children of a two-level row three list-literals deep. The Rust body reads top to bottom.

The plumbing half is worse. Python builds the class list inside one f-string; Rust needs four imperative lines above the macro because `rsx!` has no conditional-suffix form. Every component ends in `.memoize()` — a `Lazy` borrows its environment and no function can return one — ceremony repeated on 92 functions. And the borrows show: `&node.numbers()`, `selected.as_deref()`, `row.selected.then_some("true")` where Python writes `None` and a conditional expression.

Two findings beyond the exhibit:

- **The design's escape-hatch risk is dead.** It expected `data-*` and htmx attribute names to need hypertext's quoted-name form, and warned that reading badly at scale would be a finding. Nothing in the package needs it: `hx-get`, `data-nav-tree`, `aria-current` all typecheck as written. Tag and attribute names are compile-checked, which htpy cannot do
- **The mapping was mechanical.** All 92 components under `src/hyphae/view/components/` port under two rules — `rsx! { … }.memoize()` for the body, `attr=[option]` for htpy's `None`. That is a translation, not a redesign, which is why the two viewers can be diffed page for page at all

Rust costs some length. Count with `scc` over the two file sets the extract and store port puts side by side — `rust/crates/hyphae-extract/src` and `rust/crates/hyphae-store/src` (17 files, 4,124 lines, 3,120 of them code) against `src/hyphae/extract/`, `src/hyphae/export/duckdb.py` and `src/hyphae/export/schema.py` (14 files, 3,351 lines, 2,902 code) — and Rust comes out 1.2x by total lines and 1.1x by code. The gap is the doc comments the port writes where Python's shape carried the explanation, and the bulk of the added code is the column orders Python reads at run time from `dataclasses.fields`. That ratio has a practical edge: aigarden's 700-line source budget stopped three Rust files whose Python mirrors sit well under it, so a Python module much past 570 lines will land over budget and need a split.

## Iteration: the loop is fast on the stock toolchain

The design's bar was a save-to-reload loop under about 5 seconds, to be measured with `mold` configured. **No `mold` is configured** — there is no `rust/.cargo/config.toml` and no mold on this machine. The numbers below are the stock toolchain.

Measured by editing one line in `rust/crates/hyphae-view/src/components/nav_tree.rs` and reverting it after:

| Step | Time |
| --- | --- |
| `cargo check -p hyphae-view` | 0.3s |
| `cargo nextest run -p hyphae-view` (36 tests) | 10.5s wall, 4.2s of it running tests |
| Incremental release rebuild of the binary | 3.1s |
| Clean release build into an empty `CARGO_TARGET_DIR` | 1m 20s wall (16m 33s user) |

The 0.3s is the floor, not the average — a one-line change in one crate is nearly free to rustc's incremental cache. The honest edit-to-test number is 10.5s, most of it linking test binaries, and that is where a linker swap would pay.

The gates, whole:

| Gate | Tests | Wall |
| --- | --- | --- |
| `mise run rust-check` (fmt, clippy `-D warnings`, nextest) | 80 | 17.6s |
| `mise run check` (the Python gate) | 1,966 passed, 51 skipped | 230.7s |

Those two aren't the same job: the Python gate also runs the hook linter, the doc tooling, and a suite covering enrichment, OTLP and analysis that has no Rust counterpart. Read it as what each half costs to gate today, not as a like-for-like ratio. `rust-check` is deliberately not a dependency of `check`: CI installs Python and node and no Rust toolchain, so folding it in would red every run.

## Product: one binary, and no difference a reader can see

**Extract parity is clean on both corpora.** A gitignored harness diffs every table both ways with `EXCEPT ALL`, ordered by primary key, and prints its exclusions before any result.

| Corpus | Sessions | Raw records | Tables compared | Diffs |
| --- | --- | --- | --- | --- |
| `/Users/nob/repos/hyphae` (frozen copy, 558 MB) | 46 | 110,330 | 10 | 0 |
| `/Users/nob/repos/mycelia` (frozen copy, 2.3 GB) | 576 | 561,204 | 10 | 0 |

Three excluded columns all sit in `extract_state`. `fingerprint` and `extractor_version` go because the Rust extractor declares its own version by design, so each side re-extracts the other's store; `extracted_at` is wall clock. The harness prints all three on every run. Both extractors also raised the same single warning on the same mycelia thread.

Freezing the corpus is what makes the diff mean anything. A first run pointed both extractors at the live `~/.claude/projects/` and the recording session appended records between them, which showed as one extra `api_call` and a moved `ended_at` — a difference in the input read as a difference in the port.

Extraction time over those same frozen corpora, release build:

| Corpus | Python | Rust |
| --- | --- | --- |
| 46 sessions, 558 MB | 96.9s | 4.2s |
| 576 sessions, 2.3 GB | 444.5s | 27.9s |

**Page parity: 979 URLs, 837 identical to the byte after one fold.** Both viewers served one enriched fixture store, and the sweep covered every node kind, both listings, the errors, query, records and offload pages, and every fragment route. The fold is the one escaping dialect the two do not share: markupsafe writes `&#39;` and `&#34;` where Rust's escaper writes `'` and `&quot;`, so the harness rewrites all three to the characters they name, on both sides, before diffing — every other byte still has to match. All 142 differing pages are accounted for: strip `<pre>…</pre>` from both sides and re-diff, and 0 differences remain outside those blocks. The residue is syntax highlighting — syntect emits TextMate classes the shared `static/pygments.css` cannot paint. No other markup differs.

One flaw in the harness, in what it keeps rather than what it compares: it names each saved page after its URL with `/?&=` replaced by `_`, and 8 of the 979 URLs collide onto 971 names. Each URL is still fetched and diffed on its own pass, so the counts hold; only the saved artifacts of those 8 clobber each other.

**The browser tier passed unchanged: 24 specs, 24 passed, three runs.** The specs under `tests/e2e/specs/` were not edited, skipped, or retried. The prototype's one behavioural edit outside `rust/` is `tests/e2e/playwright.config.ts`, which gained three `process.env.X ?? <what it was>` constants for the base URL, server command and readiness URL. Unset, it behaves exactly as before, and a control run confirmed that. `lsof` on the listening port during the Rust run named the release binary, so nothing fell back silently to the Python gallery.

Five other files outside `rust/` changed, all of them configuration or tooling that had to learn the new directory exists: `.gitignore` (`rust/target/`), `pyproject.toml` (two ruff exemptions for the Python script that generates a Rust snapshot), `mise.toml` (the `rust-check` task and its parts), `tools/gen_layout.py` (one entry) and the `CLAUDE.md` layout tree that generator writes. Nothing under `src/hyphae/` was touched.

**The binary is 48.4 MB unstripped, 38.8 MB stripped**, with DuckDB bundled — nothing to install beside it. That DuckDB is most of the weight is inferred, not measured; I did not break the size down by crate.

Two smaller results worth keeping. The design's worst store risk — duckdb-rs's appender panicking on nested LIST/STRUCT values — turned out to be unreachable: no stored column is nested, the DDL is flat scalars end to end, and the named fallback would have failed identically because both paths bind through `ToSql`. The appender is also 73x faster than prepared `INSERT`: 22ms against 1.6s for 10,000 real `api_calls` rows.

One known wobble: `cargo nextest` reported leaky tests on two occasions (2, then 3) and none on repeat runs. Nothing failed and nothing hung. Most likely DuckDB's background threads outliving a connection, plus the `uv run` subprocesses the CLI tier spawns; unproven either way.

## The verdict: achievable and pleasant, not yet priced

The prototype answers all three questions in Rust's favour, within the scope it was given. The component layer reads better where it counts — the markup — at the price of ceremony around it, and the port was mechanical enough that 92 components moved under two rules. The edit loop costs 0.3s to check and about 10s to test a crate, without a linker swap, against a 231s Python gate. And one 48 MB binary extracts two real corpora with zero table diffs, 23x and 16x faster, and serves a viewer 979 URLs of which every difference from Python's is a syntax-highlighting class inside a `<pre>`.

What that does not settle is the half that was never built. Enrichment writing, OTLP export, the dev reload loop, the doc tooling and store migrations are all still Python, and a graduation decision has to price them, plus the row-typing question the prototype deferred. This report is evidence that the conversion is achievable and pleasant, not that it is worth its remaining cost.

## Three corrections from the full port

The full port (`reports/2026_08_31_hyphae_rust_full_port.md`) falsified three of the claims above. The body stands as the record of what that pass found; these are what changed.

- **The `<pre>` residue was not syntect against Pygments.** The prototype highlighted nothing: `highlight::lit` returned `syntax: None` unconditionally, a bug, so the residue was highlighting against none. The full port fixed the bug and added real highlighting — syntect behind a 39-entry table mapping TextMate scope prefixes onto the classes the shared `static/pygments.css` already paints
- **"Nothing under `src/hyphae/` was touched" no longer holds.** The generation bridge made Python the owner of metadata Rust reads, so the full port added Python-side generators — the query manifest, the bounds registry, the enrichment stamps and vocabulary — and their freshness tests
- **The verdict is priced now.** The port took ten agent slices over two calendar days; the branch carries the slices plus the fixes a pre-push audit called for, counted by `git log --oneline 4cf891f..HEAD`. What it did not price is who ends up owning the schema; the later report ends on that
