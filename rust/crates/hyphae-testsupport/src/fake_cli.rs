//! The faked `claude` seam every client leaf drives: recorded envelopes in, calls out.
//!
//! The twin of `tests/enrich/fake_cli.py`. No process starts here. [`FakeCli`] implements
//! `CliRunner`, answers from the recorded envelopes in `tests/enrich/fixtures/` — read by path,
//! so both tiers pin the same recording — and records every call, which is what makes the argv,
//! the constructed env, the temp cwd and the deadline assertable.
//!
//! Only two answer envelopes were recorded, a success and the logged-out error, plus the two
//! auth blobs. Every other shape is a mutation of the success, labelled where it is built.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use hyphae_enrich::Answer;
use hyphae_enrich::client::{EnrichRequest, ITEM_TIMEOUT};
use hyphae_enrich::runner::{Call, CallError, CliRunner, Output};
use hyphae_enrich::validation::FailureKind;
use serde_json::{Map, Value};

use crate::corpus::repo;

/// The model the fake answers are attributed to, at both doors that write rows.
pub const MODEL: &str = "claude-haiku-4-5-20251001";

/// A second model id, for the leaves where agreeing with `DEFAULT_MODEL` by accident would
/// hide a client that ignored the model it was built with.
pub const OTHER_MODEL: &str = "claude-sonnet-4-5-20250929";

/// A short stand-in for the real per-level instructions: the client forwards whatever it is
/// given, so the text only has to be recognizable in an argv assertion.
pub const INSTRUCTIONS: &str = "Describe the item you are about to read.";

/// What the fake calls the `claude auth status` call, which passes no input.
pub const AUTH_CALL: &str = "<auth status>";

/// How long a gated fake waits for the call before it in the completion chain. A ceiling, not
/// a pace: a mistake in the chain fails the test instead of hanging the run.
pub const GATE_TIMEOUT: Duration = Duration::from_secs(10);

/// One recorded envelope, fresh each call so a mutation cannot leak between leaves.
///
/// # Panics
/// When the fixture is missing or is not JSON.
pub fn recorded(name: &str) -> Value {
    let path = repo()
        .join("tests/enrich/fixtures")
        .join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path).expect("the recorded envelope is readable");
    serde_json::from_str(&text).expect("the recorded envelope is JSON")
}

/// The recorded success envelope with fields replaced — a derived shape, not a recording.
pub fn mutated(changes: &[(&str, Value)]) -> Value {
    let mut envelope = recorded("envelope_success");
    let fields = envelope.as_object_mut().expect("the envelope is an object");
    for (field, value) in changes {
        fields.insert((*field).to_owned(), value.clone());
    }
    envelope
}

/// The recorded success envelope with fields removed — derived, standing for CLI drift.
pub fn without(fields: &[&str]) -> Value {
    let mut envelope = recorded("envelope_success");
    let held = envelope.as_object_mut().expect("the envelope is an object");
    for field in fields {
        held.remove(*field).expect("the envelope carried the field");
    }
    envelope
}

/// The structured output the recorded success carries, which is what the client hands back.
pub fn recorded_output() -> Map<String, Value> {
    recorded("envelope_success")["structured_output"]
        .as_object()
        .expect("the recorded answer is an object")
        .clone()
}

/// The recorded call's own usage numbers, re-keyed by a caller to stand for another model.
pub fn recorded_usage() -> Value {
    recorded("envelope_success")["modelUsage"][MODEL].clone()
}

/// What the item with this key renders to. The fake keys its script on this.
pub fn content_of(key: &str) -> String {
    format!("# Main turn\n\nrender for {key}")
}

/// One request per key, all carrying the same stand-in instructions.
pub fn requests_for(keys: &[&str]) -> Vec<EnrichRequest> {
    keys.iter()
        .map(|key| EnrichRequest {
            key: (*key).to_owned(),
            instructions: INSTRUCTIONS.to_owned(),
            content: content_of(key),
        })
        .collect()
}

/// Every answer by key: its failure kind, or None where the model answered.
pub fn kinds(answers: &[Answer]) -> BTreeMap<String, Option<FailureKind>> {
    answers
        .iter()
        .map(|answer| (answer.key().to_owned(), answer.failure()))
        .collect()
}

/// One scripted `claude` call: what it writes, or what it fails with instead.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Reply {
    pub stdout: String,
    pub code: i32,
    /// A process that never answered, as the runner reports one.
    pub fails: Option<CallError>,
}

impl Reply {
    /// A call that exited zero with this on stdout.
    pub fn printing(stdout: &str) -> Self {
        Self {
            stdout: stdout.to_owned(),
            code: 0,
            fails: None,
        }
    }

    /// A call that exited nonzero, printing whatever it printed.
    pub fn refusing(stdout: &str) -> Self {
        Self {
            stdout: stdout.to_owned(),
            code: 1,
            fails: None,
        }
    }

    /// A call that produced no process at all.
    pub fn failing(error: CallError) -> Self {
        Self {
            stdout: String::new(),
            code: 0,
            fails: Some(error),
        }
    }

    /// Whether this reply carries a usable envelope — what ends the serial canary phase.
    fn answers(&self) -> bool {
        if self.fails.is_some() || self.code != 0 {
            return false;
        }
        // Stdout the client cannot read is no more an answer than an errored call is.
        serde_json::from_str::<Value>(&self.stdout)
            .is_ok_and(|envelope| envelope.get("is_error") == Some(&Value::Bool(false)))
    }
}

