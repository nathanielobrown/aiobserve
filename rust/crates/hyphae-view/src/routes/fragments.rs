//! Every route that answers with part of a page: a body opened in a log, the rest of a level, the
//! numbers behind a NavTree row, and the fat values a pane only previews.

use axum::Router;
use axum::extract::{Path as UrlPath, Query, State};
use axum::response::Response;
use axum::routing::{MethodRouter, get};

use crate::app::Shared;
use crate::routes::{Knobs, served};
use crate::{expansions, fragments};

/// The fragments, in the order `docs/viewer.md` prints them.
pub(crate) fn routes() -> Router<Shared> {
    mounted()
        .into_iter()
        .fold(Router::new(), |router, (path, handler)| {
            router.route(&path, handler)
        })
}

/// Every path this group mounts, beside the handler that answers it.
///
/// A `Router` cannot be asked what it holds, so the mounting is a list the router is folded out
/// of rather than a chain: `crate::routes::paths` reads the same list the app is built from.
pub(crate) fn mounted() -> Vec<(String, MethodRouter<Shared>)> {
    vec![
        (
            format!(
                "{}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}",
                crate::nodes::BODY_URL
            ),
            get(thread_body),
        ),
        (
            format!(
                "{}/session/{{session_id}}/{}/{{run_id}}",
                crate::nodes::BODY_URL,
                crate::nodes::Kind::Run.word()
            ),
            get(run_body),
        ),
        (
            format!(
                "{}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}",
                crate::nodes::KIN_URL
            ),
            get(node_kin),
        ),
        (
            format!(
                "{}/session/{{session_id}}/{{kind}}/{{node_id}}",
                crate::nodes::KIN_URL
            ),
            get(loose_kin),
        ),
        (
            format!(
                "{}/session/{{session_id}}/thread/{{source}}/{}/{{compaction_id}}",
                crate::nodes::NUMBERS_URL,
                crate::nodes::Kind::Compaction.word()
            ),
            get(compaction_numbers),
        ),
        (
            format!(
                "{}/session/{{session_id}}/thread/{{source}}/{{kind}}/{{node_id}}",
                crate::nodes::NUMBERS_URL
            ),
            get(node_numbers),
        ),
        (
            format!(
                "{}/session/{{session_id}}/{}/{{run_id}}",
                crate::nodes::NUMBERS_URL,
                crate::nodes::Kind::Run.word()
            ),
            get(run_numbers),
        ),
        (
            format!("{}/session/{{session_id}}", crate::nodes::NUMBERS_URL),
            get(session_numbers),
        ),
        (
            "/fragment/description/session/{session_id}/thread/{source}/turn/{turn_id}".to_owned(),
            get(turn_description),
        ),
        (
            "/fragment/friction/session/{session_id}/thread/{source}/turn/{turn_id}".to_owned(),
            get(turn_friction),
        ),
        (
            "/fragment/description/session/{session_id}/run/{run_id}".to_owned(),
            get(run_description),
        ),
        (
            "/fragment/friction/session/{session_id}/run/{run_id}".to_owned(),
            get(run_friction),
        ),
        (
            "/fragment/description/session/{session_id}".to_owned(),
            get(session_description),
        ),
        (
            "/fragment/friction/session/{session_id}".to_owned(),
            get(session_friction),
        ),
        (
            "/fragment/text/session/{session_id}/thread/{source}/call/{api_call_id}".to_owned(),
            get(call_text),
        ),
        (
            "/fragment/thinking/session/{session_id}/thread/{source}/call/{api_call_id}".to_owned(),
            get(call_thinking),
        ),
        (
            "/fragment/record/session/{session_id}/thread/{source}/line/{line_no}".to_owned(),
            get(record_value),
        ),
        (
            "/fragment/input/session/{session_id}/thread/{source}/tool/{tool_call_id}".to_owned(),
            get(tool_input),
        ),
        (
            "/fragment/result/session/{session_id}/thread/{source}/tool/{tool_call_id}".to_owned(),
            get(tool_result),
        ),
        (
            "/fragment/command/session/{session_id}/thread/{source}/tool/{tool_call_id}".to_owned(),
            get(tool_command),
        ),
        (
            "/fragment/prompt/session/{session_id}/thread/{source}/turn/{turn_id}".to_owned(),
            get(turn_prompt),
        ),
        (
            "/fragment/args/session/{session_id}/thread/{source}/turn/{turn_id}".to_owned(),
            get(turn_command_args),
        ),
        (
            "/fragment/brief/session/{session_id}/run/{run_id}".to_owned(),
            get(run_brief),
        ),
        (
            "/fragment/prompt/session/{session_id}/run/{run_id}".to_owned(),
            get(run_prompt),
        ),
        (
            "/fragment/result/session/{session_id}/run/{run_id}".to_owned(),
            get(run_result),
        ),
    ]
}

/// The body of a turn, an api call, or a tool call, opened in its parent's log.
async fn thread_body(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, kind, node_id)): UrlPath<(String, String, String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        expansions::thread_body(&viewer, &session_id, &source, &kind, &node_id, &asked)
    }))
}

