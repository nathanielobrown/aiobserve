//! The seam between the enricher and the model, and the one client behind it.
//!
//! Ported from `src/hyphae/enrich/client.py`. Everything above [`CliClient`] is pure and
//! testable without a process: the enricher renders, hands over a round, and reads back one
//! answer per key. Below it, a round runs `claude -p` once per item through [`CliRunner`] —
//! the subscription authenticates only through the CLI's OAuth, so that is the transport a
//! corpus pass really runs on.
//!
//! Two properties shape the client:
//!
//! - **A round never raises once it is spending.** The enricher upserts only after `submit`
//!   returns, so an error mid-round forfeits every item already paid for. The one exception is
//!   the round's first item, run alone as a canary: envelope drift ends the run there, for the
//!   price of one item, and is a `Failed(drift)` everywhere after. An interrupt is no
//!   exception — it ends the round, not the answers, and stops the run at the next round
//! - **The child process cannot act on what it reads.** Renders carry untrusted transcript
//!   text, so tools, settings, MCP and slash commands are all switched off, the environment is
//!   constructed rather than inherited, and the cwd is a temp directory

use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::time::Duration;

use serde_json::{Map, Value};

use crate::prompts::OUTPUT_SCHEMA;
use crate::runner::{Call, CallError, CliRunner};
use crate::validation::FailureKind;

/// Cheap enough to enrich the whole corpus, and the classification is a short judgement over
/// text a bigger model would not read differently.
pub const DEFAULT_MODEL: &str = "claude-haiku-4-5-20251001";

/// Resolved on PATH rather than pinned: the CLI updates itself, and the env below carries the
/// PATH the parent was launched with.
pub const CLAUDE: &str = "claude";

/// Four `claude` processes for the length of a corpus pass, against a 5-hour allowance this
/// project's own agents share. `--limit` is the pacing lever, and it is manual.
pub const DEFAULT_CONCURRENCY: usize = 4;

/// ~19x the worst wall time probed (2026-08-13). It fires on a hung process, not a slow one.
pub const ITEM_TIMEOUT: Duration = Duration::from_secs(300);

/// `auth status` makes no model call, so anything but an immediate answer is a broken CLI.
pub const AUTH_TIMEOUT: Duration = Duration::from_secs(30);

/// One immediate retry, which is what absorbs a transient CLI failure. A second identical
/// send cannot improve a bad answer, so only the shapes below are resent.
pub const ATTEMPTS: usize = 2;

/// Five failures in a row is a run that has stopped working — logged out mid-round, allowance
/// gone, a timeout grind. The kinds do not matter; the consecutiveness does.
pub const BREAKER_BOUND: usize = 5;

/// Failures the transport might not repeat. These are also the failures that saw no envelope
/// at all, which is what makes a canary that hits one inconclusive.
pub const TRANSPORT_FAILURES: [FailureKind; 2] = [FailureKind::ApiError, FailureKind::Timeout];

/// The envelope fields this client reads, pinned at claude 2.1.221 and recorded in
/// `tests/enrich/fixtures/`. `structured_output` is deliberately not among them: the CLI omits
/// it whenever the model produced nothing conforming, which is a bad answer, not drift.
pub const CONTRACT_FIELDS: [&str; 3] = ["is_error", "stop_reason", "modelUsage"];

/// One item to describe.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnrichRequest {
    /// The item's key, echoed back on the answer — a round answers in completion order.
    pub key: String,
    pub instructions: String,
    pub content: String,
}

/// What one request came back as. Carries no model output when it failed, by construction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Answer {
    /// The model answered. The output is unvalidated: [`crate::validate`] is next.
    Succeeded {
        key: String,
        output: Map<String, Value>,
    },
    /// The request produced no answer, and why.
    Failed { key: String, kind: FailureKind },
}

impl Answer {
    /// The key of the request this answers.
    pub fn key(&self) -> &str {
        match self {
            Self::Succeeded { key, .. } | Self::Failed { key, .. } => key,
        }
    }

