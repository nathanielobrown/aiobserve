//! Reading a rendered NavTree, and building the level the store says one should hold.
//!
//! The twin of `tests/view/nav_trees.py`. `data-nav-tree` carries a row's node key — `kind:id`,
//! the key its URL is built from — so a NavTree reads back as a list in document order. The
//! levels here are built out of the store the way the design orders one, in the test's own SQL:
//! turns with compactions dropped in by time, then the thread's unattributed bucket, then —
//! under the session alone — the runs nothing placed. A run hangs under the tool call that
//! spawned it, in every preset. Reading the order back out of the store rather than pinning it
//! means a re-recorded fixture moves the expectation instead of reddening the tier.
//!
//! [`Levels::cell`] is the design's kind × preset table written out — every cell in full,
//! including the ones a preset passes through, so a table edit has to be an edit here before it
//! can pass.
//!
//! Times come back as epoch microseconds rather than as instants: nothing here prints one, and
//! ordering is all a placement rule asks of them.

use std::path::Path;

use hyphae_store::{Param, Store};
use hyphae_view::nodes::{BODY_URL, KIN_URL, Kind, Preset, meter};

use crate::landmarks::{MAIN, SPINE};

/// One session's runs beside every edge the design's table has of placing one.
///
/// The spawning edge the full tree reads, plus the tool call that edge resolved through and the
/// run's own declared parent — the one edge `agents` reads that an unresolvable call cannot
/// lose.
const EDGES: &str = "SELECT a.id, c.source, st.id AS turn_id, c.id AS call_id, tc.id AS tool_id, \
     a.parent_agent_id FROM live_agent_runs a \
     LEFT JOIN live_tool_calls tc ON tc.session_id = a.session_id AND tc.id = a.tool_use_id \
      AND tc.source <> a.id \
     LEFT JOIN live_api_calls c ON c.session_id = a.session_id AND c.source = tc.source \
      AND c.id = tc.api_call_id \
     LEFT JOIN live_turns st ON st.session_id = c.session_id AND st.source = c.source \
      AND st.id = c.turn_id \
     WHERE a.session_id = $session_id ORDER BY a.started_at NULLS LAST, a.id";

/// One run and every way the design's table has of placing it.
#[derive(Debug, Clone)]
pub struct Edge {
    pub run_id: String,
    /// The thread its spawning call was made on, `None` where nothing resolved at all.
    pub spawn_source: Option<String>,
    /// The turn that call answered, `None` where the call resolved to no turn of its thread.
    pub spawn_turn_id: Option<String>,
    pub spawn_call_id: Option<String>,
    pub spawn_tool_id: Option<String>,
    pub parent_agent_id: Option<String>,
}

/// The store a NavTree expectation is built from, opened once.
pub struct Levels {
    store: Store,
}

impl Levels {
    /// Read the store the viewer is serving. Read-only, so it takes no lock the viewer wants.
    pub fn of(db: &Path) -> Self {
        Self {
            store: Store::open_read_only(db).expect("the store opens read only"),
        }
    }

    /// The store itself, for a leaf that reads a number rather than a level.
    pub fn store(&self) -> &Store {
        &self.store
    }

    /// One thread's children, as node keys, in the order the design puts them in.
    ///
    /// The session's own thread is `main`, and it is the one that also holds the unattached
    /// bucket: what makes a run unattached is that nothing says which thread spawned it, so
    /// the bucket spans every thread rather than sitting on one.
    pub fn thread_level(&self, session_id: &str, source: &str) -> Vec<String> {
        let turns = self.timed(
            "SELECT id, epoch_us(started_at) AS at FROM live_turns \
             WHERE session_id = $session_id AND source = $source ORDER BY \"index\"",
            &[("session_id", session_id.into()), ("source", source.into())],
            "turn",
        );
        // A compaction lands before the first turn that started after it, which is when it
        // happened — but only the ones that happened between two turns: one that happened
        // during a turn is a child of that turn, not a sibling of it.
        let between: Vec<(String, i64)> = self
            .marks(session_id, source)
            .into_iter()
            .filter(|(_, _, turn)| turn.is_none())
            .map(|(mark, at, _)| (mark, at))
            .collect();
        let mut placed = dropped_in(&turns, &between);
        // The thread's calls that answer no turn *of this thread*, as one bucket. A fork
        // replays calls whose `turn_id` names a turn of the thread it forked from, so the
        // resolution is a join and not a NULL check.
        let loose = self.count(
            "SELECT count(*) AS n FROM live_api_calls c \
             LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE c.session_id = $session_id AND c.source = $source AND t.id IS NULL",
            &[("session_id", session_id.into()), ("source", source.into())],
        );
        if loose > 0 {
            placed.push(format!("unattributed:{source}"));
        }
        if source == MAIN
            && self
                .edges(session_id)
                .iter()
                .any(|edge| edge.spawn_source.is_none())
        {
            placed.push(format!("unattached:{session_id}"));
        }
        placed
    }

