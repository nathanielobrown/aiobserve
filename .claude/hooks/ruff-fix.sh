#!/usr/bin/env bash
# Claude Code PostToolUse hook: format + autofix the file Claude just edited.
#
# Wired in .claude/settings.json against Edit|Write. Reads the tool payload as
# JSON on stdin, extracts tool_input.file_path, and runs ruff on that one file.
# Bails on anything that isn't a .py file under this repo.
#
# Speed-sensitive: invoked on every edit. We call .venv/bin/ruff directly to skip
# the ~50-100ms `uv run` sync check. F401 is configured `unfixable` in
# pyproject.toml so unused imports are never silently removed here.

set -euo pipefail

# The repo this hook instance belongs to. CLAUDE_PROJECT_DIR (set by Claude Code)
# points at the active checkout; the BASH_SOURCE fallback covers direct invocation.
repo_root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

payload="$(cat)"
file_path="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')"

[[ -n "$file_path" && "$file_path" == *.py && -f "$file_path" ]] || exit 0

# Only ever format files under this repo — a subagent editing a foreign repo still
# fires this hook, and our ruff config must not rewrite foreign code. Compare
# physical paths so symlinked spellings (/tmp vs /private/tmp) can't dodge the
# prefix check; fail open (skip) if either path won't resolve.
real_root="$(cd "$repo_root" 2>/dev/null && pwd -P)" || exit 0
real_dir="$(cd "$(dirname "$file_path")" 2>/dev/null && pwd -P)" || exit 0
[[ "$real_dir/" == "$real_root"/* ]] || exit 0

ruff="$repo_root/.venv/bin/ruff"
# If the venv hasn't been created yet, no-op rather than failing the edit.
[[ -x "$ruff" ]] || exit 0

# --exit-zero on check so leftover unfixable lints (F401, etc.) don't surface as a
# hook failure mid-edit. They'll fail at `mise run check` time.
before="$(shasum -a 256 "$file_path" | cut -d' ' -f1)"

"$ruff" format "$file_path" >/dev/null
"$ruff" check --fix --exit-zero "$file_path" >/dev/null

after="$(shasum -a 256 "$file_path" | cut -d' ' -f1)"

# Exit 2 feeds stderr back to the model on a PostToolUse hook (the ruff run already
# happened; nothing is blocked) — it flags that the model's mental copy of the file
# is now stale before it attempts another Edit against it.
if [[ "$before" != "$after" ]]; then
  echo "ruff-fix hook reformatted $file_path after your edit — re-read the changed region before further edits (stale old_string will not match)." >&2
  exit 2
fi
