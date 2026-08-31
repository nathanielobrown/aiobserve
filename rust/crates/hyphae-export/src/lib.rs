//! Shipping the store's sessions to an OTLP backend: span shaping and checked delivery.
//!
//! One trace per session, ids derived from the store's own composite keys, so re-sending a
//! session lands on the spans it landed on last time. That is the whole delivery promise:
//! at-least-once with stable ids.
//!
//! Only structure ships by default. Transcript text is untrusted and POSTing it to a third
//! party publishes it, so prompts, model text, tool arguments and results stay home.
//!
//! Ported from `src/hyphae/export/otlp.py`, which stays the authority.

pub mod otlp;