    /// One thread's compactions in time order, each beside the turn it happened during.
    ///
    /// The placement rule in the test's own SQL: a turn holds a compaction its span covers,
    /// and where two spans cover one — the corpus records turns that overlap — the turn that
    /// started last holds it, because that is the one still running. Half-open at both ends: a
    /// compaction at the instant a turn starts is that turn's, one at the instant it ends is
    /// the next thing's.
    pub fn marks(&self, session_id: &str, source: &str) -> Vec<(String, i64, Option<String>)> {
        self.store
            .fetch(
                "SELECT k.id, epoch_us(k.timestamp) AS at, \
                   (SELECT t.id FROM live_turns t \
                      WHERE t.session_id = k.session_id AND t.source = k.source \
                        AND k.timestamp >= t.started_at AND k.timestamp < t.ended_at \
                      ORDER BY t.started_at DESC, t.\"index\" DESC LIMIT 1) AS during \
                 FROM live_compactions k \
                 WHERE k.session_id = $session_id AND k.source = $source ORDER BY k.timestamp",
                &[("session_id", session_id.into()), ("source", source.into())],
            )
            .expect("the store answers")
            .iter()
            .map(|row| {
                (
                    row.str("id").expect("a compaction id").to_owned(),
                    row.i64("at").expect("an instant"),
                    row.opt_str("during")
                        .expect("a turn or none")
                        .map(str::to_owned),
                )
            })
            .collect()
    }

    /// The api calls under one turn and the compactions among them.
    ///
    /// `turn_id` `None` is the unattributed bucket's own level, which reads the same way: the
    /// calls that answer no turn. No run is here — a run hangs off its own spawning tool call,
    /// which is two levels further down.
    pub fn turn_level(&self, session_id: &str, source: &str, turn_id: Option<&str>) -> Vec<String> {
        let calls = self.timed(
            "SELECT c.id, epoch_us(c.started_at) AS at FROM live_api_calls c \
             LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE c.session_id = $session_id AND c.source = $source \
               AND t.id IS NOT DISTINCT FROM CAST($turn_id AS VARCHAR) ORDER BY c.\"index\"",
            &[
                ("session_id", session_id.into()),
                ("source", source.into()),
                ("turn_id", turn_id.into()),
            ],
            "call",
        );
        dropped_in(&calls, &self.turn_marks(session_id, source, turn_id))
    }

    /// `noapi`'s level under a turn: its calls' tool calls and its compactions.
    ///
    /// The api calls are hidden, so their tool calls rise to the turn in call-then-tool order.
    /// A compaction hangs off the turn whichever preset the reader is in, so it drops in by
    /// time here too. A run is not here either: it hangs under the tool call that spawned it.
    pub fn tool_level(&self, session_id: &str, source: &str, turn_id: Option<&str>) -> Vec<String> {
        let tools = self.timed(
            "SELECT tc.id, epoch_us(tc.started_at) AS at FROM live_tool_calls tc \
             JOIN live_api_calls c ON c.session_id = tc.session_id AND c.source = tc.source \
              AND c.id = tc.api_call_id \
             LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE tc.session_id = $session_id AND tc.source = $source \
               AND t.id IS NOT DISTINCT FROM CAST($turn_id AS VARCHAR) \
             ORDER BY c.\"index\", tc.\"index\"",
            &[
                ("session_id", session_id.into()),
                ("source", source.into()),
                ("turn_id", turn_id.into()),
            ],
            "tool",
        );
        dropped_in(&tools, &self.turn_marks(session_id, source, turn_id))
    }

