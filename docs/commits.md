# Commits

Each commit is a review unit. Keep it to one change and write a subject a reviewer can understand without opening the diff. See [the PR guide](pull-requests.md) for branches, fixups, rebases, and landing.

## Make each commit one change

Commit as often as you need while working. Before review, shape the branch into a few atomic commits. The reviewed commits land on `main` unchanged, without a squash.

Never commit extracted session data or a backend ingest key. `.gitignore` covers `data/` and `.env`; ignore any related files you add.

## Say what changed in the subject

Use this form:

```
<emoji> <plain statement of the change>
```

Choose an emoji for the type of change, then name the area or feature it touches. Keep the subject short. Add a body when the reviewer needs more context.

Keep issue and PR numbers out of the subject. Put them on a later body line instead:

```
Closes #12.
```

## Choose emojis from this list

Start each subject with at least one emoji from this table. Don't use emojis outside the list.

| Emoji | Change |
| --- | --- |
| ✨ | New feature |
| 🌱 | Intermediate work that is built but not yet wired in |
| 🧹 | Refactor or cleanup |
| ⚡ | Performance: the same behavior for less time or memory |
| 🗂️ | Data model |
| 🐛 | Bug fix |
| 🧪 | Tests only |
| 📚 | Documentation |
| 🔍 | Observability |
| 🚀 | CI/CD |
| ⬆️ | Dependency upgrade |
| ⚙️ | Configuration |
| 🛠️ | Tooling, including linters, formatters, type checkers, scripts, and AI guidance |
| 🔒 | Security |

Two common Gitmoji choices don't apply here: use 🧹 instead of ♻️, and 📚 instead of 📝.