/// One agent run's body, opened in the log of whatever lists it.
async fn run_body(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(
        knobs
            .asked()
            .and_then(|asked| expansions::run_body(&viewer, &session_id, &run_id, &asked)),
    )
}

/// The rest of one level under a node recorded on a thread.
async fn node_kin(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, kind, node_id)): UrlPath<(String, String, String, String)>,
    Query(spill): Query<expansions::Spill>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        expansions::node_kin(
            &viewer,
            &session_id,
            &source,
            &kind,
            &node_id,
            &spill,
            &asked,
        )
    }))
}

/// The rest of one level under a node that carries no thread of its own.
async fn loose_kin(
    State(viewer): State<Shared>,
    UrlPath((session_id, kind, node_id)): UrlPath<(String, String, String)>,
    Query(spill): Query<expansions::Spill>,
    Query(knobs): Query<Knobs>,
) -> Response {
    served(knobs.asked().and_then(|asked| {
        expansions::loose_kin(&viewer, &session_id, &kind, &node_id, &spill, &asked)
    }))
}

/// One compaction's numbers, for the popover its NavTree row fetches.
async fn compaction_numbers(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, compaction_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::compaction_numbers(
        &viewer,
        &session_id,
        &source,
        &compaction_id,
    ))
}

/// The numbers behind a turn, an api call, or a tool call recorded on a thread.
async fn node_numbers(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, kind, node_id)): UrlPath<(String, String, String, String)>,
) -> Response {
    served(fragments::node_numbers(
        &viewer,
        &session_id,
        &source,
        &kind,
        &node_id,
    ))
}

/// One agent run's numbers.
async fn run_numbers(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_numbers(&viewer, &session_id, &run_id))
}

/// A whole session's numbers.
async fn session_numbers(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
) -> Response {
    served(fragments::session_numbers(&viewer, &session_id))
}

/// What a pass wrote about one turn: what it said, and the friction it saw.
async fn turn_description(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::turn_said(
        &viewer,
        &session_id,
        &source,
        &turn_id,
        DESCRIPTION,
    ))
}

async fn turn_friction(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::turn_said(
        &viewer,
        &session_id,
        &source,
        &turn_id,
        FRICTION,
    ))
}

/// The same, for one agent run.
async fn run_description(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_said(
        &viewer,
        &session_id,
        &run_id,
        DESCRIPTION,
    ))
}

async fn run_friction(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_said(&viewer, &session_id, &run_id, FRICTION))
}

/// And for the session itself.
async fn session_description(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
) -> Response {
    served(fragments::session_said(&viewer, &session_id, DESCRIPTION))
}

async fn session_friction(
    State(viewer): State<Shared>,
    UrlPath(session_id): UrlPath<String>,
) -> Response {
    served(fragments::session_said(&viewer, &session_id, FRICTION))
}

/// The two lines a pass writes about an item, which are also the columns they are stored in.
const DESCRIPTION: &str = "description";
const FRICTION: &str = "friction";

/// What one api call said, whole.
async fn call_text(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, api_call_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::call_text(
        &viewer,
        &session_id,
        &source,
        &api_call_id,
    ))
}

/// What one api call thought, whole.
async fn call_thinking(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, api_call_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::call_thinking(
        &viewer,
        &session_id,
        &source,
        &api_call_id,
    ))
}

/// One raw transcript record, whole.
async fn record_value(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, line_no)): UrlPath<(String, String, i64)>,
) -> Response {
    served(fragments::record_value(
        &viewer,
        &session_id,
        &source,
        line_no,
    ))
}

/// What one tool call was passed, whole.
async fn tool_input(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, tool_call_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::tool_input(
        &viewer,
        &session_id,
        &source,
        &tool_call_id,
    ))
}

/// What one tool call returned, whole.
async fn tool_result(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, tool_call_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::tool_result(
        &viewer,
        &session_id,
        &source,
        &tool_call_id,
    ))
}

/// What one `Bash` call ran, whole.
async fn tool_command(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, tool_call_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::tool_command(
        &viewer,
        &session_id,
        &source,
        &tool_call_id,
    ))
}

/// What one turn was asked, whole.
async fn turn_prompt(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::turn_prompt(
        &viewer,
        &session_id,
        &source,
        &turn_id,
    ))
}

/// What followed the slash command one turn ran, whole.
async fn turn_command_args(
    State(viewer): State<Shared>,
    UrlPath((session_id, source, turn_id)): UrlPath<(String, String, String)>,
) -> Response {
    served(fragments::turn_command_args(
        &viewer,
        &session_id,
        &source,
        &turn_id,
    ))
}

/// The whole brief one agent run was given.
async fn run_brief(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_brief(&viewer, &session_id, &run_id))
}

/// The whole of what one agent run was asked.
async fn run_prompt(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_prompt(&viewer, &session_id, &run_id))
}

/// The whole of what one agent run sent back.
async fn run_result(
    State(viewer): State<Shared>,
    UrlPath((session_id, run_id)): UrlPath<(String, String)>,
) -> Response {
    served(fragments::run_result(&viewer, &session_id, &run_id))
}