    /// The tool calls one api call made, in the order it made them.
    pub fn call_tools(&self, session_id: &str, source: &str, api_call_id: &str) -> Vec<String> {
        self.store
            .fetch(
                "SELECT id FROM live_tool_calls WHERE session_id = $session_id \
                 AND source = $source AND api_call_id = $call_id ORDER BY \"index\"",
                &[
                    ("session_id", session_id.into()),
                    ("source", source.into()),
                    ("call_id", api_call_id.into()),
                ],
            )
            .expect("the store answers")
            .iter()
            .map(|row| format!("tool:{}", row.str("id").expect("a tool call id")))
            .collect()
    }

    /// One session's runs with the edges that place them, in `view_runs`' order — by start.
    pub fn edges(&self, session_id: &str) -> Vec<Edge> {
        self.store
            .fetch(EDGES, &[("session_id", session_id.into())])
            .expect("the store answers")
            .iter()
            .map(|row| {
                let text = |column: &str| {
                    row.opt_str(column)
                        .expect("a column the join selects")
                        .map(str::to_owned)
                };
                Edge {
                    run_id: row.str("id").expect("a run id").to_owned(),
                    spawn_source: text("source"),
                    spawn_turn_id: text("turn_id"),
                    spawn_call_id: text("call_id"),
                    spawn_tool_id: text("tool_id"),
                    parent_agent_id: text("parent_agent_id"),
                }
            })
            .collect()
    }

    /// The session's runs one edge places under one node, in the order they started.
    pub fn runs_where(&self, session_id: &str, holds: impl Fn(&Edge) -> bool) -> Vec<String> {
        self.edges(session_id)
            .iter()
            .filter(|edge| holds(edge))
            .map(|edge| format!("run:{}", edge.run_id))
            .collect()
    }

    /// The runs one shut row stands, and the runs under those, as the NavTree draws them.
    ///
    /// A run is always visible: where the rows between it and its spawning call are shut, it
    /// renders under the deepest one showing. So the expectation for any row a page draws
    /// closed is the row and then this, by the spawning edge — the same edge the cells place a
    /// run by.
    pub fn hanging(&self, session_id: &str, source: &str, key: &str) -> Vec<String> {
        let (kind, node_id) = key.split_once(':').unwrap_or((key, ""));
        let spawned = match kind {
            "turn" => self.runs_where(session_id, |edge| {
                edge.spawn_source.as_deref() == Some(source)
                    && edge.spawn_turn_id.as_deref() == Some(node_id)
            }),
            "unattributed" => self.runs_where(session_id, |edge| {
                edge.spawn_source.as_deref() == Some(node_id) && edge.spawn_turn_id.is_none()
            }),
            "call" => self.runs_where(session_id, |edge| {
                edge.spawn_source.as_deref() == Some(source)
                    && edge.spawn_call_id.as_deref() == Some(node_id)
            }),
            "tool" => self.runs_where(session_id, |edge| {
                edge.spawn_source.as_deref() == Some(source)
                    && edge.spawn_tool_id.as_deref() == Some(node_id)
            }),
            // A run stands whatever its own thread spawned, which is what its turns would show.
            "run" => self.runs_where(session_id, |edge| {
                edge.spawn_source.as_deref() == Some(node_id)
            }),
            "unattached" => self.runs_where(session_id, |edge| edge.spawn_source.is_none()),
            _ => return Vec::new(),
        };
        spawned
            .into_iter()
            .flat_map(|run| {
                let under = run.trim_start_matches("run:").to_owned();
                let below = self.hanging(session_id, &under, &run);
                std::iter::once(run).chain(below)
            })
            .collect()
    }

    /// One level as a page draws it with every row of it closed: each row, then what it hides.
    pub fn shut(&self, session_id: &str, source: &str, level: &[String]) -> Vec<String> {
        level
            .iter()
            .flat_map(|row| {
                std::iter::once(row.clone()).chain(self.hanging(session_id, source, row))
            })
            .collect()
    }

