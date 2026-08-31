//! The recorded rows every tier names, keyed by what makes each one worth naming.
//!
//! The twin of `tests/conftest.py`'s landmark table. A fixture is a redacted excerpt of a real
//! session, so the shape a leaf needs — a turn with four api calls under it, a thread that
//! compacted outside `main`, a tool result Claude Code offloaded to a file — exists in exactly
//! one recorded place. Naming it here is what keeps a leaf's subject stable and its comment
//! about why that subject beside the id rather than at the use site.
//!
//! Every id is a store key, never a value a page prints, so nothing here carries transcript
//! text.

/// The project every recorded fixture was captured under. `tests/fixtures/*/README.md` names
/// the session behind each one.
pub const MYCELIA: &str = "/Users/nob/repos/mycelia";
/// The home directory that project sits under, which the viewer folds to `~` for a reader on
/// the machine the corpus was recorded on.
pub const HOME: &str = "/Users/nob";
/// The source name of a session's own thread.
pub const MAIN: &str = "main";
/// An id that matches nothing, in the shape a session id has. Every "the store does not hold
/// it" leaf asks for this one, whatever kind of id the route takes.
pub const MISSING: &str = "00000000-0000-0000-0000-000000000000";

// The two clean `invented/` fixtures are the only ones recorded under another project, which
// is what makes the corpus predicate testable: `/invented/project` and `/repo` respectively.
pub const INVENTED_PROJECT_SESSION: &str = "invented-no-cache-creation";
/// That fixture's one api call: the corpus's only reply whose usage carries no cache-creation
/// TTL split, so both split columns are NULL and the whole write prices at the 5-minute rate.
pub const NO_TTL_SPLIT_CALL: &str = "msg_invented000000000002";
pub const OTHER_PROJECT_SESSION: &str = "invented-truncated-tail";
/// `fork_byref`'s fork: NULL `project_dir` and NULL `started_at`, the recorded twin of the
/// store's zero-cost bookkeeping stubs. The corpus predicate cannot judge it either way.
pub const NO_PROJECT_SESSION: &str = "07a769d7-828c-4edb-b3ce-af51e2712aa3";
/// The three sessions the corpus predicate leaves out.
pub const NON_CORPUS: &[&str] = &[
    INVENTED_PROJECT_SESSION,
    OTHER_PROJECT_SESSION,
    NO_PROJECT_SESSION,
];

/// `spine/`: the deepest recorded run tree, and the session most leaves here read.
pub const SPINE: &str = "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b";
pub const SPINE_RUN: &str = "ac461ef46b4bb8e32";
pub const SPINE_LEAF: &str = "af6473ae437c9608d";
/// `spine/`'s main-thread turn typed as a slash command — `/night-run`, with arguments
/// recorded after it — which is the one shape that fills the two command columns.
pub const SLASH_TURN: &str = "30aad8e5-21f8-486d-b9d9-e118c703a5a1";
/// `spine/`'s `/model` turn — the one the CLI answered by itself — and the line its stdout
/// record sits on, so a planted record can be ordered against it.
pub const SPINE_MODEL_TURN: &str = "5b848af7-f86e-4950-b474-cd98125fad24";
pub const SPINE_MODEL_LINE: i64 = 8;
/// `spine/`'s turn whose three recorded api calls stopped `end_turn`, `tool_use` and nothing.
pub const STOP_REASON_TURN: &str = "9ae45aaa-d992-4089-a78d-f65d2f237080";
/// `spine/`'s main-thread `Bash` call. The one recorded shape that fills the command column:
/// a command is read out of a `Bash` call's arguments and every other tool has none.
pub const BASH_TOOL: &str = "toolu_012pdUKAdn6qh1dYSBug3rr9";
/// `spine/`'s one api call that asked for two different tools in the same breath: a tool
/// search beside a `Bash` command. A list named row by row reads differently from one named
/// by whatever its first row was, and this pair is what tells the two apart.
pub const SEARCH_TOOL: &str = "toolu_01CcyHEsu4XugVyeSfS3U8hT";
pub const SEARCH_BASH_TOOL: &str = "toolu_013mFHM2jYQ6khnnZDCHq5Ua";

/// `resume_pair/`'s resume, whose api calls all sit under no turn of its own thread.
pub const RESUME: &str = "0a76f771-5f5b-447e-852a-664fc972ea7c";
/// The line of `RESUME`'s longest recorded raw record, 3,054 chars — the one record past the
/// `records_slice` cap.
pub const RESUME_LONG_RECORD: i64 = 5;
/// The session `RESUME` resumed, and one of the two pool sessions that compacted.
pub const ANCESTOR: &str = "2352492b-1437-4427-ad51-70f35c75f663";

