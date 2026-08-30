//! The pages that are not a node's: a session's failures, a query, the raw records, a file.
//!
//! Ported from `src/hyphae/view/pages.py`. Each answers a question about a session that no single
//! node holds — every failed tool call on every thread, the SQL behind a page, the transcript as
//! Claude Code wrote it, and the output a tool wrote to a file instead of the transcript
//! (`docs/viewer.md`). The markup is `components::pages`; what is here is what each one reads.

use hyphae_store::{Param, macros, queries};

use crate::browse::{PageError, header_bound};
use crate::components::Markup;
use crate::components::pages as components;
use crate::nav_tree::Bound;
use crate::store::{Page, Query, ViewError, page_rows, paged};
use crate::viewer::Viewer;
use crate::{citation, errors, knobs};

/// Every failed tool call of one session, in the order they happened.
///
/// Not a node page: a failure is a property of a tool call rather than a place in the NavTree, and
/// a session's failures are scattered across every thread it ran. So this is a list, and each row
/// leads to the tool call's own page — which opens the NavTree at it and carries the crumbs that
/// place it.
pub fn errors_page(viewer: &Viewer, session_id: &str) -> Result<Markup, PageError> {
    let store = viewer.reader.connect()?;
    let failed = errors::failures(&store, session_id)?;
    // A session the store never held and one whose calls all succeeded are both nothing at this
    // URL, and not the same nothing. The header is read only when there is a 404 to word, so the
    // page a reader actually opens runs one query.
    let held = !failed.listed.is_empty()
        || !page_rows(&store, Page::SessionHeader, &header_bound(session_id))?.is_empty();
    drop(store);
    if failed.listed.is_empty() {
        return Err(PageError::Missing(
            if held {
                "This session's tool calls all succeeded."
            } else {
                "No session with that id is in this store."
            }
            .to_owned(),
        ));
    }
    Ok(components::errors_page(
        session_id,
        &failed.listed,
        failed.cut,
        &citation::citations(&failed.ran),
        viewer.dev,
    ))
}

/// One library query's SQL, under the bindings a page cited it with.
///
/// Where every citation in a footer goes. The name is a key of the query manifest and never a
/// path: a name the library does not declare is a 404 before anything is read, which is what makes
/// a request for `../../secret` a miss rather than a file.
pub fn query_page(
    viewer: &Viewer,
    name: &str,
    // Whatever the citation carried, printed back rather than bound to anything: this page runs no
    // query, so a binding here is a fact about the page that sent you. It is the one place a
    // request's own text reaches rendering, and it crosses the seam as plain data.
    asked: &[(String, String)],
) -> Result<Markup, PageError> {
    if !queries::QUERIES.iter().any(|(stem, _)| *stem == name) {
        return Err(PageError::Missing(
            "No query by that name ships with this build.".to_owned(),
        ));
    }
    let statement = queries::load(name);
    Ok(components::query_page(
        name,
        statement,
        // What a shell has to run first, where the statement calls a library macro: both consumers
        // install these, and a reader pasting the statement alone has no way to find out why the
        // catalog does not know the name.
        &macros::needed_by(statement),
        &bindings(asked),
        viewer.dev,
    ))
}

