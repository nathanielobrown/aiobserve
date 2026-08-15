# Commits

How to write a commit in this repo. Branch from `origin/main` with `git switch -c <name>`, then commit with plain `git`. Make each commit one reviewable change. For branch flow, fixup commits, and history-rewrite recipes, see [the PR guide](pull-requests.md).

## Message format

- First line: concise — lead with the *type* of change and the area or feature it touches. Add detail in the body as needed, but not too much
- Keep references such as issue or PR numbers off the first line. Put them on a later body line (e.g. `Closes #12.`). Use the first line for the what-and-type, not bookkeeping
- Start every message with at least one emoji from this list:
  - ✨ new feature
  - 🌱 built but not yet hooked up (intermediate development steps)
  - 🧹 refactor or cleanup
  - 🗂️ data model changes
  - 🐛 bug fix
  - 🧪 testing (exclusively)
  - 📚 documentation
  - 🔍 observability
  - 🚀 CI/CD
  - ⬆️ dependency upgrades
  - ⚙️ configuration changes
  - 🛠️ tooling (linters, formatters, typecheckers, scripts, AI guidance, …)
  - 🔒 security
- Use only emojis from this list. Two Gitmoji habits slip past it: ♻️ is 🧹 here, and 📝 is 📚

## Hygiene

- Commit as finely as you like while iterating. Before review, shape the branch into a few well-scoped, atomic commits. They land on `main` exactly as reviewed (fast-forward, no squash)
- Never commit extracted session data or a backend ingest key. `.gitignore` covers `data/` and `.env`; give anything you add beside them the same treatment