    /// How it failed, or None where the model answered.
    pub fn failure(&self) -> Option<FailureKind> {
        match self {
            Self::Succeeded { .. } => None,
            Self::Failed { kind, .. } => Some(*kind),
        }
    }

    fn retriable(&self) -> bool {
        self.failure()
            .is_some_and(|kind| TRANSPORT_FAILURES.contains(&kind))
    }
}

/// Runs one round of requests to completion, whatever "completion" costs.
///
/// `submit` returns exactly one answer per request, in any order, and errors only when the
/// whole round failed — a single item's failure comes back as [`Answer::Failed`]. The enricher
/// is written to this, not to [`CliClient`], which is what lets a test drive a round with a
/// fake.
pub trait BatchClient {
    /// The model the answers were produced by, which is part of what makes a row stale.
    fn model(&self) -> &str;

    fn submit(&self, requests: &[EnrichRequest]) -> Result<Vec<Answer>, RoundError>;
}

/// Why a whole round ended without answering.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum RoundError {
    /// The CLI answered in a shape this client is not pinned to.
    ///
    /// Claude Code owns the envelope and changes it without notice, so a run that kept going
    /// would be writing rows out of an answer nobody has read. Raised only from a round's
    /// canary, where the crash costs one item.
    #[error("{0}")]
    Drift(String),
    /// The operator stopped the run during the previous round.
    #[error("interrupted during the previous round")]
    Interrupted,
}

/// A concurrency no pool can honour.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
#[error("concurrency must be at least 1, not {0}")]
pub struct NarrowPool(pub usize);

/// The operator's stop, as this port can see one.
///
/// Python's client catches `KeyboardInterrupt` where the signal lands; nothing delivers a
/// signal into a Rust thread, so the flag a handler would set is the seam instead. Setting it
/// ends the round the way a tripped breaker ends it — paid answers back, the rest `aborted`.
#[derive(Debug, Clone, Default)]
pub struct Interrupt(Arc<AtomicBool>);