    /// One cell of the design's kind × preset table, read out of the store.
    ///
    /// Every cell is spelled out rather than folded into "same as full", so a table edit is an
    /// edit here: a preset that started filtering a cell it used to pass through would have to
    /// be written down before it could pass.
    pub fn cell(
        &self,
        preset: Preset,
        kind: Kind,
        session_id: &str,
        source: &str,
        node_id: &str,
    ) -> Vec<String> {
        match (kind, preset) {
            // A thread's own children, or — under `agents` — the runs it spawned instead, with
            // the session keeping the unattached bucket that no thread holds.
            (Kind::Session, Preset::Agents) => {
                let mut placed = self.runs_where(session_id, |edge| {
                    edge.spawn_source.as_deref() == Some(MAIN)
                });
                if self
                    .edges(session_id)
                    .iter()
                    .any(|edge| edge.spawn_source.is_none())
                {
                    placed.push(format!("unattached:{session_id}"));
                }
                placed
            }
            (Kind::Session, _) => self.thread_level(session_id, MAIN),
            (Kind::Run, Preset::Agents) => self.runs_where(session_id, |edge| {
                edge.parent_agent_id.as_deref() == Some(node_id)
            }),
            (Kind::Run, _) => self.thread_level(session_id, node_id),
            // A turn and its thread's bucket hold the same three levels, one at a bound turn
            // and one at none: the api calls, those calls' tool calls, or the runs they
            // spawned.
            (Kind::Turn | Kind::Unattributed, _) => {
                let turn_id = (kind == Kind::Turn).then_some(node_id);
                match preset {
                    Preset::Agents => self.runs_where(session_id, |edge| {
                        edge.spawn_source.as_deref() == Some(source)
                            && edge.spawn_turn_id.as_deref() == turn_id
                    }),
                    Preset::NoApi => self.tool_level(session_id, source, turn_id),
                    Preset::Full => self.turn_level(session_id, source, turn_id),
                }
            }
            (Kind::Call, Preset::Agents) => self.runs_where(session_id, |edge| {
                edge.spawn_call_id.as_deref() == Some(node_id)
            }),
            (Kind::Call, _) => self.call_tools(session_id, source, node_id),
            // A tool call holds the run it spawned, in every preset: that is where a run lives
            // now, and the preset only decides how many rows stand between the two.
            (Kind::Tool, _) => self.runs_where(session_id, |edge| {
                edge.spawn_tool_id.as_deref() == Some(node_id)
            }),
            (Kind::Compaction, _) => Vec::new(),
            (Kind::Unattached, _) => {
                self.runs_where(session_id, |edge| edge.spawn_source.is_none())
            }
        }
    }

    /// Every node of one kind the corpus holds: its session, its thread, and its id.
    ///
    /// A bucket is not a row of the store, so each is enumerated by what makes one exist — a
    /// call that answers no turn of its own thread, and a run whose spawning call resolved to
    /// nothing.
    pub fn candidates(&self, kind: Kind) -> Vec<(String, String, String)> {
        let sql = match kind {
            Kind::Session | Kind::Unattached => {
                let sessions: Vec<String> = self
                    .store
                    .fetch("SELECT id FROM sessions", &[])
                    .expect("the store answers")
                    .iter()
                    .map(|row| row.str("id").expect("a session id").to_owned())
                    .collect();
                return sessions
                    .into_iter()
                    .filter(|session_id| {
                        kind == Kind::Session
                            || self
                                .edges(session_id)
                                .iter()
                                .any(|edge| edge.spawn_source.is_none())
                    })
                    .map(|session_id| (session_id.clone(), MAIN.to_owned(), session_id))
                    .collect();
            }
            Kind::Run => "SELECT session_id, id AS source, id FROM live_agent_runs",
            Kind::Turn => "SELECT session_id, source, id FROM live_turns",
            Kind::Call => "SELECT session_id, source, id FROM live_api_calls",
            Kind::Tool => "SELECT session_id, source, id FROM live_tool_calls",
            Kind::Compaction => "SELECT session_id, source, id FROM live_compactions",
            Kind::Unattributed => {
                "SELECT DISTINCT c.session_id, c.source, c.source AS id FROM live_api_calls c \
                 LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
                  AND t.id = c.turn_id \
                 WHERE t.id IS NULL"
            }
        };
        self.store
            .fetch(sql, &[])
            .expect("the store answers")
            .iter()
            .map(|row| {
                (
                    row.str("session_id").expect("a session id").to_owned(),
                    row.str("source").expect("a thread").to_owned(),
                    row.str("id").expect("a node id").to_owned(),
                )
            })
            .collect()
    }

