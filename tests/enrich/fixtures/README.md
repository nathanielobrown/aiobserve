# Recorded Claude Code CLI envelopes

What `claude` really writes, so the client is tested against the CLI's schema rather than a
remembered one. The four envelopes were captured on 2026-08-13 from **claude 2.1.221** on macOS, under the
constructed env the client builds (`HOME`, `PATH`, `USER`, `MAX_THINKING_TOKENS=0`).

| File | Produced by |
| --- | --- |
| `envelope_success.json` | one `claude -p` item call with the client's full flag set, over an invented one-turn render |
| `envelope_logged_out.json` | the same call with `USER` dropped from the env, which logs the CLI out — exit 1, `is_error: true`, no `structured_output`, empty `modelUsage` |
| `auth_status_logged_in.json` | `claude auth status`, exit 0 |
| `auth_status_logged_out.json` | `claude auth status` with `USER` dropped, exit 1 |
| `stderr_unknown_option.txt` | what the CLI wrote on stderr for a flag it does not take (`claude --print --no-such-flag hi`), exit 1 — captured 2026-08-30 from the same 2.1.221 |

The prompt content behind `envelope_success.json` was invented — there is no recorded session whose
model answer we could draw on — so its `structured_output` is a real model answer about invented
work. The *envelope* around it is what these files are evidence of.

`auth_status_logged_in.json` has `email`, `orgId` and `orgName` replaced with `REDACTED-…-9f2c`
placeholders. The placeholders are load-bearing: `test_preflight_never_prints_the_auth_blob`
searches the output for them.

A flag the installed CLI no longer takes is the refusal that fails every item of a round
identically, which is why its stderr is recorded here. A mistyped `--model` is not: the CLI answers
anyway, from a model of its own choosing (probed on 2.1.221, exit 0).

Error envelopes other than the logged-out one are **mutations** of `envelope_success.json`, built in
`tests/enrich/fake_cli.py` and labelled there as derived.