impl Interrupt {
    /// Ask the running round to stop. Nothing further is sent; what is in flight is waited out.
    pub fn stop(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    fn asked(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

/// The round's one circuit breaker: consecutive failures, counted as answers land.
///
/// One counter for the whole round rather than one per worker, advanced by the single thread
/// that collects answers — so it counts in completion order, which is the order the run is
/// really failing in.
#[derive(Debug, Default)]
struct Breaker {
    consecutive: usize,
}

impl Breaker {
    fn record(&mut self, answer: &Answer) {
        self.consecutive = match answer {
            Answer::Succeeded { .. } => 0,
            Answer::Failed { .. } => self.consecutive + 1,
        };
    }

    fn tripped(&self) -> bool {
        self.consecutive >= BREAKER_BOUND
    }
}

/// The environment every `claude` subprocess runs under — constructed, never inherited.
///
/// One definition, shared by [`preflight`] and the items: an auth question asked in a different
/// process shape than the spend would pass while every item failed.
///
/// # Panics
/// When `USER`, `HOME` or `PATH` is unset — without them the CLI reports itself logged out,
/// which is a broken machine rather than a failed item.
pub fn build_env() -> BTreeMap<String, String> {
    let mut env = BTreeMap::new();
    for name in ["USER", "HOME", "PATH"] {
        // OAuth lives in the keychain, and without `USER` the CLI reports itself logged out.
        let value = std::env::var(name).unwrap_or_else(|_| panic!("{name} is set"));
        env.insert(name.to_owned(), value);
    }
    // Thinking off: 1,168 output tokens for a 40-token answer became 142 in the
    // 2026-08-13 probes. Env rather than settings, which `--setting-sources ""` drops.
    env.insert("MAX_THINKING_TOKENS".to_owned(), "0".to_owned());
    // A stray ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL is absent by construction: it would
    // divert auth off the subscription with no signal.
    env
}

/// Why a run must not start. The message names the problem and nothing from the answer.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct Unusable(String);

/// Refuse the run now if the CLI cannot spend the subscription.
///
/// Runs `claude auth status` under [`build_env`], so what it validates is the process shape the
/// items spend under. Echoes nothing from the answer: it carries an email, an org id and an
/// org name.
pub fn preflight(runner: &dyn CliRunner) -> Result<(), Unusable> {
    let done = runner
        .run(&Call {
            argv: vec![CLAUDE.to_owned(), "auth".to_owned(), "status".to_owned()],
            input: None,
            env: build_env(),
            cwd: None,
            timeout: AUTH_TIMEOUT,
        })
        .map_err(|error| match error {
            CallError::NotFound => Unusable(format!(
                "no {CLAUDE} on PATH: enrichment runs through the Claude Code CLI"
            )),
            CallError::Timeout(waited) => Unusable(format!(
                "`{CLAUDE} auth status` said nothing in {}s",
                waited.as_secs()
            )),
            CallError::Os(reason) => Unusable(format!(
                "`{CLAUDE} auth status` could not be started: {reason}"
            )),
        })?;
    let status: Value = serde_json::from_str(&done.stdout).map_err(|_| {
        Unusable(format!(
            "`{CLAUDE} auth status` answered with something other than JSON"
        ))
    })?;
    // Absence is the answer: the recorded logged-out blob carries neither field.
    if status.get("loggedIn") != Some(&Value::Bool(true)) {
        return Err(Unusable(format!(
            "the Claude Code CLI is logged out — run `{CLAUDE}`, log in, and rerun"
        )));
    }
    if status
        .get("subscriptionType")
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
    {
        return Err(Unusable(
            "the Claude Code CLI is logged in with no subscription behind it, which is what \
             enrichment spends"
                .to_owned(),
        ));
    }
    Ok(())
}

/// One envelope as this client reads it, or the drift message if it is not that shape.
///
/// Reads four fields and ignores everything else the CLI writes, so a new field is not drift.
fn read_answer(key: &str, stdout: &str, model: &str) -> Result<Answer, String> {
    let envelope: Value = serde_json::from_str(stdout)
        .map_err(|_| "`--output-format json` wrote something that is not JSON".to_owned())?;
    let missing: Vec<&str> = CONTRACT_FIELDS
        .into_iter()
        .filter(|field| envelope.get(field).is_none())
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "the answer envelope carries no {}",
            missing.join(", ")
        ));
    }
    if envelope["is_error"] != Value::Bool(false) {
        // An errored call carries no answer, and no usage to check one against.
        return Ok(failed(key, FailureKind::ApiError));
    }
    let named: Vec<&str> = envelope["modelUsage"]
        .as_object()
        .map(|usage| usage.keys().map(String::as_str).collect())
        .unwrap_or_default();
    if named != [model] {
        return Err(format!(
            "{model} was asked for and modelUsage names {named:?} — a substituted model would \
             mislabel every row the round wrote"
        ));
    }
    // Absent whenever the model produced nothing conforming, which the recorded logged-out
    // envelope shows the CLI doing. A bad answer, not a changed envelope.
    let output = envelope.get("structured_output").and_then(Value::as_object);
    match output {
        Some(held) if envelope["stop_reason"] != "max_tokens" => Ok(Answer::Succeeded {
            key: key.to_owned(),
            output: held.clone(),
        }),
        _ => Ok(failed(key, FailureKind::InvalidOutput)),
    }
}

fn failed(key: &str, kind: FailureKind) -> Answer {
    Answer::Failed {
        key: key.to_owned(),
        kind,
    }
}

/// One enrichment round through `claude -p`, one call per item over a pool of threads.
///
/// `submit` runs the round's first item alone as a canary, fans the rest out, and returns
/// whatever it has — including after the breaker or an [`Interrupt`] ended the round early,
/// when everything with no answer to its name comes back as `Failed(aborted)`. It errors only
/// from the canary, and at the start of a round that follows an interrupted one.
pub struct CliClient {
    model: String,
    concurrency: usize,
    runner: Box<dyn CliRunner>,
    interrupt: Interrupt,
    /// Set when a round was stopped, and read at the top of the next one. See `submit`.
    stopped: AtomicBool,
}

