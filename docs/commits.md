# Commits

How to write a commit in this repo. Branch from `origin/main` (`git switch -c <name>`) and commit with plain `git`; each commit is one reviewable change. The branch flow, fixup commits, and history-rewrite recipes: [the PR guide](pull-requests.md).

## Message format

- First line: concise — lead with the *type* of change and the area or feature it touches. Add detail in the body as needed, but not too much
- Keep references (issue / PR numbers) off the first line; put them on a later body line (e.g. `Closes #12.`). The first line is for the what-and-type, not the bookkeeping.
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
- The list is closed. Two Gitmoji habits slip past it: ♻️ is 🧹 here, and 📝 is 📚

## Hygiene

- Commit as finely as you like while iterating, then shape the branch into a few well-scoped, atomic commits before review — they land on `main` exactly as reviewed (fast-forward, no squash)
- Never commit extracted session data or a backend ingest key. `.gitignore` covers `data/` and `.env`; anything you add beside them needs the same treatment.