    /// The turn these leaves select: one with more than one api call, and not its level's first.
    ///
    /// Both halves matter. Two calls under it give the level below the selection something for
    /// a cap to cut, and a turn that is not its level's first row is one a cap of a single
    /// child would drop — which is what the rescue rule exists to stop.
    pub fn open_turn(&self) -> String {
        let rows = self
            .store
            .fetch(
                "SELECT t.id FROM live_turns t WHERE t.session_id = $session_id \
                 AND t.source = $source AND t.\"index\" > 0 \
                 AND (SELECT count(*) FROM live_api_calls c WHERE c.session_id = t.session_id \
                   AND c.source = t.source AND c.turn_id = t.id) > 1 \
                 ORDER BY t.\"index\" LIMIT 1",
                &[("session_id", SPINE.into()), ("source", MAIN.into())],
            )
            .expect("the store answers");
        rows.first()
            .expect("the spine records a turn with two calls under it")
            .str("id")
            .expect("a turn id")
            .to_owned()
    }

    /// The compactions that happened during one turn, in time order.
    ///
    /// None at a bucket: a bucket holds the calls that answer no turn, and a compaction that
    /// answers none stays beside the turns of its thread.
    fn turn_marks(
        &self,
        session_id: &str,
        source: &str,
        turn_id: Option<&str>,
    ) -> Vec<(String, i64)> {
        let Some(turn_id) = turn_id else {
            return Vec::new();
        };
        self.marks(session_id, source)
            .into_iter()
            .filter(|(_, _, during)| during.as_deref() == Some(turn_id))
            .map(|(mark, at, _)| (mark, at))
            .collect()
    }

    /// What a thread's unattributed bucket gathers: its own spend, and how much went unpriced.
    pub fn standing(&self, session_id: &str, source: &str) -> (f64, i64) {
        self.spend(
            "SELECT coalesce(round(sum(c.cost_usd), 4), 0) AS spent, \
             count(*) FILTER (c.cost_usd IS NULL) AS unpriced FROM live_api_calls c \
             LEFT JOIN live_turns t ON t.session_id = c.session_id AND t.source = c.source \
              AND t.id = c.turn_id \
             WHERE c.session_id = $session_id AND c.source = $source AND t.id IS NULL",
            session_id,
            source,
        )
    }

    /// One run's own thread, which is what an unattached run brings to the bucket gathering it.
    pub fn thread_spend(&self, session_id: &str, source: &str) -> (f64, i64) {
        self.spend(
            "SELECT coalesce(round(sum(cost_usd), 4), 0) AS spent, \
             count(*) FILTER (cost_usd IS NULL) AS unpriced FROM live_api_calls \
             WHERE session_id = $session_id AND source = $source",
            session_id,
            source,
        )
    }

    /// What one session spent altogether, which every wash is a share of.
    pub fn session_spend(&self, session_id: &str) -> f64 {
        self.store
            .fetch(
                "SELECT coalesce(cost_usd, 0) AS spent FROM session_rollups WHERE session_id = $s",
                &[("s", session_id.into())],
            )
            .expect("the store answers")
            .first()
            .expect("every session has a rollup")
            .f64("spent")
            .expect("a total")
    }

    fn spend(&self, sql: &str, session_id: &str, source: &str) -> (f64, i64) {
        let rows = self
            .store
            .fetch(
                sql,
                &[("session_id", session_id.into()), ("source", source.into())],
            )
            .expect("the store answers");
        let row = rows.first().expect("an aggregate answers one row");
        (
            row.f64("spent").expect("a total"),
            row.i64("unpriced").expect("a count"),
        )
    }

    /// One `id, at` query read as keys of `kind` beside the instant each started.
    fn timed(&self, sql: &str, params: &[(&str, Param)], kind: &str) -> Vec<(String, Option<i64>)> {
        self.store
            .fetch(sql, params)
            .expect("the store answers")
            .iter()
            .map(|row| {
                (
                    format!("{kind}:{}", row.str("id").expect("a node id")),
                    row.opt_i64("at").expect("an instant or none"),
                )
            })
            .collect()
    }

    /// The one number a counting query answers with.
    fn count(&self, sql: &str, params: &[(&str, Param)]) -> i64 {
        self.store
            .fetch(sql, params)
            .expect("the store answers")
            .first()
            .expect("a count query answers one row")
            .i64("n")
            .expect("a count")
    }
}