impl CliClient {
    /// # Errors
    /// When `concurrency` is zero — refused here rather than by the pool, which would raise
    /// one paid item in. A negative width cannot be spelled, as it can in Python.
    pub fn new(
        model: &str,
        concurrency: usize,
        runner: Box<dyn CliRunner>,
    ) -> Result<Self, NarrowPool> {
        if concurrency < 1 {
            return Err(NarrowPool(concurrency));
        }
        Ok(Self {
            model: model.to_owned(),
            concurrency,
            runner,
            interrupt: Interrupt::default(),
            stopped: AtomicBool::new(false),
        })
    }

    /// The handle a Ctrl-C sets to end the round the way a tripped breaker ends it.
    pub fn interrupt(&self) -> Interrupt {
        self.interrupt.clone()
    }

    fn canary(
        &self,
        pending: &mut VecDeque<EnrichRequest>,
        answers: &mut Vec<Answer>,
        breaker: &mut Breaker,
        cwd: &std::path::Path,
    ) -> Result<(), RoundError> {
        // Run items one at a time until one produces an envelope, or the breaker gives up. A
        // canary that errored or timed out validated nothing, so the next item re-canaries
        // rather than opening the pool onto a contract no answer has confirmed.
        while let Some(request) = pending.front() {
            if breaker.tripped() || self.interrupt.asked() {
                return Ok(());
            }
            let request = request.clone();
            pending.pop_front();
            let answer = self.one(&request, cwd, true)?;
            let inconclusive = answer.retriable();
            breaker.record(&answer);
            answers.push(answer);
            if !inconclusive {
                return Ok(());
            }
        }
        Ok(())
    }

    fn fan_out(
        &self,
        pending: &mut VecDeque<EnrichRequest>,
        answers: &mut Vec<Answer>,
        breaker: &mut Breaker,
        cwd: &std::path::Path,
    ) {
        // Fed rather than submitted all at once: on a trip nothing further starts, so the
        // remainder is exactly the work nobody paid for. Items already running are collected —
        // their spend has landed either way.
        std::thread::scope(|scope| {
            let (send, receive) = mpsc::channel();
            let mut in_flight = 0usize;
            loop {
                while in_flight < self.concurrency
                    && !breaker.tripped()
                    && !self.interrupt.asked()
                    && let Some(request) = pending.pop_front()
                {
                    let send = send.clone();
                    scope.spawn(move || {
                        // Past the canary, drift fails its own item, so no worker can error.
                        let answer = self
                            .one(&request, cwd, false)
                            .expect("only a canary ends a round");
                        let _ = send.send(answer);
                    });
                    in_flight += 1;
                }
                if in_flight == 0 {
                    return;
                }
                // One at a time, so the breaker advances in the order answers land.
                let answer = receive.recv().expect("a worker answers exactly once");
                in_flight -= 1;
                breaker.record(&answer);
                answers.push(answer);
            }
        });
    }

    /// One item, sent again only when the transport rather than the model was what failed.
    fn one(
        &self,
        request: &EnrichRequest,
        cwd: &std::path::Path,
        canary: bool,
    ) -> Result<Answer, RoundError> {
        let mut answer = self.attempt(request, cwd, canary)?;
        for _ in 1..ATTEMPTS {
            if !answer.retriable() {
                break;
            }
            answer = self.attempt(request, cwd, canary)?;
        }
        Ok(answer)
    }