/// `server_tools/`, which carries an agent-source api call under no turn.
pub const SERVER_TOOLS: &str = "088d63aa-71d3-4108-965e-5147e3eaddbd";
pub const SERVER_TOOLS_RUN: &str = "a3b37063695183556";

// The densest shapes the corpus records, which the viewer's paging leaves page *below* so that
// a page boundary is a real overflow of recorded data rather than a staged one.
/// `ANCESTOR`'s main-thread turn holding four api calls, which is also the plain turn the
/// resume pair's archived stdout hangs off.
pub const DENSE_TURN: &str = "55309e59-0fae-4ef1-9251-877e27487bda";
pub const DENSE_TURN_CALL: &str = "msg_011Ccs78BfVLQfyQqhkxnpkm";
/// The api call in `FORK_ORIGIN_RUN` holding four tool calls, and the turn it was made in.
pub const DENSE_CALL: &str = "msg_011CdFxfStgUUn3Q59b4RFii";
pub const DENSE_CALL_TURN: &str = "33438141-776f-4e1e-9bc5-e5d85df18d22";
pub const DENSE_TOOL: &str = "toolu_015wiqbosE2nUYZBYdd9urjA";

/// `fork_origin/` holds the fork whose spawning call sits in the fork's own transcript.
pub const FORK_ORIGIN: &str = "5a88789c-1da7-4f32-b631-40a7e243334b";
/// The run that spawned it — the fork's `parent_agent_id`, and `DENSE_CALL`'s source.
pub const FORK_ORIGIN_RUN: &str = "acbc29008a04b9702";
pub const FORK_RUN: &str = "a61a059e3610e6fb4";
/// The second recorded fork, the one whose own api calls sit under no turn.
pub const BYREF_FORK: &str = "afa3946951a08a798";

/// `registry_zoo/`: one session naming every tool the registry knows.
pub const REGISTRY_ZOO: &str = "registry-zoo-0000-0000-0000-000000000000";
/// The pool session no other leaf asserts on, so a copied store can strip its api calls and
/// leave it the shape a `/model`-only session has.
pub const CONFIG_ONLY: &str = "7e37bb35-4dcb-4e16-85be-55ac510c168e";
/// `model_only/`: that same shape as recorded rather than planted — one `/model` turn and no
/// api call under it.
pub const MODEL_ONLY: &str = "bec99999-cbb7-4d11-9a58-3ad3d0e1c8cf";
/// The corpus's one offloaded tool result — Claude Code wrote the output to a file beside the
/// transcript instead of into it. `CONFIG_ONLY` recorded it, in a 159-character file.
pub const OFFLOAD_FILE: &str = "bosvr1kjx.txt";
pub const OFFLOAD_CHARS: i64 = 159;
pub const OFFLOAD_TOOL: &str = "toolu_01JXs55LXLHvzWt8KczuYfyD";

/// `teammate/`'s session and the `architect` run the team mechanism started inside it: the
/// corpus's one orphan, a run with no spawning tool call behind it.
pub const TEAMMATE: &str = "10d0349d-0705-4e23-aa64-5b1b97698b2e";
pub const TEAMMATE_RUN: &str = "aarchitect-5144001ac50718bc";

/// `compaction/`'s session, which holds two recorded main-thread compactions, and the first of
/// the two — the node a compaction's own page is served for.
pub const COMPACTED: &str = "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9";
pub const COMPACTED_BOUNDARY: &str = "459d0d29-cb67-477a-9cf1-f9bb19417c49";
/// Its agent run, the corpus's one thread that compacted outside `main`.
pub const COMPACTED_RUN: &str = "a003de2a5c1985f71";

/// `parallel_tools/`'s session, which issued a batch each way — two calls in one record, and
/// two a record apart — and addressed two of its own runs by id.
pub const PARALLEL: &str = "5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515";
pub const PARALLEL_RUNS: &[&str] = &["a43bfe9fc86734ff1", "aa52d3fe48cec7f58"];

/// The two sessions a re-export can re-home under a planted `project_dir`, chosen because no
/// other leaf asserts on them.
pub const WORKTREE_SESSION: &str = "0b34d1b8-ebd3-40a6-bd89-f1881e1de2ba";
pub const SIBLING_SESSION: &str = "4b443ab7-98f8-4c1d-859f-9bdcafbabdd3";

/// `dup_uuid/`: the session that records no main turn and no agent run.
pub const DUP_UUID: &str = "8ee00a94-b01a-4394-b447-b065f74b11af";
/// The `deep-research` user, and the only session `pr-and-document` reaches from the pool.
pub const DEEP_RESEARCH_SESSION: &str = "8d930c77-9e60-4784-9885-6d4c226280f7";