/// A level's own rows with `pending`'s compactions dropped in where they happened.
fn dropped_in(rows: &[(String, Option<i64>)], pending: &[(String, i64)]) -> Vec<String> {
    let mut placed = Vec::new();
    let mut waiting = pending.iter();
    let mut next = waiting.next();
    for (key, started) in rows {
        while let (Some((mark, at)), Some(started)) = (next, started) {
            if *at >= *started {
                break;
            }
            placed.push(format!("compaction:{mark}"));
            next = waiting.next();
        }
        placed.push(key.clone());
    }
    placed.extend(
        next.into_iter()
            .chain(waiting)
            .map(|(mark, _)| format!("compaction:{mark}")),
    );
    placed
}

/// The node URL of one turn of `SPINE`'s main thread.
pub fn url(turn_id: &str) -> String {
    format!("/session/{SPINE}/thread/{MAIN}/turn/{turn_id}")
}

/// Where one node's page is. A kind's word is its URL segment, so the kind is the shape.
pub fn node_url(kind: Kind, session_id: &str, source: &str, node_id: &str) -> String {
    match kind {
        Kind::Session => format!("/session/{session_id}"),
        Kind::Run => format!("/session/{session_id}/run/{node_id}"),
        Kind::Unattached => format!("/session/{session_id}/unattached"),
        Kind::Unattributed => format!("/session/{session_id}/thread/{source}/unattributed"),
        _ => format!(
            "/session/{session_id}/thread/{source}/{}/{node_id}",
            kind.word()
        ),
    }
}

/// Every expansion a page's log rows mount, unescaped — the markup carries `&` as `&amp;`.
pub fn mounts(page: &crate::html::Markup) -> Vec<String> {
    fetched(page, BODY_URL)
}

/// Every level a page's tail rows fetch the rest of, unescaped.
pub fn spilled(page: &crate::html::Markup) -> Vec<String> {
    fetched(page, KIN_URL)
}

fn fetched(page: &crate::html::Markup, under: &str) -> Vec<String> {
    page.values("hx-get")
        .into_iter()
        .filter(|href| href.starts_with(under))
        .map(|href| html_escape::decode_html_entities(&href).into_owned())
        .collect()
}

/// Whether a link goes to a node page — the records browser and an offload file do not.
pub fn node_link(href: &str) -> bool {
    let path: Vec<&str> = href
        .split('?')
        .next()
        .expect("a split yields one piece")
        .trim_matches('/')
        .split('/')
        .collect();
    if path.first() != Some(&"session") {
        return false;
    }
    // Past the session, and past the thread where the node was recorded on one, a node's path
    // says its kind. Everything else the session holds is named by something that is not one.
    let rest = if path.get(2) == Some(&"thread") {
        &path[4..]
    } else {
        &path[2..]
    };
    rest.first()
        .is_none_or(|word| Kind::spelled(word).is_some())
}

/// One row's own half read against what the store holds on its thread, and what went unpriced.
///
/// The subtree half is the rollup's, and the badge leaves weigh it: what this holds is the
/// number a row has always printed first.
pub fn weighed(
    page: &crate::html::Markup,
    key: &str,
    levels: &Levels,
    session_id: &str,
    cost: f64,
    unpriced: i64,
) {
    let whole = levels.session_spend(session_id);
    let badges = page.badges(key);
    let own = badges
        .get("cost_usd")
        .unwrap_or_else(|| panic!("{key} draws a cost badge"));
    assert_eq!(own.shown, crate::html::money(cost), "{key}");
    // The wash is that spend against the session, not against the row's parent or its own
    // children — and a session with nothing to take a share of draws every row at nothing. It
    // rides on the value it washes rather than on the row, because the row draws two of them.
    let share = (whole != 0.0).then_some(cost / whole);
    assert!(
        own.step.split_whitespace().any(|step| step == meter(share)),
        "{key}: {}",
        own.step
    );
    // A `title` inside the row is the mark on a total our price table could not complete —
    // there where some call under the row went unpriced, and nowhere else.
    let marks = page.inside("data-nav-tree", key, "title");
    assert_eq!(!marks.is_empty(), unpriced != 0, "{key}");
    assert!(
        unpriced == 0 || marks[0].contains(&unpriced.to_string()),
        "{key}: {marks:?}"
    );
}