/// The recorded success: an answer, as it really came back on 2026-08-13.
pub fn succeeds() -> Reply {
    Reply::printing(&recorded("envelope_success").to_string())
}

/// The recorded logged-out call: exit 1, `is_error`, no answer.
pub fn errors() -> Reply {
    Reply::refusing(&recorded("envelope_logged_out").to_string())
}

/// A hung process, killed at the item deadline.
pub fn hangs() -> Reply {
    Reply::failing(CallError::Timeout(ITEM_TIMEOUT))
}

/// Stands in for a real process: answers from a script, and records every call.
///
/// Scripted by item content, which is unique per key, so a retry of the same item gets the same
/// reply. `gate` optionally holds a call open until another call has started, which is how a
/// completion order that is not the submission order gets forced.
pub struct FakeCli {
    replies: HashMap<String, Reply>,
    gate: Option<Gate>,
    seen: Mutex<Seen>,
}

/// What holds a call open until the chain releases it.
type Gate = Box<dyn Fn(&str) + Send + Sync>;

/// What the fake watched happen, behind one lock.
#[derive(Debug, Default)]
struct Seen {
    calls: Vec<Call>,
    started: Vec<String>,
    live: usize,
    answered: usize,
    peak_before_an_answer: usize,
}

impl FakeCli {
    /// A fake answering these replies, keyed by the content each call sends.
    pub fn new(replies: HashMap<String, Reply>) -> Arc<Self> {
        Arc::new(Self {
            replies,
            gate: None,
            seen: Mutex::new(Seen::default()),
        })
    }

    /// The same, with every call held at `gate` before it is answered.
    pub fn gated(
        replies: HashMap<String, Reply>,
        gate: impl Fn(&str) + Send + Sync + 'static,
    ) -> Arc<Self> {
        Arc::new(Self {
            replies,
            gate: Some(Box::new(gate)),
            seen: Mutex::new(Seen::default()),
        })
    }

    /// Every call the fake was asked for, whole, in start order.
    pub fn calls(&self) -> Vec<Call> {
        self.seen.lock().expect("the fake's lock").calls.clone()
    }

    /// Every content the fake was asked for, in call-start order.
    pub fn started(&self) -> Vec<String> {
        self.seen.lock().expect("the fake's lock").started.clone()
    }

    /// The widest overlap seen while no call had yet returned an answer — 1 means the calls
    /// before the first answer were strictly serial.
    pub fn peak_before_an_answer(&self) -> usize {
        self.seen
            .lock()
            .expect("the fake's lock")
            .peak_before_an_answer
    }
}

impl CliRunner for FakeCli {
    fn run(&self, call: &Call) -> Result<Output, CallError> {
        let key = call.input.clone().unwrap_or_else(|| AUTH_CALL.to_owned());
        {
            let mut seen = self.seen.lock().expect("the fake's lock");
            seen.calls.push(call.clone());
            seen.started.push(key.clone());
            seen.live += 1;
            if seen.answered == 0 {
                seen.peak_before_an_answer = seen.peak_before_an_answer.max(seen.live);
            }
        }
        if let Some(gate) = &self.gate {
            gate(&key);
        }
        let reply = self
            .replies
            .get(&key)
            .unwrap_or_else(|| panic!("the fake has no reply scripted for {key:?}"))
            .clone();
        {
            let mut seen = self.seen.lock().expect("the fake's lock");
            seen.live -= 1;
            if reply.answers() {
                seen.answered += 1;
            }
        }
        match reply.fails {
            Some(error) => Err(error),
            None => Ok(Output {
                code: reply.code,
                stdout: reply.stdout,
            }),
        }
    }
}

/// Forces a completion order by holding each call until another call has started.
///
/// A call starting is the only thing a fake can see that proves the client recorded an earlier
/// answer: the client feeds one new item per answer it records. So each gated call waits for
/// the *start* of the item fed by the completion it must follow.
#[derive(Debug)]
pub struct Chain {
    waits_for: HashMap<String, String>,
    started: Mutex<HashSet<String>>,
    woken: Condvar,
}

impl Chain {
    /// A chain in which each key waits for the key it maps to.
    pub fn new(waits_for: HashMap<String, String>) -> Arc<Self> {
        Arc::new(Self {
            waits_for,
            started: Mutex::new(HashSet::new()),
            woken: Condvar::new(),
        })
    }

    /// Hold this call until the call it waits for has started.
    ///
    /// # Panics
    /// When the awaited call never starts, so a mistake in the chain fails a leaf instead of
    /// hanging the run.
    pub fn gate(&self, key: &str) {
        let mut started = self.started.lock().expect("the chain's lock");
        started.insert(key.to_owned());
        self.woken.notify_all();
        let Some(awaited) = self.waits_for.get(key) else {
            return;
        };
        let deadline = Instant::now() + GATE_TIMEOUT;
        while !started.contains(awaited) {
            let left = deadline.saturating_duration_since(Instant::now());
            assert!(!left.is_zero(), "{key:?} waited for {awaited:?} to start");
            started = self
                .woken
                .wait_timeout(started, left)
                .expect("the chain's lock")
                .0;
        }
    }
}
