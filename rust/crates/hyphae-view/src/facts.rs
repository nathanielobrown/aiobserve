//! The labelled fields a node's body prints, read off the row its header query answered.
//!
//! Ported with [`crate::builders`] from `src/hyphae/view/builders.py`, and split off it here
//! because it is the one place a store row stops being a bag of columns: past this function a
//! body reads named fields of a type, so a query that dropped one fails here rather than
//! printing a dash under its label.

use hyphae_store::{Row, RowError};

use crate::components::node_body::{
    BucketFacts, CallFacts, CompactionFacts, Facts, RunFacts, SessionFacts, ToolFacts, TurnFacts,
};
use crate::nodes::{Kind, Node};

/// Total over [`Kind`], the two buckets sharing a shape because neither is a row of the store —
/// what they hold is counted on the node itself.
pub fn node_facts(node: &Node, row: &Row) -> Result<Facts, RowError> {
    match node.kind {
        Kind::Session => Ok(Facts::Session(SessionFacts {
            session_id: row.str("session_id")?.to_owned(),
            git_branch: row.opt_str("git_branch")?.map(str::to_owned),
            version: row.opt_str("version")?.map(str::to_owned),
            entrypoint: row.opt_str("entrypoint")?.map(str::to_owned),
            started_at: row.opt_timestamp("started_at")?,
            wall_ms: row.opt_i64("wall_ms")?,
            active_ms: row.opt_i64("active_ms")?,
            turns: row.i64("turns")?,
            api_calls: row.i64("api_calls")?,
            tool_calls: row.i64("tool_calls")?,
            tool_errors: row.i64("tool_errors")?,
            agent_runs: row.i64("agent_runs")?,
            compactions: row.i64("compactions")?,
            cost_usd: row.opt_f64("cost_usd")?,
            unpriced_api_calls: row.i64("unpriced_api_calls")?,
            output_tokens: row.i64("output_tokens")?,
            skills: row
                .strings("skills")?
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            skills_cut: row.i64("skills_cut")?,
            pr_urls: row
                .strings("pr_urls")?
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            pr_urls_cut: row.i64("pr_urls_cut")?,
        })),
        Kind::Turn => Ok(Facts::Turn(TurnFacts {
            turn_id: row.str("turn_id")?.to_owned(),
            command_name: row.opt_str("command_name")?.map(str::to_owned),
            turn_index: row.i64("turn_index")?,
            started_at: row.opt_timestamp("started_at")?,
            replayed: row.bool("replayed")?,
            api_calls: row.i64("api_calls")?,
            tool_calls: row.i64("tool_calls")?,
            tool_errors: row.i64("tool_errors")?,
            cost_usd: row.opt_f64("cost_usd")?,
            unpriced_api_calls: row.i64("unpriced_api_calls")?,
        })),
        Kind::Run => Ok(Facts::Run(RunFacts {
            run_id: row.str("run_id")?.to_owned(),
            agent_type: row.opt_str("agent_type")?.map(str::to_owned),
            model: row.opt_str("model")?.map(str::to_owned),
            spawn_depth: row.i64("spawn_depth")?,
            is_fork: row.bool("is_fork")?,
            started_at: row.opt_timestamp("started_at")?,
            wall_ms: row.opt_i64("wall_ms")?,
            turns: row.i64("turns")?,
            api_calls: row.i64("api_calls")?,
            tool_calls: row.i64("tool_calls")?,
            tool_errors: row.i64("tool_errors")?,
            compactions: row.i64("compactions")?,
            cost_usd: row.opt_f64("cost_usd")?,
            unpriced_api_calls: row.i64("unpriced_api_calls")?,
            output_tokens: row.i64("output_tokens")?,
        })),
        Kind::Call => Ok(Facts::Call(CallFacts {
            call_index: row.i64("call_index")?,
            model: row.opt_str("model")?.map(str::to_owned),
            fallback_from: row.opt_str("fallback_from")?.map(str::to_owned),
            effort: row.opt_str("effort")?.map(str::to_owned),
            stop_reason: row.opt_str("stop_reason")?.map(str::to_owned),
            attribution_skill: row.opt_str("attribution_skill")?.map(str::to_owned),
            started_at: row.opt_timestamp("started_at")?,
            tool_calls: row.i64("tool_calls")?,
            input_tokens: row.i64("input_tokens")?,
            output_tokens: row.i64("output_tokens")?,
            cache_read_tokens: row.i64("cache_read_tokens")?,
            cache_creation_tokens: row.i64("cache_creation_tokens")?,
            cost_usd: row.opt_f64("cost_usd")?,
            unpriced_api_calls: row.i64("unpriced_api_calls")?,
        })),
        Kind::Tool => Ok(Facts::Tool(ToolFacts {
            session_id: row.str("session_id")?.to_owned(),
            run_id: row.opt_str("run_id")?.map(str::to_owned),
            tool_index: row.i64("tool_index")?,
            name: row.opt_str("name")?.map(str::to_owned),
            server_side: row.bool("server_side")?,
            is_error: row.bool("is_error")?,
            incomplete: row.bool("incomplete")?,
            started_at: row.opt_timestamp("started_at")?,
            wall_ms: row.opt_i64("wall_ms")?,
            offload_file: row.opt_str("offload_file")?.map(str::to_owned),
        })),
        Kind::Compaction => Ok(Facts::Compaction(CompactionFacts {
            trigger: row.opt_str("trigger")?.map(str::to_owned),
            timestamp: row.opt_timestamp("timestamp")?,
            pre_tokens: row.opt_i64("pre_tokens")?,
            post_tokens: row.opt_i64("post_tokens")?,
            duration_ms: row.opt_i64("duration_ms")?,
        })),
        Kind::Unattributed | Kind::Unattached => Ok(Facts::Bucket(BucketFacts {
            cost_usd: node.cost_usd(),
            unpriced_api_calls: node.unpriced_api_calls,
        })),
    }
}