/// One page of a thread's raw transcript — where a report's citation lands.
///
/// A citation names `(session_id, source, line_no)`; the URL for it is this path with
/// `?after={line_no - 1}#L{line_no}`, so the cited record is the first row on the page.
pub fn records_page(
    viewer: &Viewer,
    session_id: &str,
    source: &str,
    after: i64,
    size: i64,
) -> Result<Markup, PageError> {
    let size = knobs::checked(size, knobs::RECORDS.ceiling)?;
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("source", source.into()),
        ("after", Param::Int(after)),
        ("page_records", Param::Int(size)),
        ("preview_chars", Param::Int(queries::RECORD_PREVIEW as i64)),
    ];
    let page = {
        let store = viewer.reader.connect()?;
        paged(
            page_rows(&store, Page::Records, &bound)?,
            "matched_records",
            "line_no",
        )
        .map_err(ViewError::from)?
    };
    // A thread the store never held and a cursor past the end of one it does are the same answer —
    // nothing at this URL. Neither is a page worth rendering empty.
    let Some(first) = page.rows.first() else {
        return Err(PageError::Missing(
            "This store holds no records for that thread at that line.".to_owned(),
        ));
    };
    // The one record the page fetches unasked: the first row, which is the one a citation named —
    // but only where a record that wide stays inside a page's budget
    // (`knobs::OPENED_RECORD_CHARS`). Past it the row is where every other row is, one click from
    // its own fetch, because a reader who paged here asked for no such thing.
    let opened = (first.i64("raw_chars")? <= knobs::OPENED_RECORD_CHARS as i64)
        .then(|| first.i64("line_no"))
        .transpose()?;
    let matched = first.i64("matched_records")?;
    let rows = page
        .rows
        .iter()
        .map(|row| {
            Ok(components::RecordRow {
                line_no: row.i64("line_no")?,
                kind: row.str("type")?.to_owned(),
                timestamp: row.opt_timestamp("timestamp")?,
                raw_chars: row.i64("raw_chars")?,
                raw_head: row.str("raw_head")?.to_owned(),
            })
        })
        .collect::<Result<Vec<_>, hyphae_store::RowError>>()?;
    Ok(components::records_page(
        &components::RecordsPage {
            session_id,
            source,
            rows: &rows,
            matched,
            opened,
            after: page.after,
            more: page.more,
            size,
        },
        &citation::citations(&[(Page::Records.stem(), bound)]),
        viewer.dev,
    ))
}

/// One chunk of a tool result Claude Code wrote to a file beside the transcript.
///
/// The name is the transcript's own file name, so it may hold anything a tool named a file —
/// spaces, percent signs, something shaped like a path. It is a key into the store and never a
/// path the server opens, which is what makes the shape of it uninteresting.
pub fn offload_page(
    viewer: &Viewer,
    session_id: &str,
    offload_name: &str,
    after: i64,
    size: i64,
) -> Result<Markup, PageError> {
    let size = knobs::checked(size, knobs::CHUNK.ceiling)?;
    if after < 0 {
        return Err(knobs::BadAsk("Ask for an offset of 0 or more.".to_owned()).into());
    }
    let bound: Bound = vec![
        ("session_id", session_id.into()),
        ("name", offload_name.into()),
        ("after_chars", Param::Int(after)),
        ("chunk_chars", Param::Int(size)),
    ];
    let rows = {
        let store = viewer.reader.connect()?;
        page_rows(&store, Page::Offload, &bound)?
    };
    let Some(row) = rows.first() else {
        return Err(PageError::Missing(
            "No offloaded result of that name is in this session.".to_owned(),
        ));
    };
    let file = components::OffloadFile {
        name: row.str("name")?.to_owned(),
        size_bytes: row.i64("size_bytes")?,
        content_chars: row.i64("content_chars")?,
        lossy_decode: row.bool("lossy_decode")?,
        chunk: row.str("chunk")?.to_owned(),
    };
    // Characters rather than bytes: the store counted the content in the units SQL cut it by.
    let served = after + file.chunk.chars().count() as i64;
    Ok(components::offload_page(
        session_id,
        &file,
        // Where the next chunk starts, or nothing when this one reached the end.
        (served < file.content_chars).then_some(served),
        size,
        &citation::citations(&[(Page::Offload.stem(), bound)]),
        viewer.dev,
    ))
}

/// The query string as Python's `dict(request.query_params)` reads it: a key at the place it first
/// appeared, holding the last value spelled for it.
fn bindings(asked: &[(String, String)]) -> Vec<(String, String)> {
    let mut held: Vec<(String, String)> = Vec::with_capacity(asked.len());
    for (key, value) in asked {
        match held.iter_mut().find(|(seen, _)| seen == key) {
            Some(seen) => seen.1.clone_from(value),
            None => held.push((key.clone(), value.clone())),
        }
    }
    held
}
