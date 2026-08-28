#!/bin/sh
# Send the page archives `mise run e2e` wrote to Chromatic, which renders each one in its own
# browser and diffs it against the project's baseline. `mise run e2e-chromatic` is the way in,
# and it sends what the last sweep left — so run `mise run e2e` first.
#
# A script rather than a line in `mise.toml` because this is the one thing in the repo that
# carries a credential to a third party, and a file can be run under a stubbed `npx` — which is
# how `tests/tools/test_mise_tasks.py` proves the guard below lets a token through and hands the
# uploader the flags it should, without a byte leaving the machine.
set -eu

# This directory whatever a caller was standing in, and the repo root two above it.
cd "$(dirname "$0")"
root=$(cd ../.. && pwd)

# The npm CLI never sees `.env` — `load_dotenv` is the Python CLI's (`src/hyphae/cli.py`) — so
# read it here. Only when the variable is *unset*: an environment that names it, empty included,
# is a caller saying which token to use, and a file must not answer over them. Setting mise's
# `_.file` instead would put the OTLP ingest keys beside it into every task's environment.
if [ -z "${CHROMATIC_PROJECT_TOKEN+set}" ] && [ -f "$root/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$root/.env"
  set +a
fi

# Refuse here, before anything can reach the network: the failure a reader must never see is an
# upload that starts, sends a session's pages, and then says it was not authorized.
if [ -z "${CHROMATIC_PROJECT_TOKEN:-}" ]; then
  echo 'e2e-chromatic: CHROMATIC_PROJECT_TOKEN is missing or empty.' >&2
  echo '               Put it in .env or the environment — nothing is uploaded without it.' >&2
  exit 1
fi

# `--playwright` reads `test-results/chromatic-archives/`, which the sweep wrote.
# `--exit-zero-on-changes` reports a changed page rather than failing the job, while the
# baselines settle; tightening that later is deleting one flag. The token is read from the
# environment by the CLI itself, so it is never a word on this command line.
exec npx chromatic --playwright --exit-zero-on-changes
