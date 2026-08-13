# Recorded Claude Code CLI envelopes

What `claude` really writes, so the client is tested against the CLI's schema rather than a
remembered one. All four were captured on 2026-08-13 from **claude 2.1.221** on macOS, under the
constructed env the client builds (`HOME`, `PATH`, `USER`, `MAX_THINKING_TOKENS=0`).

| File | Produced by |
| --- | --- |
| `envelope_success.json` | one `claude -p` item call with the client's full flag set, over an invented one-turn render |
| `envelope_logged_out.json` | the same call with `USER` dropped from the env, which logs the CLI out — exit 1, `is_error: true`, no `structured_output`, empty `modelUsage` |
| `auth_status_logged_in.json` | `claude auth status`, exit 0 |
| `auth_status_logged_out.json` | `claude auth status` with `USER` dropped, exit 1 |

The prompt content behind `envelope_success.json` was invented — there is no recorded session whose
model answer we could draw on — so its `structured_output` is a real model answer about invented
work. The *envelope* around it is what these files are evidence of.

`auth_status_logged_in.json` has `email`, `orgId` and `orgName` replaced with `REDACTED-…-9f2c`
placeholders. The placeholders are load-bearing: `test_preflight_never_prints_the_auth_blob`
searches the output for them.

Error envelopes other than the logged-out one are **mutations** of `envelope_success.json`, built in
`tests/enrich/test_client.py` and labelled there as derived.