    /// One `claude -p` call: the render over stdin, the answer or a failure back.
    fn attempt(
        &self,
        request: &EnrichRequest,
        cwd: &std::path::Path,
        canary: bool,
    ) -> Result<Answer, RoundError> {
        let done = match self.runner.run(&self.call(request, cwd)) {
            Ok(done) => done,
            Err(CallError::Timeout(_)) => return Ok(failed(&request.key, FailureKind::Timeout)),
            // The runner's own failure set: no file descriptor, no memory to fork with, no
            // binary. One item's failure while the round is spending, not the round's — five
            // in a row trip the breaker, which is the shape a broken machine takes here.
            Err(_) => return Ok(failed(&request.key, FailureKind::ApiError)),
        };
        if done.code != 0 {
            // Where the CLI's own refusals land — a logged-out call exits 1.
            return Ok(failed(&request.key, FailureKind::ApiError));
        }
        read_answer(&request.key, &done.stdout, &self.model).or_else(|drift| {
            if canary {
                return Err(RoundError::Drift(drift));
            }
            // Past the canary the round is spending, and an error here would forfeit every
            // answer already paid for. The crash summary names the kind instead.
            Ok(failed(&request.key, FailureKind::Drift))
        })
    }

    /// The one call shape every item takes: no tools, no settings, no MCP, no session.
    fn call(&self, request: &EnrichRequest, cwd: &std::path::Path) -> Call {
        let argv = [
            CLAUDE,
            "--print",
            "--output-format",
            "json",
            "--model",
            &self.model,
            // Replacement, not append: the default scaffold costs ~18.8K tokens an item.
            "--system-prompt",
            &request.instructions,
            "--json-schema",
            &serde_json::to_string(&*OUTPUT_SCHEMA).expect("the output schema serializes"),
            // The render is untrusted transcript text, so nothing it says can reach a tool, a
            // settings file, an MCP server or a slash command.
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            // A render full of private transcript text is never written under `~/.claude`.
            "--no-session-persistence",
        ];
        Call {
            argv: argv.into_iter().map(str::to_owned).collect(),
            input: Some(request.content.clone()),
            env: build_env(),
            cwd: Some(cwd.to_owned()),
            timeout: ITEM_TIMEOUT,
        }
    }
}

impl BatchClient for CliClient {
    fn model(&self) -> &str {
        &self.model
    }

    fn submit(&self, requests: &[EnrichRequest]) -> Result<Vec<Answer>, RoundError> {
        // Where an interrupt is finally delivered: the round it landed in has been written by
        // now, and starting another would spend against an operator who asked to stop.
        if self.stopped.load(Ordering::SeqCst) {
            return Err(RoundError::Interrupted);
        }
        let mut answers: Vec<Answer> = Vec::new();
        let mut breaker = Breaker::default();
        // Consumed from the front by both phases below; whatever is left was never sent.
        let mut pending: VecDeque<EnrichRequest> = requests.iter().cloned().collect();
        // `sessions.py` keys the projects directory on the cwd, so running here is what keeps
        // any session the CLI still writes out of every extractable project.
        let cwd = tempfile::Builder::new()
            .prefix("hyphae-enrich-")
            .tempdir()
            .expect("a temp directory to run the calls in");
        let outcome = self.canary(&mut pending, &mut answers, &mut breaker, cwd.path());
        if !pending.is_empty() && !breaker.tripped() && outcome.is_ok() {
            self.fan_out(&mut pending, &mut answers, &mut breaker, cwd.path());
        }
        if self.interrupt.asked() {
            // The round ends the way a tripped breaker ends it — paid answers back, the rest
            // `aborted` — and the run stops at the next round instead. An error here would
            // throw away everything the round had already bought.
            self.stopped.store(true, Ordering::SeqCst);
        }
        outcome?;
        // One answer per request, whatever ended the round: the enricher pairs them by key.
        let answered: Vec<&str> = answers.iter().map(Answer::key).collect();
        let aborted: Vec<Answer> = requests
            .iter()
            .filter(|request| !answered.contains(&request.key.as_str()))
            .map(|request| failed(&request.key, FailureKind::Aborted))
            .collect();
        answers.extend(aborted);
        Ok(answers)
    }
}
