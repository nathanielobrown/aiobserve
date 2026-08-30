//! Every route that answers with a whole page: the two listings, the eight node kinds, and the
//! four pages that stand beside them.

use std::collections::HashMap;

use axum::Router;
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;

use crate::app::Shared;
use crate::routes::{Chunk, Knobs, NOT_FOUND, error, served};
use hyphae_store::queries;

use crate::{listing, node_pages, pages, statics};

/// The pages, in the order `docs/viewer.md` prints them.
pub(crate) fn routes() -> Router<Shared> {
    Router::new()
        .route("/static/{name}", get(static_file))
        .route("/", get(projects_page))
        .route(listing::LIST_URL, get(session_list))
        .route("/session/{session_id}", get(session_page))
        .route(
            "/session/{session_id}/thread/{source}/turn/{turn_id}",
            get(turn_page),
        )
        .route("/session/{session_id}/run/{run_id}", get(run_page))
        .route(
            "/session/{session_id}/thread/{source}/call/{api_call_id}",
            get(call_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/tool/{tool_call_id}",
            get(tool_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/compaction/{compaction_id}",
            get(compaction_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/unattributed",
            get(unattributed_page),
        )
        .route("/session/{session_id}/unattached", get(unattached_page))
        .route("/session/{session_id}/errors", get(errors_page))
        .route(
            &format!("{}/{{query_name}}", crate::citation::QUERY_URL),
            get(query_page),
        )
        .route(
            "/session/{session_id}/thread/{source}/records",
            get(records_page),
        )
        .route(
            "/session/{session_id}/offload/{*offload_name}",
            get(offload_page),
        )
}

/// Every project the store holds sessions for.
async fn projects_page(State(viewer): State<Shared>) -> Response {
    served(listing::projects_page(&viewer))
}

/// One page of sessions, under the filter, sort and size the URL carries.
///
/// Every query-string key arrives rather than the four the page declares: what the list takes is a
/// closed set, and a key outside it is a 400 rather than a filter that silently did nothing.
async fn session_list(
    State(viewer): State<Shared>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    served(listing::session_list(&viewer, &params))
}

/// A session's own node page.
async fn session_page(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::session_page(&viewer, &session_id, &asked)),
    )
}

/// One turn, on the thread the URL names.
async fn turn_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs.asked().and_then(|asked| {
            node_pages::turn_page(&viewer, &session_id, &source, &turn_id, &asked)
        }),
    )
}

/// One agent run, which is its own thread.
async fn run_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::run_page(&viewer, &session_id, &run_id, &asked)),
    )
}

/// One api call.
async fn call_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, api_call_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::call_page(&viewer, &session_id, &source, &api_call_id, &asked)
    }))
}

/// One tool call.
async fn tool_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, tool_call_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::tool_page(&viewer, &session_id, &source, &tool_call_id, &asked)
    }))
}

/// One compaction of a thread.
async fn compaction_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, compaction_id)): UrlPath<(String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        node_pages::compaction_page(&viewer, &session_id, &source, &compaction_id, &asked)
    }))
}

/// One thread's api calls that answer no turn.
async fn unattributed_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source)): UrlPath<(String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::unattributed_page(&viewer, &session_id, &source, &asked)),
    )
}

/// The session's agent runs no spawning call resolved.
async fn unattached_page(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| node_pages::unattached_page(&viewer, &session_id, &asked)),
    )
}

/// Every failed tool call of one session, whichever thread it ran on.
async fn errors_page(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
) -> Response {
    served(pages::errors_page(&viewer, &session_id))
}

/// One library query's SQL, under the bindings the page that cited it carried.
///
/// The pairs arrive in the order they were written, and duplicates with them: what the page prints
/// is what the citation spelled, and neither is bound to anything.
async fn query_page(
    State(viewer): State<Shared>,
    UrlPath(query_name): UrlPath<String>,
    Query(asked): Query<Vec<(String, String)>>,
) -> Response {
    served(pages::query_page(&viewer, &query_name, &asked))
}

/// One page of a thread's raw records.
async fn records_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, source)): UrlPath<(String, String)>,
    Query(chunk): Query<Chunk>,
) -> Response {
    served(pages::records_page(
        &viewer,
        &session_id,
        &source,
        chunk.after.unwrap_or(queries::FIRST_PAGE),
        chunk.size.unwrap_or(crate::knobs::RECORDS.default),
    ))
}

/// One chunk of a tool result written to a file beside the transcript.
async fn offload_page(
    State(viewer): State<Shared>,
    UrlPath((session_id, offload_name)): UrlPath<(String, String)>,
    Query(chunk): Query<Chunk>,
) -> Response {
    served(pages::offload_page(
        &viewer,
        &session_id,
        &offload_name,
        chunk.after.unwrap_or(0),
        chunk.size.unwrap_or(crate::knobs::CHUNK.default),
    ))
}

/// One embedded asset, or the 404 every unknown path gets.
async fn static_file(UrlPath(name): UrlPath<String>) -> Response {
    let Some((kind, bytes)) = statics::asset(&name) else {
        return error(StatusCode::NOT_FOUND, NOT_FOUND);
    };
    ([(header::CONTENT_TYPE, kind)], bytes).into_response()
}
